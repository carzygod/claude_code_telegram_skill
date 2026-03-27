# Telegram Remote Control For Claude Code

This directory contains a minimal working scaffold for remotely driving a local Claude Code workflow through a Telegram bot.

This is not a "skill-only" solution. It is made of three parts:

1. `bridge.py`
   A local long-running bridge process. It polls Telegram messages, checks authorization, runs local commands, and sends results back.
2. `skill/`
   A Claude Code skill that defines the remote-control protocol, compact progress style, and safety boundaries for Telegram-originated work.
3. `.env.example` and `commands.example.json`
   Example configuration files. Copy them and replace the sample values with real values for your machine.

## Current MVP Features

- Telegram user allowlist
- `/help`, `/status`, `/run`, and `/tail`
- Claude prompt forwarding through a local CLI command template
- Shell command aliases through a whitelist file
- Local audit logging
- Single-task execution lock to avoid concurrent writes in one workspace

## Directory Layout

```text
telegram/
|- README.md
|- .env.example
|- commands.example.json
|- bridge.py
|- .gitignore
\- skill/
   |- SKILL.md
   |- agents/
   |  \- openai.yaml
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

and `CLAUDE_PROMPT_TEMPLATE` is:

```text
You are handling a Telegram remote task in {cwd}. Request from {user}: {prompt}
```

the bridge runs a command equivalent to:

```powershell
claude "You are handling a Telegram remote task in C:\dev\claude-code. Request from alice: fix the failing tests..."
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
CLAUDE_PROMPT_TEMPLATE=You are handling a Telegram remote task in {cwd}. Request from {user}: {prompt}
COMMANDS_FILE=C:\dev\claude-code\telegram\commands.json
LOG_FILE=C:\dev\claude-code\telegram\bridge.log
POLL_INTERVAL_SECONDS=2
MAX_OUTPUT_CHARS=3500
DEFAULT_TIMEOUT_SECONDS=900
```

### 4. Start the bridge

```powershell
python .\telegram\bridge.py
```

### 5. Send commands to the bot

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

## Security Notes

This MVP already does the following:

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

## Skill Usage

`telegram/skill/` is the Claude Code skill draft. It does not listen to Telegram by itself. It defines how Claude should behave when work is coming from a Telegram bridge:

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

1. wire `CLAUDE_CMD` to the exact Claude CLI invocation used on your machine
2. tighten argument validation for `commands.json`
3. add a confirmation flow for risky aliases
4. run the bridge as a managed service
