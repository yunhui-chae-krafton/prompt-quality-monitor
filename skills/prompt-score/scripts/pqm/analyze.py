"""Scoring, aggregation and the correlation that makes the tool worth running.

Scores are percentile ranks within the author's own corpus, not absolute
grades. Absolute thresholds for "is this prompt specific enough" would be
invented; a percentile answers the question actually being asked — how does
this prompt compare with the ~3.9k others I have written.

The outcome side is the point. Thorgeirsson et al. could correlate prompt
quality against a graded artifact (r = .479); a session log has no grader. The
usable proxy is rework: whether the next turn or two is the human telling the
agent it got it wrong. `correlations` tests exactly the hypothesis the paper
suggests following up on — better prompts, fewer iterations.
"""

import math
import statistics
from collections import Counter, defaultdict

from .lexical import hdd, mtld, tokenize
from .rubric import SCORED_KINDS, classify, features

#: How many following turns count as rework attributable to a prompt.
REWORK_LOOKAHEAD = 2

#: HD-D needs at least this many tokens to mean anything (its sample size).
MIN_TOKENS_FOR_DIVERSITY = 42

AXES = ("specificity", "structure", "constraints")
_RAW_OF = {
    "specificity": "specificity_raw",
    "structure": "structure_raw",
    "constraints": "constraint_raw",
}


def percentile_scores(values):
    """Map raw values to 0-10 by rank. Ties share the mean rank."""
    n = len(values)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        mean_rank = (i + j) / 2.0
        for k in range(i, j + 1):
            ranks[order[k]] = mean_rank
        i = j + 1
    denom = max(n - 1, 1)
    return [10.0 * r / denom for r in ranks]


def pearson(xs, ys):
    """Pearson r with a Fisher-z p-value and 95% CI, as the paper reports."""
    n = len(xs)
    if n < 4:
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    r = sxy / math.sqrt(sxx * syy)
    r = max(min(r, 0.999999), -0.999999)

    z = math.atanh(r)
    se = 1.0 / math.sqrt(n - 3)
    p = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(z / se) / math.sqrt(2.0))))
    lo, hi = math.tanh(z - 1.96 * se), math.tanh(z + 1.96 * se)
    return {"r": r, "p": p, "n": n, "ci": [lo, hi]}


def partial_pearson(xs, ys, zs):
    """Correlation between x and y with z partialled out.

    The paper's central move is reporting a partial correlation (CS
    achievement survives controlling for general cognitive ability; writing
    does not). The same discipline is needed here for a different lurking
    variable: a long, hard session accumulates both more vocabulary and more
    corrections, so any raw diversity-to-rework correlation has to be checked
    against session length before it means anything.
    """
    rxy, rxz, ryz = pearson(xs, ys), pearson(xs, zs), pearson(ys, zs)
    if not (rxy and rxz and ryz):
        return None
    denom = math.sqrt((1 - rxz["r"] ** 2) * (1 - ryz["r"] ** 2))
    if denom <= 0:
        return None
    r = max(min((rxy["r"] - rxz["r"] * ryz["r"]) / denom, 0.999999), -0.999999)

    n = len(xs)
    if n < 5:
        return None
    z = math.atanh(r)
    se = 1.0 / math.sqrt(n - 4)  # one control variable costs a degree of freedom
    p = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(z / se) / math.sqrt(2.0))))
    return {"r": r, "p": p, "n": n,
            "ci": [math.tanh(z - 1.96 * se), math.tanh(z + 1.96 * se)],
            "controlled_for": "session_turns"}


def build(sessions_meta, records):
    """Score every prompt, aggregate by session, and run the correlations."""
    by_session = defaultdict(list)

    for rec in records:
        rec["kind"] = classify(rec["text"], rec["turn"])
        rec["features"] = features(rec["text"])
        by_session[rec["session_id"]].append(rec)

    # Rework attribution: did the human push back within the next few turns?
    for prompts in by_session.values():
        for idx, rec in enumerate(prompts):
            window = prompts[idx + 1: idx + 1 + REWORK_LOOKAHEAD]
            rec["rework_next"] = any(p["kind"] == "correct" for p in window)

    scored = [r for r in records if r["kind"] in SCORED_KINDS]
    for axis in AXES:
        raw = [r["features"][_RAW_OF[axis]] for r in scored]
        for rec, value in zip(scored, percentile_scores(raw)):
            rec.setdefault("scores", {})[axis] = value
    for rec in scored:
        rec["scores"]["total"] = statistics.fmean(
            rec["scores"][a] for a in AXES
        )

    sessions = _aggregate_sessions(sessions_meta, by_session)
    _attach_session_scores(sessions, by_session)
    return {
        "prompts": records,
        "sessions": sessions,
        "kind_counts": dict(Counter(r["kind"] for r in records)),
        "correlations": _correlations(scored, sessions),
        "vague_top": _vague_top(scored),
        "monthly": _monthly(scored),
        "weekly": _weekly(scored),
    }


def _aggregate_sessions(sessions_meta, by_session):
    out = []
    for meta in sessions_meta:
        prompts = by_session.get(meta["session_id"], [])
        if not prompts:
            continue
        scored = [p for p in prompts if p["kind"] in SCORED_KINDS]
        corrections = sum(1 for p in prompts if p["kind"] == "correct")

        # Diversity is measured over instruction turns only. Including the
        # correction turns would be circular: a complaint carries pasted error
        # text and fresh vocabulary, so sessions with more rework would score
        # as more lexically diverse purely because they went wrong.
        tokens = tokenize("\n".join(p["text"] for p in scored))

        row = dict(meta)
        row.update({
            "turns": len(prompts),
            "scored_turns": len(scored),
            "corrections": corrections,
            "rework_rate": corrections / len(prompts),
            "assistant_turns": sum(p["assistant_turns"] for p in prompts),
            "tool_calls": sum(p["tool_calls"] for p in prompts),
            "tokens": len(tokens),
            "mtld": mtld(tokens) if len(tokens) >= MIN_TOKENS_FOR_DIVERSITY else None,
            "hdd": hdd(tokens) if len(tokens) >= MIN_TOKENS_FOR_DIVERSITY else None,
            "mean_score": None,
        })
        out.append(row)
    return out


def _attach_session_scores(sessions, by_session):
    """Fill each session's mean prompt score; must run before correlations."""
    for row in sessions:
        vals = [
            p["scores"]["total"]
            for p in by_session.get(row["session_id"], [])
            if p["kind"] in SCORED_KINDS and "scores" in p
        ]
        row["mean_score"] = statistics.fmean(vals) if vals else None


def _correlations(scored, sessions):
    out = {}

    # The headline test: does a better prompt get pushed back on less often?
    if scored:
        xs = [r["scores"]["total"] for r in scored]
        ys = [1.0 if r["rework_next"] else 0.0 for r in scored]
        out["prompt_score_vs_rework"] = pearson(xs, ys)
        for axis in AXES:
            out[f"{axis}_vs_rework"] = pearson(
                [r["scores"][axis] for r in scored], ys
            )
        out["prompt_score_vs_agent_turns"] = pearson(
            xs, [float(r["assistant_turns"]) for r in scored]
        )

    # The paper's strongest prompt-side predictor was a human applying a
    # rubric, not a surface metric, so the LLM grades get the same test. The
    # sample is small but it is the more sensitive of the two.
    judged = [r for r in scored
              if r.get("llm") and r["llm"].get("mean") is not None
              and not r.get("has_attachment")]
    if len(judged) >= 20:
        rework = [1.0 if r["rework_next"] else 0.0 for r in judged]
        out["llm_score_vs_rework"] = pearson(
            [r["llm"]["mean"] for r in judged], rework
        )
        out["llm_clarity_vs_rework"] = pearson(
            [r["llm"]["clarity"] for r in judged
             if isinstance(r["llm"].get("clarity"), (int, float))],
            [rw for r, rw in zip(judged, rework)
             if isinstance(r["llm"].get("clarity"), (int, float))],
        )
        out["llm_vs_regex_score"] = pearson(
            [r["llm"]["mean"] for r in judged],
            [r["scores"]["total"] for r in judged],
        )

    usable = [s for s in sessions
              if s.get("mtld") is not None and s.get("mean_score") is not None]
    if len(usable) >= 4:
        out["session_mtld_vs_rework"] = pearson(
            [s["mtld"] for s in usable], [s["rework_rate"] for s in usable]
        )
        out["session_hdd_vs_rework"] = pearson(
            [s["hdd"] for s in usable], [s["rework_rate"] for s in usable]
        )
        out["session_score_vs_rework"] = pearson(
            [s["mean_score"] for s in usable], [s["rework_rate"] for s in usable]
        )
        out["session_mtld_vs_score"] = pearson(
            [s["mtld"] for s in usable], [s["mean_score"] for s in usable]
        )

        # Same tests, with session length partialled out.
        turns = [float(s["turns"]) for s in usable]
        rework = [s["rework_rate"] for s in usable]
        out["session_mtld_vs_rework_partial"] = partial_pearson(
            [s["mtld"] for s in usable], rework, turns
        )
        out["session_hdd_vs_rework_partial"] = partial_pearson(
            [s["hdd"] for s in usable], rework, turns
        )
        out["session_score_vs_rework_partial"] = partial_pearson(
            [s["mean_score"] for s in usable], rework, turns
        )
        out["session_turns_vs_rework"] = pearson(turns, rework)
        out["session_turns_vs_hdd"] = pearson(turns, [s["hdd"] for s in usable])
    return out


def _vague_top(scored, limit=25):
    counter = Counter()
    examples = {}
    for rec in scored:
        for term in rec["features"]["vague_terms"]:
            counter[term] += 1
            if term not in examples:
                examples[term] = rec["text"][:120]
    return [
        {"term": term, "count": count, "example": examples[term]}
        for term, count in counter.most_common(limit)
    ]


def _monthly(scored):
    buckets = defaultdict(list)
    rework = defaultdict(list)
    for rec in scored:
        stamp = rec.get("timestamp") or ""
        if len(stamp) < 7:
            continue
        buckets[stamp[:7]].append(rec["scores"]["total"])
        rework[stamp[:7]].append(1.0 if rec["rework_next"] else 0.0)
    return [
        {
            "month": month,
            "n": len(vals),
            "mean_score": statistics.fmean(vals),
            "rework_rate": statistics.fmean(rework[month]),
        }
        for month, vals in sorted(buckets.items())
    ]


def _weekly(scored):
    """Same trend at week resolution; a two-month corpus has too few months."""
    import datetime

    buckets = defaultdict(list)
    rework = defaultdict(list)
    for rec in scored:
        stamp = rec.get("timestamp") or ""
        try:
            day = datetime.date.fromisoformat(stamp[:10])
        except ValueError:
            continue
        monday = day - datetime.timedelta(days=day.weekday())
        key = monday.isoformat()
        buckets[key].append(rec["scores"]["total"])
        rework[key].append(1.0 if rec["rework_next"] else 0.0)

    return [
        {
            "week": week,
            "n": len(vals),
            "mean_score": statistics.fmean(vals),
            "rework_rate": statistics.fmean(rework[week]),
        }
        for week, vals in sorted(buckets.items())
        if len(vals) >= 5
    ]
