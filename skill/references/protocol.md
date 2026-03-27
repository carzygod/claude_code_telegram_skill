# Telegram Bridge Protocol

This file documents the intended contract between a Telegram bridge and Claude Code.

## Intended Operator Commands

- `/status`
  Ask whether the bridge is alive and which workspace it is attached to.
- `/run <prompt>`
  Forward a natural-language request into Claude Code.
- `/run shell:<alias> [args]`
  Run a pre-approved local command alias.
- `/tail`
  Return the tail of the most recent task output.

## Claude-Side Expectations

- Acknowledge the first action before substantial work.
- Report progress in compact messages suitable for Telegram.
- Prefer summaries over raw logs.
- Surface blockers clearly and early.

## Recommended Output Style

- First line: status or result
- Second line: key finding or next action
- Optional final line: verification status

## Recommended Safety Policy

- Require confirmation for destructive actions.
- Keep execution scoped to one workspace.
- Restrict shell access to allowlisted aliases.
- Log every remote task with operator identity and timestamp.
