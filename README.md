# Telegram Remote Claude Code Bridge

This directory provides a Telegram bridge for Claude Code CLI with persistent interactive sessions.

The design goal is CLI equivalence:

- Telegram bridge commands use a reserved `/tg-` prefix
- every other input is forwarded directly into a real running Claude Code CLI session
- Claude slash commands such as `/help`, `/model`, `/permissions`, `/mcp`, `/memory`, `/doctor`, `/clear`, `/compact`, `/review`, and others are sent to Claude unchanged

## Architecture

The bridge now runs in PTY mode on POSIX systems.

For each Telegram chat:

- the bridge maintains one active Claude session
- you can create multiple named Claude sessions
- Claude keeps its own internal interactive session state
- Telegram acts as a remote terminal layer

This is different from the previous stateless prompt-forwarding model. It is closer to using Claude Code directly in a terminal.

## Important Constraint

This bridge requires Linux or another POSIX environment because it proxies Claude through a pseudo-terminal.

It is not intended to run in Windows-native mode.

## What Gets Forwarded To Claude

Everything that does not start with `/tg-` is forwarded directly into the active Claude session.

Examples:

```text
Fix the failing tests in this repo.
/help
/model
/permissions
/mcp
/review
/clear
/compact
Continue from the previous result and focus on React state handling.
```

## What Stays In The Bridge

Only Telegram bridge management commands use the `/tg-` namespace.

### Bridge commands

```text
/tg-help
/tg-status
/tg-new [name]
/tg-use <name>
/tg-sessions
/tg-close [name]
/tg-restart [name]
/tg-tail
/tg-shell <alias> [args]
/tg-plugins list
/tg-plugins active
/tg-plugins add <alias> <path>
/tg-plugins remove <alias>
/tg-plugins enable <alias>
/tg-plugins disable <alias>
/tg-plugins clear
/tg-plugins validate <alias>
```

## Session Model

`/tg-new frontend-fix`

- starts a brand-new Claude CLI process
- binds it to the current Telegram chat
- makes it the active session

`/tg-use frontend-fix`

- switches the current chat to an already running session

`/tg-sessions`

- lists named Claude sessions for the current chat

`/tg-close frontend-fix`

- terminates that Claude process

`/tg-restart frontend-fix`

- restarts the Claude process for that session name

## Third-Party Skills And Plugins

Third-party skills should be provided as valid Claude Code plugin directories.

Register one:

```text
/tg-plugins add react-skill /root/skills/react-plugin
```

Enable it for the active session:

```text
/tg-plugins enable react-skill
```

Important:

- enabling or disabling an extra plugin restarts the active Claude session
- this is necessary because Claude plugin directories are applied at process startup

The bridge always loads the base Telegram plugin directory from `CLAUDE_PLUGIN_DIR`, and can append extra `--plugin-dir` flags for enabled plugin aliases.

## Shell Aliases

Shell aliases are still available, but they are now bridge-managed under `/tg-shell`.

Examples:

```text
/tg-shell git-status
/tg-shell pytest tests/unit
```

These aliases come from `commands.json`.

## Setup

### 1. Copy config files

```bash
cp .env.example .env
cp commands.example.json commands.json
```

### 2. Edit `.env`

Example Linux config:

```env
BOT_TOKEN=123456:replace-me
ALLOWED_USER_IDS=123456789
WORKDIR=/root/cc/claude_code_telegram_skill
CLAUDE_CMD=claude
CLAUDE_ARGS=
CLAUDE_PLUGIN_DIR=/root/cc/claude_code_telegram_skill
COMMANDS_FILE=/root/cc/claude_code_telegram_skill/commands.json
LOG_FILE=/root/cc/claude_code_telegram_skill/bridge.log
PLUGIN_REGISTRY_FILE=/root/cc/claude_code_telegram_skill/plugins.json
POLL_INTERVAL_SECONDS=2
MAX_OUTPUT_CHARS=3500
SESSION_IDLE_TIMEOUT_SECONDS=1.5
SESSION_COMMAND_TIMEOUT_SECONDS=120
SESSION_BUFFER_CHARS=60000
```

### 3. Validate the base Claude plugin

```bash
claude plugin validate /root/cc/claude_code_telegram_skill
```

### 4. Start the bridge

```bash
python ./bridge.py
```

## Telegram Workflow

### Start a named Claude session

```text
/tg-new frontend-fix
```

### Use Claude exactly like the CLI

```text
/help
/model
/permissions
Inspect the React entry points in this repo.
Continue, but focus on state management.
/review
```

### Add an external React skill/plugin

```text
/tg-plugins add react-skill /root/skills/react-plugin
/tg-plugins enable react-skill
```

### Run a bridge-managed shell alias

```text
/tg-shell git-status
```

## Behavior Notes

- The bridge does not parse or emulate Claude built-in slash commands. It forwards them to Claude.
- The bridge keeps the Claude process alive so built-in CLI state behaves like a real interactive session.
- Output is returned after the Claude session becomes idle for a short period.
- Very long output is truncated before being sent back to Telegram.

## Security Notes

- Telegram access is restricted to allowlisted user IDs
- shell execution is restricted to configured aliases
- risky shell actions inside Claude are still guarded by the plugin hook
- extra plugin directories must be explicitly registered before use

## Known Limits

- POSIX only
- if the bridge process restarts, live Claude sessions are lost
- plugin changes require session restart
- Telegram output is truncated for large terminal output
