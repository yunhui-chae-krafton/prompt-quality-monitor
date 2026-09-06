"""Pull genuine human-typed prompts out of Claude Code session transcripts.

A session JSONL contains far more `user` records than the human actually
typed: tool results, pasted attachments, agent-to-agent peer messages, queued
replays and SDK traffic all carry `type: "user"`. The pair that isolates real
keyboard input is:

    promptSource == "typed"  and  origin.kind == "human"

Everything downstream depends on this filter being right, so it lives in one
place.
"""

import json
import os
import glob
import re

DEFAULT_ROOT = os.path.expanduser("~/.claude/projects")

# Claude Code injects these into the user turn; they are not typed by anyone.
_SYSTEM_BLOCK = re.compile(
    r"<(system-reminder|command-name|command-message|command-args|local-command-[a-z]+)>.*?"
    r"</\1>",
    re.DOTALL,
)
_STRAY_TAG = re.compile(r"</?(system-reminder|command-[a-z-]+)>")

# A pasted screenshot or text block leaves a marker in the typed prompt while
# the content itself lives in a separate attachment record. "[Image #1] 위 오류
# 조사해줘" reads as vague to any scorer, but the referent is right there in the
# image. Prompts carrying one are flagged so they are not graded as if the
# author had left the target unspecified.
_ATTACHMENT_REF = re.compile(r"\[(Image|Pasted text|Screenshot)\s*#?\d*\]",
                             re.IGNORECASE)


def clean(text):
    text = _SYSTEM_BLOCK.sub("", text)
    text = _STRAY_TAG.sub("", text)
    return text.strip()


def _text_of(content):
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)


def iter_sessions(root=DEFAULT_ROOT):
    """Yield (project_dir_name, session_path) for every transcript."""
    for path in sorted(glob.glob(os.path.join(root, "*", "*.jsonl"))):
        yield os.path.basename(os.path.dirname(path)), path


def read_session(project, path):
    """Return the typed prompts of one session, in order, with work counters.

    Each record carries how much agent work followed it (`assistant_turns`,
    `tool_calls`) before the next typed prompt, which serves as an effort
    proxy in the absence of a graded outcome.
    """
    prompts = []
    meta = {"session_id": os.path.basename(path)[:-6], "project": project}

    with open(path, errors="replace") as handle:
        for line in handle:
            try:
                rec = json.loads(line)
            except (ValueError, TypeError):
                continue

            rtype = rec.get("type")

            if rtype == "assistant" and prompts:
                prompts[-1]["assistant_turns"] += 1
                content = rec.get("message", {}).get("content") or []
                if isinstance(content, list):
                    prompts[-1]["tool_calls"] += sum(
                        1 for b in content
                        if isinstance(b, dict) and b.get("type") == "tool_use"
                    )
                continue

            if rtype != "user":
                continue
            if rec.get("promptSource") != "typed":
                continue
            if (rec.get("origin") or {}).get("kind") != "human":
                continue

            text = clean(_text_of(rec.get("message", {}).get("content")))
            if not text:
                continue

            meta.setdefault("cwd", rec.get("cwd"))
            meta.setdefault("git_branch", rec.get("gitBranch"))

            prompts.append({
                "session_id": meta["session_id"],
                "project": project,
                "turn": len(prompts),
                "timestamp": rec.get("timestamp"),
                "text": text,
                "chars": len(text),
                "has_attachment": bool(_ATTACHMENT_REF.search(text)),
                "assistant_turns": 0,
                "tool_calls": 0,
            })

    return meta, prompts


def load_all(root=DEFAULT_ROOT, projects=None, since=None):
    """Collect prompts from every session, optionally filtered.

    `projects` is a substring match against the project directory name;
    `since` is an ISO date string compared against the first prompt.
    """
    sessions, records = [], []
    for project, path in iter_sessions(root):
        if projects and not any(p in project for p in projects):
            continue
        meta, prompts = read_session(project, path)
        if not prompts:
            continue
        if since and (prompts[0]["timestamp"] or "") < since:
            continue
        meta["prompt_count"] = len(prompts)
        meta["started"] = prompts[0]["timestamp"]
        sessions.append(meta)
        records.extend(prompts)
    return sessions, records
