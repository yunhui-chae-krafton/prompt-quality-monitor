---
name: prompt-score
description: >
  Scores the prompts the user has actually typed into their coding agent, using
  the expert rubric from Thorgeirsson, Weidmann & Su (CHI '26). Reads the
  user's own session logs in ~/.claude/projects, classifies each turn, scores
  the instruction turns, optionally grades a stratified sample against the
  rubric, and produces a Markdown report plus a self-contained HTML dashboard.
  Use when the user asks to analyze, score, review or get feedback on their own
  prompts or prompting habits; asks how their prompting has changed over time;
  asks which words or habits are hurting their prompts; or says
  "prompt score", "프롬프트 점수", "내 프롬프트 분석", "프롬프트 품질",
  "내가 쓴 프롬프트 평가", "프롬프트 습관", or uses /prompt-score.
---

# Prompt Quality Monitor

Scores the user's own coding-agent prompts. Everything runs locally from
`~/.claude/projects`; nothing leaves the machine unless the user asks for
rubric grading, and even then it can stay inside this session.

## Finding the script

The package sits next to this file, at `scripts/pqm.py` inside this skill's
own directory. Resolve it in this order and use the first that exists:

1. `${CLAUDE_PLUGIN_ROOT}/skills/prompt-score/scripts/pqm.py` — set when this
   is installed as a Claude Code or Codex plugin.
2. `scripts/pqm.py` relative to the directory this SKILL.md was read from —
   correct for a skills-only install such as OpenCode, where the skill
   directory is symlinked and the code comes with it.

It needs only the Python standard library, so the system interpreter is
enough. Never create a virtualenv or install anything unless the user asks.

## Commands

Write `$PQM` for the resolved script path.

| Command | Cost | What it does |
|---|---|---|
| `python3 $PQM analyze` | free | Deterministic metrics over every prompt. Start here. |
| `python3 $PQM grade --limit 240` | free | Prepares a grading job for **you** to do (see below). |
| `python3 $PQM grade --ingest` | free | Merges your grades back in. |
| `python3 $PQM dashboard` | free | Self-contained HTML from whatever has been computed. |

Output goes to `~/.prompt-quality-monitor/`. Override with `--out` or
`$PQM_OUT`. Narrow the corpus with `--project <substring>` and
`--since YYYY-MM-DD`.

## How to use it in a conversation

1. Run `analyze` first. It is free and instant, and it tells you whether the
   user has enough history for the rest to mean anything. Under ~200 prompts,
   say so — the correlations will not be interpretable.
2. Read `~/.prompt-quality-monitor/report.md` and summarise it. Lead with the
   habit numbers (constraint rate, output-format rate, vague-word counts),
   which are the actionable part.
3. Offer rubric grading. Describe what it costs in *this* session (see below)
   and let the user decide rather than starting it unasked.
4. After grading, run `dashboard` and offer to publish or open it.

## Doing the rubric grading yourself

This is the default and the cheapest path: you are already a capable grader in
a session the user is already paying for, so no API key, no second
subscription, and no extra vendor is involved.

```
python3 $PQM grade --limit 240
```

This writes `~/.prompt-quality-monitor/grade-request.json` containing a
`rubric` string and an `items` array of `{id, prompt}`. Then:

- Read the request file. Grade the items **in batches of about 20**, applying
  the `rubric` field exactly as written — do not paraphrase or extend it.
- Append results to `~/.prompt-quality-monitor/grade-response.json` as a JSON
  array of `{id, coherence, complexity, clarity, issue, rewrite}`. Keep each
  `id` byte-for-byte as given; it is a content hash and is how grades are
  matched back.
- Run `python3 $PQM grade --ingest` to merge. Partial responses are fine —
  ingest merges whatever is present, and the cache means already-graded
  prompts are never re-done.

Grading 240 prompts is real work: budget roughly 12 batches. Tell the user the
scale before starting, and offer a smaller `--limit` (60 is enough to see the
distribution) if they would rather not spend the session on it.

### Unattended alternatives

If the user would rather not spend this session's context on grading:

```
python3 $PQM grade --backend claude --limit 240        # ~$2 via the claude CLI
python3 $PQM grade --backend command --command 'codex exec -'
python3 $PQM grade --backend command --command 'ollama run qwen3'
```

`--backend command` pipes the rubric plus a JSON batch to any command on
stdin and reads a JSON array back from stdout, so it covers other agent CLIs,
local model runners, and in-house gateway wrappers. Only suggest these when
the user raises cost or context; the default needs nothing extra.

## Reading the numbers honestly

Three things will mislead a reader who skips the report's caveats. Repeat them
when you summarise:

- **The deterministic score is a percentile inside the user's own corpus**, so
  its mean is 5 by construction. A monthly value of 5.2 does not mean "above
  average skill"; it means those prompts sat slightly above that user's own
  median. Only the rubric axes (0–10) are absolute.
- **The deterministic score and the rubric measure different things.** In the
  author's corpus they correlated at r = .02. Counting paths and identifiers
  is not the same as judging whether an instruction is clear. If the two
  disagree about a prompt, the rubric is the one to trust.
- **The outcome variable is a proxy.** "Rework" means the user pushed back
  within the next turn or two, detected by regex. It also fires on hard tasks,
  broken environments and model mistakes, so every reported r is a lower bound.

## Language

Turn classification is tuned for Korean and handles English; it has not been
checked on other languages. Korean lemmatisation is noticeably better with
`kiwipiepy` installed (`pip install kiwipiepy`), which roughly halves the
inflated MTLD values, but it is optional and the tool never requires it.

## When there is nothing to analyse

If `analyze` reports no prompts, the cause is almost always one of: the user
keeps sessions somewhere other than `~/.claude/projects` (pass `--root`), or
they are new to the tool. Do not silently widen the filter — the
`promptSource="typed"` + `origin.kind="human"` pair is what separates real
keyboard input from tool results and pasted content, and loosening it makes
every number downstream wrong.
