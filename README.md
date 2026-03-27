# Telegram Remote Control For Claude Code

This directory now contains a Claude Code plugin plus a Telegram bridge for remotely driving a local Claude Code workflow.

This is not a "skill-only" solution. It is made of four parts:

1. `bridge.py`
   A local long-running bridge process. It polls Telegram messages, checks authorization, runs local commands, and sends results back.
2. `.claude-plugin/plugin.json`, `skills/`, and `hooks/`
   A Claude Code plugin that Claude can load through `--plugin-dir`.
3. `scripts/pre_tool_guard.py`
   A hook command that blocks a set of dangerous shell operations before Claude executes them.
4. `.env.example` and `commands.example.json`
   Example configuration files. Copy them and replace the sample values with real values for your machine.

## Current Features

- Telegram user allowlist
- `/help`, `/status`, `/run`, and `/tail`
- Claude prompt forwarding through a local CLI command template
- Automatic plugin loading through `--plugin-dir`
- `PreToolUse` safety hook for risky shell commands
- Shell command aliases through a whitelist file
- Local audit logging
- Single-task execution lock to avoid concurrent writes in one workspace

## Directory Layout

```text
telegram/
|- .claude-plugin/
|  \- plugin.json
|- README.md
|- .env.example
|- commands.example.json
|- bridge.py
|- .gitignore
|- hooks/
|  \- hooks.json
|- scripts/
|  \- pre_tool_guard.py
\- skills/
   \- telegram-remote-control/
      |- SKILL.md
      \- references/
         \- protocol.md
```

## How It Works

### Claude mode

Use `/run <prompt>` to send natural-language work into the local Claude CLI.

Example:

```text
/run fix the failing tests in the current repo and summarize the changes
```

If `CLAUDE_CMD` is:

```text
claude
```

and `CLAUDE_PLUGIN_DIR` points to this directory, the bridge automatically appends `--plugin-dir`.

If `CLAUDE_PROMPT_TEMPLATE` is:

```text
You are handling a Telegram remote task in {cwd}. Request from {user}: {prompt}
```

the bridge runs a command equivalent to:

```powershell
claude --plugin-dir C:\dev\claude-code\telegram "You are handling a Telegram remote task in C:\dev\claude-code. Request from alice: fix the failing tests..."
```

### Shell mode

Use `/run shell:<alias> [args]` to run a pre-approved command alias from `commands.json`.

Example:

```text
/run shell:git-status
/run shell:pytest tests/unit
```

Each alias in `commands.json` can control:

- executable and fixed arguments
- whether extra arguments are allowed
- timeout

## Quick Start

### 1. Create a Telegram bot

Use BotFather and get a `BOT_TOKEN`.

### 2. Find your Telegram user ID

Send a message to the bot, then open:

```text
https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
```

Find your `from.id` value in the returned JSON.

### 3. Prepare config files

```powershell
Copy-Item .\telegram\.env.example .\telegram\.env
Copy-Item .\telegram\commands.example.json .\telegram\commands.json
```

Then edit `telegram/.env`:

```env
BOT_TOKEN=123456:replace-me
ALLOWED_USER_IDS=123456789
WORKDIR=C:\dev\claude-code
CLAUDE_CMD=claude
CLAUDE_ARGS=
CLAUDE_PLUGIN_DIR=C:\dev\claude-code\telegram
CLAUDE_PROMPT_TEMPLATE=You are handling a Telegram remote task in {cwd}. Request from {user}: {prompt}
COMMANDS_FILE=C:\dev\claude-code\telegram\commands.json
LOG_FILE=C:\dev\claude-code\telegram\bridge.log
POLL_INTERVAL_SECONDS=2
MAX_OUTPUT_CHARS=3500
DEFAULT_TIMEOUT_SECONDS=900
```

On Linux, use Linux-style absolute paths instead of the Windows examples above:

```env
WORKDIR=/root/cc/claude_code_telegram_skill
CLAUDE_PLUGIN_DIR=/root/cc/claude_code_telegram_skill
COMMANDS_FILE=/root/cc/claude_code_telegram_skill/commands.json
LOG_FILE=/root/cc/claude_code_telegram_skill/bridge.log
```

### 4. Validate the plugin

```powershell
claude plugin validate .\telegram
```

### 5. Test the Claude Code plugin locally

```powershell
claude --plugin-dir .\telegram
```

Inside Claude Code, verify the plugin loaded:

```text
/help
/hooks
```

The plugin should expose:

- skill namespace: `/telegram-remote-control:telegram-remote-control`
- a visible `PreToolUse` hook in `/hooks`

### 6. Start the bridge

```powershell
python .\telegram\bridge.py
```

### 7. Send commands to the bot

```text
/help
/status
/run check whether the repo has uncommitted changes
/run shell:git-status
```

## Command Reference

### `/help`

Shows built-in help.

### `/status`

Shows bridge status, workdir, and configured shell aliases.

### `/run <text>`

Runs Claude mode. The text is wrapped into `CLAUDE_PROMPT_TEMPLATE` and passed to the local Claude CLI command.

### `/run shell:<alias> [args]`

Runs a configured alias from `commands.json`.

### `/tail`

Returns the tail of the most recent task output.

## Plugin Behavior

This plugin changes Claude Code behavior in two ways:

1. `skills/telegram-remote-control/` provides reusable remote-operation instructions.
2. `hooks/hooks.json` is auto-discovered by Claude Code and registers a `PreToolUse` hook that calls `scripts/pre_tool_guard.py`.

The current guard blocks:

- `rm -rf`
- `del /s` or `del /f`
- `Remove-Item -Recurse -Force`
- `git reset --hard`
- `git clean -fd`
- `git push --force` and `git push --force-with-lease`
- direct block-device writes
- disk formatting commands
- shutdown and reboot commands

## Security Notes

This plugin and bridge already do the following:

- accepts only allowlisted Telegram users
- blocks arbitrary shell execution
- limits shell execution to configured aliases
- runs one task at a time
- writes audit logs

You still need to evaluate and improve:

- Telegram account compromise risk
- the permission scope of the local Claude CLI
- sensitive path restrictions
- destructive command confirmation
- output redaction

For a stronger production setup, add these next:

1. explicit workspace allowlists
2. `/approve <task-id>` confirmation flow
3. command risk classes
4. webhook mode
5. Windows Service or systemd supervision
6. health checks and retry handling

## How Claude Loads It

For local testing and development, Claude Code loads this plugin directly from the filesystem:

```powershell
claude --plugin-dir .\telegram
```

According to the official Claude Code plugin docs, this is the standard local-development flow for plugin testing, and plugin root components such as `.claude-plugin/plugin.json`, `skills/`, and the standard `hooks/hooks.json` file are discovered automatically from that directory.

## Skill Usage

`telegram/skills/telegram-remote-control/` defines how Claude should behave when work is coming from a Telegram bridge:

- interpret remote task origin correctly
- keep progress messages short
- treat risky actions conservatively
- summarize long output
- produce Telegram-friendly completion messages

## Known Limits

- `bridge.py` assumes a callable local Claude CLI already exists
- the bridge uses Telegram `getUpdates` polling, not webhooks
- long output is truncated
- there is no multi-workspace scheduler

## Suggested Next Steps

1. install Claude Code CLI on this machine and verify `claude --version`
2. wire `CLAUDE_CMD` to the exact CLI invocation used locally
3. add a confirmation flow for risky aliases
4. expand the shell guard to enforce a workspace allowlist
5. run the bridge as a managed service
