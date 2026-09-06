"""Markdown report.

Percentile scores are uniform by construction, so reporting their distribution
would say nothing. What is worth reading back is the absolute behaviour the
percentiles are built from — how often a prompt names a concrete referent,
how often it leans on 이거/그거, whether constraints get stated at all — plus
the correlations, reported whether or not they came out significant.
"""

import statistics
from collections import Counter

from .rubric import SCORED_KINDS

_KIND_LABEL = {
    "initiate": "과제 개시",
    "refine": "후속 지시",
    "question": "질문",
    "correct": "정정 (재작업)",
    "approve": "승인",
    "status": "상태 보고",
}

_CORR_LABEL = {
    "prompt_score_vs_rework": "프롬프트 점수 → 직후 재작업",
    "specificity_vs_rework": "  구체성 → 재작업",
    "structure_vs_rework": "  구조화 → 재작업",
    "constraints_vs_rework": "  제약명시 → 재작업",
    "prompt_score_vs_agent_turns": "프롬프트 점수 → 에이전트 작업량",
    "llm_score_vs_rework": "LLM 루브릭 점수 → 직후 재작업",
    "llm_clarity_vs_rework": "  LLM 지시 명확성 → 재작업",
    "llm_vs_regex_score": "LLM 루브릭 점수 ↔ 정규식 점수 (일치도)",
    "session_mtld_vs_rework": "세션 MTLD → 재작업률",
    "session_hdd_vs_rework": "세션 HD-D → 재작업률",
    "session_score_vs_rework": "세션 평균점수 → 재작업률",
    "session_mtld_vs_score": "세션 MTLD → 세션 평균점수",
    "session_mtld_vs_rework_partial": "세션 MTLD → 재작업률 (턴수 통제)",
    "session_hdd_vs_rework_partial": "세션 HD-D → 재작업률 (턴수 통제)",
    "session_score_vs_rework_partial": "세션 평균점수 → 재작업률 (턴수 통제)",
    "session_turns_vs_rework": "세션 턴수 → 재작업률",
    "session_turns_vs_hdd": "세션 턴수 → HD-D",
}


def _pct(part, whole):
    return f"{100.0 * part / whole:.1f}%" if whole else "-"


def render(result):
    prompts = result["prompts"]
    scored = [r for r in prompts if r["kind"] in SCORED_KINDS and "scores" in r]
    sessions = result["sessions"]
    out = []

    out.append("# 프롬프트 품질 리포트\n")
    out += _corpus(prompts, scored, sessions)
    out += _habits(scored)
    out += _diversity(sessions)
    out += _correlations(result["correlations"])
    out += _trend(result["monthly"])
    out += _llm(prompts)
    out += _extremes(scored)
    return "\n".join(out)


def _corpus(prompts, scored, sessions):
    stamps = sorted(r["timestamp"] for r in prompts if r.get("timestamp"))
    counts = Counter(r["kind"] for r in prompts)
    total = len(prompts)

    out = ["## 코퍼스\n"]
    out.append(f"- 세션 **{len(sessions)}개**, 직접 입력한 프롬프트 **{total:,}개**")
    if stamps:
        out.append(f"- 기간 {stamps[0][:10]} ~ {stamps[-1][:10]}")
    out.append(f"- 루브릭 적용 대상(지시 턴) **{len(scored):,}개**\n")
    out.append("| 턴 유형 | 개수 | 비중 |")
    out.append("|---|---:|---:|")
    for kind, n in counts.most_common():
        out.append(f"| {_KIND_LABEL.get(kind, kind)} | {n:,} | {_pct(n, total)} |")
    out.append("")
    return out


def _habits(scored):
    n = len(scored)
    if not n:
        return []
    has = lambda key: sum(1 for r in scored if r["features"][key] > 0)

    out = ["## 지시 프롬프트 습관\n"]
    out.append("| 지표 | 값 |")
    out.append("|---|---:|")
    out.append(f"| 구체적 참조(파일·경로·식별자·수치)를 포함한 비율 | {_pct(has('concrete_hits'), n)} |")
    out.append(f"| 제약 조건을 명시한 비율 | {_pct(has('constraint_hits'), n)} |")
    out.append(f"| 출력 형식을 지정한 비율 | {_pct(has('format_hits'), n)} |")
    out.append(f"| 모호어를 포함한 비율 | {_pct(has('vague_hits'), n)} |")
    out.append(f"| 여러 줄로 쓴 비율 | {_pct(sum(1 for r in scored if r['features']['lines'] > 1), n)} |")
    out.append(f"| 목록·번호를 쓴 비율 | {_pct(has('list_items'), n)} |")
    out.append(f"| 중앙값 길이 | {statistics.median(r['chars'] for r in scored):.0f}자 |")
    attached = sum(1 for r in scored if r.get("has_attachment"))
    out.append(f"| 붙여넣은 이미지·텍스트를 참조 | {_pct(attached, n)} |")
    out.append("")
    if attached:
        out.append(f"이 중 {attached}개는 `[Image #N]` 같은 첨부를 가리킨다. "
                   "텍스트만 보면 “위 오류를 조사해줘”처럼 모호해 보이지만 지시 대상은 "
                   "이미지에 있으므로, LLM 채점 표본에서는 제외했다.\n")
    return out


def _diversity(sessions):
    usable = [s for s in sessions if s.get("mtld")]
    if not usable:
        return []
    mt = sorted(s["mtld"] for s in usable)
    hd = sorted(s["hdd"] for s in usable)
    pick = lambda v, p: v[int(len(v) * p)]

    out = ["## 어휘 다양성 (세션 단위, 지시 턴만)\n"]
    out.append("논문이 성과와 유의한 상관을 보고한 두 지표. 42토큰 이상인 "
               f"세션 {len(usable)}개에서 측정.\n")
    out.append("| 지표 | p10 | 중앙값 | p90 |")
    out.append("|---|---:|---:|---:|")
    out.append(f"| MTLD | {pick(mt, .1):.1f} | {statistics.median(mt):.1f} | {pick(mt, .9):.1f} |")
    out.append(f"| HD-D | {pick(hd, .1):.3f} | {statistics.median(hd):.3f} | {pick(hd, .9):.3f} |")
    out.append("")
    return out


def _correlations(corrs):
    out = ["## 상관 분석\n"]
    out.append("결과 대리 지표는 **재작업**(다음 1~2턴 안에 정정이 나오는가)이다. "
               "논문처럼 채점된 성과물이 없으므로 이것이 상한이다.\n")
    out.append("| 관계 | r | p | n |")
    out.append("|---|---:|---:|---:|")
    for key, corr in corrs.items():
        if not corr:
            continue
        mark = " **\\***" if corr["p"] < 0.05 else ""
        out.append(f"| {_CORR_LABEL.get(key, key)} | {corr['r']:+.3f}{mark} | "
                   f"{corr['p']:.3g} | {corr['n']} |")
    out.append("\n`*` = p < 0.05\n")
    return out


def _trend(monthly):
    if not monthly:
        return []
    out = ["## 월별 추이\n", "| 월 | 지시 턴 | 평균 점수 | 재작업 발생률 |", "|---|---:|---:|---:|"]
    for row in monthly:
        out.append(f"| {row['month']} | {row['n']:,} | {row['mean_score']:.2f} | "
                   f"{row['rework_rate'] * 100:.1f}% |")
    out.append("\n점수는 코퍼스 내 백분위이므로 전체 평균은 5에 고정된다. "
               "월별 값은 그 달의 프롬프트가 전체 대비 어디에 있었는지를 뜻한다.\n")
    return out


def _llm(prompts):
    # Grades are cached by prompt text, so a string that also occurs as an
    # approval or a status report picks one up. Only instruction turns whose
    # referent is actually in the text belong in this section.
    graded = [r for r in prompts
              if r["kind"] in SCORED_KINDS and not r.get("has_attachment")
              and r.get("llm") and r["llm"].get("mean") is not None]
    if not graded:
        return []
    out = ["## LLM 루브릭 채점 (표본)\n"]
    out.append(f"표본 {len(graded)}개. 축별 0~10.\n")
    out.append("| 축 | 평균 | 중앙값 |")
    out.append("|---|---:|---:|")
    for axis, label in (("coherence", "일관성"), ("complexity", "적정 정보량"),
                        ("clarity", "지시 명확성")):
        vals = [r["llm"][axis] for r in graded
                if isinstance(r["llm"].get(axis), (int, float))]
        if vals:
            out.append(f"| {label} | {statistics.fmean(vals):.2f} | "
                       f"{statistics.median(vals):.1f} |")
    out.append("")

    issues = [r for r in graded if (r["llm"].get("issue") or "").strip()]
    if issues:
        out.append("### 가장 낮게 평가된 프롬프트\n")
        for rec in sorted(issues, key=lambda r: r["llm"]["mean"])[:8]:
            out.append(f"- `{rec['text'][:80].strip()}`")
            out.append(f"  - **{rec['llm']['mean']:.1f}/10** — {rec['llm']['issue']}")
            if (rec["llm"].get("rewrite") or "").strip():
                out.append(f"  - 개선안: {rec['llm']['rewrite'][:200]}")
        out.append("")
    return out


def _extremes(scored):
    if not scored:
        return []
    ranked = sorted(scored, key=lambda r: r["scores"]["total"])
    out = ["## 결정론적 점수 양극단\n", "### 하위\n"]
    for rec in ranked[:6]:
        out.append(f"- `{rec['text'][:90].strip()}` — {rec['scores']['total']:.1f}/10")
    out.append("\n### 상위\n")
    for rec in ranked[-6:][::-1]:
        out.append(f"- `{rec['text'][:90].strip()}` — {rec['scores']['total']:.1f}/10")
    out.append("")
    return out
