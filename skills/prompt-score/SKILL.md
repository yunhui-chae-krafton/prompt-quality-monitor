---
name: prompt-score
description: >
  Scores the prompts the user has actually typed into Claude Code, using the
  expert rubric from Thorgeirsson, Weidmann & Su (CHI '26). Reads the user's own
  session logs in ~/.claude/projects, classifies each turn, scores the
  instruction turns, optionally grades a stratified sample with an LLM rubric,
  and produces a Markdown report plus a self-contained HTML dashboard.
  Use when the user asks to analyze, score, review or get feedback on their own
  prompts or prompting habits; asks how their prompting has changed over time;
  asks which words or habits are hurting their prompts; or says
  "prompt score", "프롬프트 점수", "내 프롬프트 분석", "프롬프트 품질",
  "내가 쓴 프롬프트 평가", "프롬프트 습관", or uses /prompt-score.
---

# Prompt Quality Monitor

Scores the user's own Claude Code prompts. Everything runs locally from
`~/.claude/projects`; nothing is uploaded unless the user asks for LLM grading.

## Running it

The package needs only the standard library, so the system interpreter is
enough. Never create a virtualenv or install anything unless the user asks.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pqm.py" analyze
```

Output goes to `~/.prompt-quality-monitor/` (`report.md`, `analysis.json`,
`dashboard.html`, `llm-cache.json`). Override with `--out` or `$PQM_OUT`.

### Commands

| Command | Cost | What it does |
|---|---|---|
| `analyze` | free | Deterministic metrics over every prompt. Start here. |
| `grade --limit 240` | ~$2 | LLM rubric on a stratified sample. Cached by prompt hash. |
| `dashboard` | free | Self-contained HTML from whatever has been computed. |
| `run --limit 240` | ~$2 | `grade` then `dashboard`. |

Useful flags: `--project <substring>` and `--since YYYY-MM-DD` to narrow the
corpus, `--model` to change the grading model, `--print-report` to put the
Markdown on stdout instead of only writing it.

## How to use it in a conversation

1. Run `analyze` first. It is free and instant, and it tells you whether the
   user has enough history for the rest to mean anything. Under ~200 prompts,
   say so — the correlations will not be interpretable.
2. Read `~/.prompt-quality-monitor/report.md` and summarise it. Lead with the
   habit numbers (constraint rate, output-format rate, vague-word counts),
   which are the actionable part.
3. Offer LLM grading rather than running it unprompted — it costs real money
   and sends the user's prompt text to their own Claude account. State the
   approximate cost and let them decide.
4. After grading, run `dashboard` and offer to publish it as an artifact.

## Reading the numbers honestly

Three things will mislead a reader who skips the report's caveats. Repeat them
when you summarise:

- **The deterministic score is a percentile inside the user's own corpus**, so
  its mean is 5 by construction. A monthly value of 5.2 does not mean "above
  average skill"; it means those prompts sat slightly above that user's own
  median. Only the LLM axes (0–10) are absolute.
- **The deterministic score and the LLM rubric measure different things.** In
  the author's corpus they correlated at r = .02. Counting paths and
  identifiers is not the same as judging whether an instruction is clear. If
  the two disagree about a prompt, the rubric is the one to trust.
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
they are new to Claude Code. Do not silently widen the filter — the
`promptSource="typed"` + `origin.kind="human"` pair is what separates real
keyboard input from tool results and pasted content, and loosening it makes
every number downstream wrong.
