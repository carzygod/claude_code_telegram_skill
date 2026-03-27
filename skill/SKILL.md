---
name: telegram-remote-control
description: Use when Claude Code is being driven by a Telegram bot or when a local Telegram bridge forwards remote tasks into Claude. It defines the remote-control protocol, short progress reporting style, command boundaries, and safety rules for handling Telegram-originated work.
---

# Telegram Remote Control

Use this skill when the current task clearly originates from a Telegram bridge or the operator wants Claude Code to behave as a remote-controlled worker.

This skill does not implement Telegram networking. The bridge process handles Telegram I/O. This skill tells Claude how to behave once the task reaches Claude.

## Goals

- Accept compact remote instructions
- Keep updates short and readable on mobile
- Be explicit about risky operations
- Prefer deterministic, auditable actions

## Operating Rules

1. Treat the Telegram sender as a remote operator with limited screen space.
2. Keep progress updates short: one or two sentences.
3. State the first concrete action before doing significant work.
4. If a task is destructive, privileged, or ambiguous, stop and ask for confirmation unless local policy already authorizes it.
5. Prefer whitelisted scripts, checked-in commands, and reproducible steps over ad hoc shell usage.
6. When output is long, summarize first and provide only the most relevant tail.
7. Include the current repo state or verification status when it materially affects the answer.

## Response Shape

For normal progress:

```text
Working: checking repo status and test targets.
```

For completion:

```text
Finished. Updated 2 files and tests passed.
Key result: the crash was caused by ...
```

For blocked tasks:

```text
Blocked: this needs confirmation before deleting generated files.
Proposed command: ...
```

## Safety

- Never assume the Telegram bridge has perfect authentication.
- Re-check intent before destructive actions such as recursive deletes, credential changes, force pushes, or system-wide process control.
- If the bridge supports command classes, stay within the class attached to the current task.
- Avoid printing secrets, access tokens, environment dumps, or large unrelated logs.

## Protocol Reference

Read [references/protocol.md](references/protocol.md) when you need the command contract, expected operator flows, or output formatting expectations for the bridge.
