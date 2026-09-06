"""LLM rubric grading for a stratified sample of prompts.

The regex features in `rubric` see surface form only. The paper's strongest
prompt-side signal came from a human expert applying a rubric (r = .479 with
performance, against r = .31-.34 for lexical diversity), so a judgment layer
is worth the money. This grades a sample rather than the corpus: the point is
a calibrated read on the distribution, not a score attached to every turn.

The backend is the `claude` CLI, which avoids API key handling entirely. MCP
servers are switched off for the grading calls; they add ~30k tokens of tool
definitions per call and nothing to the grading.

Results are cached by prompt hash, so re-running only pays for new prompts.
"""

import hashlib
import json
import os
import subprocess
import tempfile

from .rubric import SCORED_KINDS

RUBRIC_AXES = ("coherence", "complexity", "clarity")

_SYSTEM = """당신은 소프트웨어 요구사항 엔지니어링 전문가이자 LLM 프롬프트 분석가입니다.
아래는 한 개발자가 코딩 에이전트(Claude Code)에게 실제로 입력한 프롬프트들입니다.
각 프롬프트를 '에이전트에게 내리는 지시'로서 평가하십시오.

평가 축 (각 0~10 정수):
- coherence: 하나의 프롬프트 안에서 요구가 논리적으로 일관되고 서로 모순되지 않는가.
- complexity: 과제 규모에 맞는 정보량인가. 너무 빈약하거나 불필요하게 장황하지 않은가.
- clarity: 지시가 명확한가. 지시 대상·동작·완료 조건이 해석의 여지 없이 특정되는가.

평가 원칙:
- 이전 대화 맥락은 주어지지 않는다. 맥락 없이 읽히지 않는다는 이유만으로 감점하지 말고,
  '이 문장이 그 맥락 안에서 얼마나 정확히 특정하는가'를 보라.
- 한국어의 정중한 의문형("~해줄 수 있어?")은 지시이지 질문이 아니다. 어투는 감점 사유가 아니다.
- 짧다는 이유만으로 감점하지 말라. 짧아도 대상과 동작이 특정되면 clarity는 높다.

각 프롬프트마다 다음을 출력하십시오:
- id: 입력에 주어진 id 그대로
- coherence, complexity, clarity: 0~10 정수
- issue: 가장 큰 문제 한 가지를 한 문장으로. 문제가 없으면 빈 문자열.
- rewrite: 문제가 있을 때만, 같은 의도를 유지한 개선 프롬프트. 없으면 빈 문자열.

출력은 JSON 배열 하나만. 설명·머리말·코드펜스 없이 배열만 출력하십시오."""


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def stratified_sample(records, limit, seed=0):
    """Spread the sample across the score range instead of taking the top n.

    A sample drawn at random is dominated by the middle of the distribution;
    grading needs the tails to say anything useful about them.
    """
    import random

    # Prompts whose referent is a pasted image cannot be graded fairly from
    # the text alone, so they stay out of the sample.
    pool = [r for r in records
            if r["kind"] in SCORED_KINDS and "scores" in r
            and not r.get("has_attachment")]
    if len(pool) <= limit:
        return pool

    pool.sort(key=lambda r: r["scores"]["total"])
    bins, per_bin = 10, max(limit // 10, 1)
    size = len(pool) / bins
    rng = random.Random(seed)

    picked = []
    for i in range(bins):
        chunk = pool[int(i * size): int((i + 1) * size)]
        picked.extend(rng.sample(chunk, min(per_bin, len(chunk))))

    chosen = {id(r) for r in picked}
    remaining = [r for r in pool if id(r) not in chosen]
    if len(picked) < limit and remaining:
        picked.extend(rng.sample(remaining, min(limit - len(picked), len(remaining))))
    return picked[:limit]


def _call_claude(payload, model, timeout):
    """One grading call. MCP off, prompt on stdin, JSON out."""
    with tempfile.TemporaryDirectory() as tmp:
        mcp_path = os.path.join(tmp, "empty-mcp.json")
        with open(mcp_path, "w") as handle:
            json.dump({"mcpServers": {}}, handle)

        proc = subprocess.run(
            ["claude", "-p", "--output-format", "json", "--model", model,
             "--strict-mcp-config", "--mcp-config", mcp_path],
            input=payload, capture_output=True, text=True, timeout=timeout,
        )
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {proc.stderr[:400]}")

    envelope = json.loads(proc.stdout)
    body = (envelope.get("result") or "").strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(body), envelope.get("total_cost_usd", 0.0)


def grade(records, cache_path, limit=300, batch_size=12,
          model="claude-sonnet-5", timeout=300, progress=None):
    """Grade a stratified sample, reusing anything already in the cache."""
    cache = {}
    if os.path.exists(cache_path):
        with open(cache_path) as handle:
            cache = json.load(handle)

    sample = stratified_sample(records, limit)
    todo = [r for r in sample if _digest(r["text"]) not in cache]

    spent = 0.0
    for start in range(0, len(todo), batch_size):
        batch = todo[start: start + batch_size]
        items = [{"id": i, "prompt": r["text"][:1800]} for i, r in enumerate(batch)]
        payload = f"{_SYSTEM}\n\n입력:\n{json.dumps(items, ensure_ascii=False, indent=1)}"

        try:
            graded, cost = _call_claude(payload, model, timeout)
        except Exception as exc:  # keep partial progress on failure
            if progress:
                progress(f"batch {start // batch_size + 1} failed: {exc}")
            continue

        spent += cost
        by_id = {g.get("id"): g for g in graded if isinstance(g, dict)}
        for i, rec in enumerate(batch):
            row = by_id.get(i)
            if not row:
                continue
            cache[_digest(rec["text"])] = {
                axis: row.get(axis) for axis in RUBRIC_AXES
            } | {"issue": row.get("issue", ""), "rewrite": row.get("rewrite", "")}

        with open(cache_path, "w") as handle:
            json.dump(cache, handle, ensure_ascii=False, indent=1)
        if progress:
            progress(f"graded {min(start + batch_size, len(todo))}/{len(todo)}"
                     f"  (${spent:.2f})")

    attach(records, cache)
    return {"graded": len(cache), "new": len(todo), "cost_usd": spent}


def attach(records, cache):
    """Copy cached grades onto whichever records have them."""
    for rec in records:
        row = cache.get(_digest(rec["text"]))
        if not row:
            continue
        scores = [row[a] for a in RUBRIC_AXES if isinstance(row.get(a), (int, float))]
        rec["llm"] = dict(row, mean=sum(scores) / len(scores) if scores else None)
    return records


def load_cache(cache_path):
    if not os.path.exists(cache_path):
        return {}
    with open(cache_path) as handle:
        return json.load(handle)
