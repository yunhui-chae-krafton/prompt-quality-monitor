"""Rubric grading for a stratified sample of prompts.

The regex features in `rubric` see surface form only. The paper's strongest
prompt-side signal came from a human expert applying a rubric (r = .479 with
performance, against r = .31-.34 for lexical diversity), so a judgment layer
earns its place. This grades a sample rather than the corpus: the point is a
calibrated read on the distribution, not a score attached to every turn.

Three backends, because tying the grader to one vendor's CLI would put this
layer out of reach for anyone whose plan or workplace does not include it:

- `agent` (default) writes a request file and stops. Whatever agent is running
  the skill grades it in its own session and writes the answers back. Costs
  nothing extra, needs no API key, and works in any product that can run the
  skill at all — the reader is already there.
- `claude` shells out to the `claude` CLI unattended.
- `command` pipes the request to any command you name: another CLI agent, a
  local model runner, a gateway wrapper. JSON in on stdin, JSON out on stdout.

Results are cached by prompt hash, so a re-run only pays for new prompts, and
the cache is shared across backends.
"""

import hashlib
import json
import os
import subprocess
import tempfile

from .rubric import SCORED_KINDS

RUBRIC_AXES = ("coherence", "complexity", "clarity")
BACKENDS = ("agent", "claude", "command")

RUBRIC = """당신은 소프트웨어 요구사항 엔지니어링 전문가이자 프롬프트 분석가입니다.
아래는 한 개발자가 코딩 에이전트에게 실제로 입력한 프롬프트들입니다.
각 프롬프트를 '에이전트에게 내리는 지시'로서 평가하십시오.

평가 축 (각 0~10 정수):
- coherence: 하나의 프롬프트 안에서 요구가 논리적으로 일관되고 서로 모순되지 않는가.
- complexity: 과제 규모에 맞는 정보량인가. 너무 빈약하거나 불필요하게 장황하지 않은가.
- clarity: 지시가 명확한가. 지시 대상·동작·완료 조건이 해석의 여지 없이 특정되는가.

평가 원칙:
- 이전 대화 맥락은 주어지지 않는다. 맥락 없이 읽히지 않는다는 이유만으로 감점하지 말고,
  '이 문장이 그 맥락 안에서 얼마나 정확히 특정하는가'를 보라.
- 정중한 의문형("~해줄 수 있어?", "can you fix X?")은 지시이지 질문이 아니다.
  어투는 감점 사유가 아니다.
- 짧다는 이유만으로 감점하지 말라. 짧아도 대상과 동작이 특정되면 clarity는 높다.

각 프롬프트마다 다음을 출력하십시오:
- id: 입력에 주어진 id 그대로 (수정하지 말 것)
- coherence, complexity, clarity: 0~10 정수
- issue: 가장 큰 문제 한 가지를 한 문장으로. 문제가 없으면 빈 문자열.
- rewrite: 문제가 있을 때만, 같은 의도를 유지한 개선 프롬프트. 없으면 빈 문자열.

출력은 JSON 배열 하나만. 설명·머리말·코드펜스 없이 배열만 출력하십시오."""


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# Sampling
# --------------------------------------------------------------------------


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


def pending(records, cache, limit):
    """The sampled prompts that have not been graded yet."""
    return [r for r in stratified_sample(records, limit)
            if _digest(r["text"]) not in cache]


# --------------------------------------------------------------------------
# Backend: agent-in-the-loop
# --------------------------------------------------------------------------


def write_request(todo, request_path, batch_size=20):
    """Write the grading job for whatever agent is running this.

    Items are keyed by content hash rather than position so the answers can
    come back in any order, in pieces, or across several sittings.
    """
    payload = {
        "rubric": RUBRIC,
        "response_path": os.path.join(
            os.path.dirname(request_path), "grade-response.json"
        ),
        "batch_size": batch_size,
        "count": len(todo),
        "items": [{"id": _digest(r["text"]), "prompt": r["text"][:1800]}
                  for r in todo],
    }
    with open(request_path, "w") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)
    return payload


def ingest(response_path, cache_path):
    """Merge an agent-written response file into the cache.

    Tolerant on purpose: an agent may hand back a bare array, or wrap it in
    {"results": [...]}, and may cover only part of the request.
    """
    with open(response_path) as handle:
        body = json.load(handle)
    if isinstance(body, dict):
        body = body.get("results") or body.get("items") or []

    cache = load_cache(cache_path)
    added = 0
    for row in body:
        if not isinstance(row, dict):
            continue
        key = row.get("id")
        if not key or not any(
            isinstance(row.get(axis), (int, float)) for axis in RUBRIC_AXES
        ):
            continue
        cache[key] = _row_to_entry(row)
        added += 1

    _save_cache(cache, cache_path)
    return {"ingested": added, "skipped": len(body) - added, "graded": len(cache)}


# --------------------------------------------------------------------------
# Backends: subprocess
# --------------------------------------------------------------------------


def _parse_array(body):
    body = (body or "").strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[1].rsplit("```", 1)[0]
    start, end = body.find("["), body.rfind("]")
    if start != -1 and end > start:
        body = body[start:end + 1]
    return json.loads(body)


def _read_envelope(stdout):
    """Pull the result envelope out of whatever shape the CLI produced.

    `--output-format json` returns one object. `--output-format stream-json`
    returns one object per line and the payload is the last of them. Assuming
    either shape means a call that already cost money gets thrown away, so
    accept both.
    """
    stdout = (stdout or "").strip()
    if not stdout:
        raise RuntimeError("empty response from grading command")
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        pass

    envelope = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(message, dict) and message.get("type") == "result":
            envelope = message
    if envelope is None:
        raise RuntimeError(
            "could not find a result envelope in the response "
            f"({len(stdout.splitlines())} lines)"
        )
    return envelope


def _call_claude(payload, model, timeout):
    """One grading call through the `claude` CLI.

    MCP servers are switched off: they add tens of thousands of tokens of tool
    definitions per call and nothing to the grading.
    """
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

    envelope = _read_envelope(proc.stdout)
    return _parse_array(envelope.get("result")), envelope.get("total_cost_usd", 0.0)


def _call_command(payload, command, timeout):
    """One grading call through an arbitrary command.

    The command reads the prompt on stdin and writes the JSON array on stdout.
    Cost is unknown to us, so it is reported as zero.
    """
    proc = subprocess.run(
        command, shell=True, input=payload,
        capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"command failed ({proc.returncode}): "
                           f"{(proc.stderr or proc.stdout)[:400]}")

    # An arbitrary command may hand back a bare array or an agent CLI's
    # envelope; try the array first and fall back to unwrapping.
    try:
        return _parse_array(proc.stdout), 0.0
    except (json.JSONDecodeError, ValueError):
        envelope = _read_envelope(proc.stdout)
        return _parse_array(envelope.get("result")), 0.0


#: Consecutive failures that mean the problem is systematic, not transient.
#: Past this we stop rather than keep paying to rediscover the same fault.
MAX_CONSECUTIVE_FAILURES = 2


def grade(records, cache_path, limit=300, batch_size=12, backend="claude",
          model="claude-sonnet-5", command=None, timeout=300, progress=None):
    """Grade the sample with a subprocess backend, reusing the cache.

    Failures are counted, not just logged. A batch that fails after the
    request went out has already cost money, so a systematic fault — a bad
    envelope shape, an expired login, a model that stopped emitting arrays —
    must stop the run instead of repeating once per batch.
    """
    if backend == "command" and not command:
        raise ValueError("backend 'command' needs --command")

    cache = load_cache(cache_path)
    before = len(cache)
    todo = pending(records, cache, limit)

    spent = 0.0
    failures, streak, last_error = 0, 0, None
    for start in range(0, len(todo), batch_size):
        batch = todo[start: start + batch_size]
        items = [{"id": _digest(r["text"]), "prompt": r["text"][:1800]}
                 for r in batch]
        payload = f"{RUBRIC}\n\n입력:\n{json.dumps(items, ensure_ascii=False, indent=1)}"

        try:
            if backend == "claude":
                graded, cost = _call_claude(payload, model, timeout)
            else:
                graded, cost = _call_command(payload, command, timeout)
        except Exception as exc:
            failures += 1
            streak += 1
            last_error = str(exc)
            if progress:
                progress(f"batch {start // batch_size + 1} failed: {exc}")
            if streak >= MAX_CONSECUTIVE_FAILURES:
                if progress:
                    progress(f"연속 {streak}회 실패 — 같은 원인이 반복되고 있어 "
                             f"중단합니다. 남은 배치는 호출하지 않습니다.")
                break
            continue

        streak = 0
        spent += cost
        by_id = {g.get("id"): g for g in graded if isinstance(g, dict)}
        for rec in batch:
            row = by_id.get(_digest(rec["text"]))
            if row:
                cache[_digest(rec["text"])] = _row_to_entry(row)

        _save_cache(cache, cache_path)
        if progress:
            note = f"  (${spent:.2f})" if backend == "claude" else ""
            progress(f"graded {min(start + batch_size, len(todo))}/{len(todo)}{note}")

    attach(records, cache)
    return {
        "graded": len(cache),
        "new": len(todo),
        "cached": len(cache) - before,
        "cost_usd": spent,
        "failed_batches": failures,
        "last_error": last_error,
    }


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------


def _row_to_entry(row):
    entry = {axis: row.get(axis) for axis in RUBRIC_AXES}
    entry["issue"] = row.get("issue", "")
    entry["rewrite"] = row.get("rewrite", "")
    return entry


def _save_cache(cache, cache_path):
    os.makedirs(os.path.dirname(os.path.abspath(cache_path)), exist_ok=True)
    with open(cache_path, "w") as handle:
        json.dump(cache, handle, ensure_ascii=False, indent=1)


def attach(records, cache):
    """Copy cached grades onto whichever records have them."""
    for rec in records:
        row = cache.get(_digest(rec["text"]))
        if not row:
            continue
        scores = [row[a] for a in RUBRIC_AXES
                  if isinstance(row.get(a), (int, float))]
        rec["llm"] = dict(row, mean=sum(scores) / len(scores) if scores else None)
    return records


def load_cache(cache_path):
    if not os.path.exists(cache_path):
        return {}
    with open(cache_path) as handle:
        return json.load(handle)
