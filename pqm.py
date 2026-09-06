#!/usr/bin/env python3
"""Launcher for the copy that lives beside the skill.

The package sits inside `skills/prompt-score/` so that a symlink of the skill
directory — which is how OpenCode and any other skills-only harness picks it
up — carries the code with it. This shim keeps `python3 pqm.py` working for
anyone who just cloned the repository.

It re-execs rather than importing so the real script gets the sys.path entry
Python only adds for the file it was launched with.
"""

import os
import sys

TARGET = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "skills", "prompt-score", "scripts", "pqm.py",
)

if __name__ == "__main__":
    os.execv(sys.executable, [sys.executable, TARGET, *sys.argv[1:]])
