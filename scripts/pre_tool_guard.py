from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


RISKY_PATTERNS = [
    (re.compile(r"\brm\s+-rf\b", re.IGNORECASE), "Blocked destructive recursive delete."),
    (re.compile(r"\bdel\s+/[sqf]+\b", re.IGNORECASE), "Blocked destructive Windows delete."),
    (re.compile(r"\bRemove-Item\b[^\n]*-Recurse[^\n]*-Force", re.IGNORECASE), "Blocked destructive PowerShell delete."),
    (re.compile(r"\bgit\s+reset\s+--hard\b", re.IGNORECASE), "Blocked git reset --hard."),
    (re.compile(r"\bgit\s+clean\b[^\n]*-fd", re.IGNORECASE), "Blocked git clean that removes files."),
    (re.compile(r"\bgit\s+push\b[^\n]*--force(?:-with-lease)?\b", re.IGNORECASE), "Blocked force push."),
    (re.compile(r">\s*/dev/sd[a-z]\b", re.IGNORECASE), "Blocked direct write to block device."),
    (re.compile(r"\bformat\s+[a-z]:", re.IGNORECASE), "Blocked disk formatting command."),
    (re.compile(r"\bshutdown\b|\breboot\b", re.IGNORECASE), "Blocked machine shutdown or reboot."),
]


def deny(reason: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )


def allow() -> None:
    sys.exit(0)


def main() -> None:
    payload = json.load(sys.stdin)
    tool_name = str(payload.get("tool_name", ""))
    tool_input = payload.get("tool_input") or {}
    command = str(tool_input.get("command", ""))

    if tool_name not in {"Bash", "Shell", "PowerShell"}:
        allow()

    for pattern, reason in RISKY_PATTERNS:
        if pattern.search(command):
            deny(reason)
            return

    plugin_root = Path(os.environ.get("CLAUDE_PLUGIN_ROOT", "")).resolve() if os.environ.get("CLAUDE_PLUGIN_ROOT") else None
    cwd = payload.get("cwd")
    if plugin_root is not None and cwd:
        try:
            cwd_path = Path(str(cwd)).resolve()
            if not cwd_path.exists():
                deny("Blocked shell tool use outside an existing working directory.")
                return
        except OSError:
            deny("Blocked shell tool use because the working directory could not be resolved.")
            return

    allow()


if __name__ == "__main__":
    main()
