from __future__ import annotations

import json
import logging
import os
import pty
import re
import select
import shlex
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
OSC_RE = re.compile(r"\x1b\].*?(?:\x07|\x1b\\)")
NONPRINT_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
LINE_ART_RE = re.compile(r"^[\s\-\|╭╮╰╯│─┌┐└┘▐▛▜▌▝▘█◐?·]+$")
JUNK_SUBSTRINGS = (
    "]0;",
    "claudecodev",
    "tipsforgettingstarted",
    "welcomeback",
    "recentactivity",
    "norecentactivity",
    "apiusagebilling",
    "forshortcuts",
    "/effort",
    "medium/effort",
    "[>0q",
    "ctrl+gtoeditinvim",
)


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def strip_ansi(text: str) -> str:
    text = OSC_RE.sub("", text)
    text = ANSI_RE.sub("", text)
    text = text.replace("\r", "\n")
    text = NONPRINT_RE.sub("", text)
    return text


def normalize_terminal_output(text: str) -> str:
    text = strip_ansi(text)
    text = text.replace("❯", "\n")
    raw_lines = [line.strip() for line in text.splitlines()]
    cleaned: list[str] = []
    for line in raw_lines:
        if not line:
            if cleaned and cleaned[-1] != "":
                cleaned.append("")
            continue
        compact = "".join(ch for ch in line if not ch.isspace())
        if not compact:
            continue
        lower = compact.lower()
        if any(token in lower for token in JUNK_SUBSTRINGS):
            continue
        if LINE_ART_RE.match(line):
            continue
        if len(set(compact)) <= 3 and len(compact) > 8:
            continue
        cleaned.append(line)

    collapsed: list[str] = []
    for line in cleaned:
        if line == "" and collapsed and collapsed[-1] == "":
            continue
        collapsed.append(line)
    return "\n".join(collapsed).strip()


def remove_input_echo(output: str, sent_text: str) -> str:
    if not output:
        return output
    sent = sent_text.strip()
    if not sent:
        return output
    lines = output.splitlines()
    cleaned: list[str] = []
    stripped_once = False
    for line in lines:
        compact = "".join(ch for ch in line.split())
        if not stripped_once and compact == "".join(ch for ch in sent.split()):
            stripped_once = True
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


@dataclass
class CommandSpec:
    argv: list[str]
    allow_extra_args: bool = False
    timeout_seconds: int | None = None


@dataclass
class ClaudeSession:
    name: str
    process: subprocess.Popen[bytes]
    master_fd: int
    plugin_aliases: list[str]
    buffer: str = ""
    output_seq: int = 0
    last_activity: float = field(default_factory=time.monotonic)
    reader_thread: threading.Thread | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    condition: threading.Condition = field(default_factory=lambda: threading.Condition(threading.Lock()))


@dataclass
class ChatState:
    active_session: str | None = None
    sessions: dict[str, ClaudeSession] = field(default_factory=dict)


class TelegramBridge:
    def __init__(self, base_dir: Path) -> None:
        if os.name != "posix":
            raise RuntimeError("This bridge requires a POSIX system because Claude CLI is proxied through a PTY.")

        load_dotenv(base_dir / ".env")
        self.base_dir = base_dir
        self.bot_token = self.require_env("BOT_TOKEN")
        self.allowed_user_ids = {
            int(item.strip())
            for item in self.require_env("ALLOWED_USER_IDS").split(",")
            if item.strip()
        }
        self.workdir = Path(os.environ.get("WORKDIR", str(Path.cwd()))).resolve()
        self.claude_cmd = shlex.split(self.require_env("CLAUDE_CMD"))
        self.claude_args = shlex.split(os.environ.get("CLAUDE_ARGS", ""))
        self.base_plugin_dir = Path(os.environ.get("CLAUDE_PLUGIN_DIR", str(base_dir))).resolve()
        self.commands_file = Path(os.environ.get("COMMANDS_FILE", str(base_dir / "commands.json")))
        self.log_file = Path(os.environ.get("LOG_FILE", str(base_dir / "bridge.log")))
        self.plugin_registry_file = Path(os.environ.get("PLUGIN_REGISTRY_FILE", str(base_dir / "plugins.json")))
        self.poll_interval = float(os.environ.get("POLL_INTERVAL_SECONDS", "2"))
        self.max_output_chars = int(os.environ.get("MAX_OUTPUT_CHARS", "3500"))
        self.idle_timeout_seconds = float(os.environ.get("SESSION_IDLE_TIMEOUT_SECONDS", "1.5"))
        self.command_timeout_seconds = float(os.environ.get("SESSION_COMMAND_TIMEOUT_SECONDS", "120"))
        self.min_response_wait_seconds = float(os.environ.get("MIN_RESPONSE_WAIT_SECONDS", "4.0"))
        self.session_buffer_chars = int(os.environ.get("SESSION_BUFFER_CHARS", "60000"))

        self.api_base = f"https://api.telegram.org/bot{self.bot_token}"
        self.offset = 0
        self.state_lock = threading.Lock()
        self.commands = self.load_commands()
        self.plugin_registry = self.load_plugin_registry()
        self.chats: dict[int, ChatState] = {}
        self.last_output = "No session output yet."

        self.configure_logging()

    def require_env(self, key: str) -> str:
        value = os.environ.get(key)
        if not value:
            raise RuntimeError(f"Missing required environment variable: {key}")
        return value

    def configure_logging(self) -> None:
        if self.log_file.exists() and self.log_file.is_dir():
            self.log_file = self.log_file / "bridge.log"
        elif self.log_file.suffix == "":
            self.log_file.mkdir(parents=True, exist_ok=True)
            self.log_file = self.log_file / "bridge.log"
        else:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            filename=self.log_file,
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
            encoding="utf-8",
        )

    def load_commands(self) -> dict[str, CommandSpec]:
        if not self.commands_file.exists():
            return {}
        raw = json.loads(self.commands_file.read_text(encoding="utf-8"))
        commands: dict[str, CommandSpec] = {}
        for alias, spec in raw.items():
            argv = spec.get("argv")
            if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
                raise RuntimeError(f"Invalid argv for alias: {alias}")
            commands[alias] = CommandSpec(
                argv=argv,
                allow_extra_args=bool(spec.get("allow_extra_args", False)),
                timeout_seconds=int(spec["timeout_seconds"]) if spec.get("timeout_seconds") is not None else None,
            )
        return commands

    def load_plugin_registry(self) -> dict[str, str]:
        if not self.plugin_registry_file.exists():
            return {}
        raw = json.loads(self.plugin_registry_file.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        return {alias: path for alias, path in raw.items() if isinstance(alias, str) and isinstance(path, str)}

    def save_plugin_registry(self) -> None:
        self.plugin_registry_file.parent.mkdir(parents=True, exist_ok=True)
        self.plugin_registry_file.write_text(
            json.dumps(self.plugin_registry, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def api_request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        query = urllib.parse.urlencode(params)
        url = f"{self.api_base}/{method}"
        if query:
            url = f"{url}?{query}"
        with urllib.request.urlopen(url, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram API error on {method}: {payload}")
        return payload

    def send_message(self, chat_id: int, text: str) -> None:
        safe_text = text if len(text) <= 4096 else text[:4000] + "\n...[truncated]"
        self.api_request("sendMessage", {"chat_id": chat_id, "text": safe_text})

    def get_updates(self) -> list[dict[str, Any]]:
        payload = self.api_request("getUpdates", {"offset": self.offset, "timeout": 25})
        return payload.get("result", [])

    def get_chat_state(self, chat_id: int) -> ChatState:
        with self.state_lock:
            return self.chats.setdefault(chat_id, ChatState())

    def resolve_plugin_path(self, alias: str) -> Path:
        path = self.plugin_registry.get(alias)
        if path is None:
            raise ValueError(f"Unknown plugin alias: {alias}")
        resolved = Path(path).expanduser().resolve()
        if not resolved.exists():
            raise ValueError(f"Plugin path does not exist: {resolved}")
        return resolved

    def build_claude_argv(self, plugin_aliases: list[str]) -> list[str]:
        argv = [*self.claude_cmd, *self.claude_args]
        if "--plugin-dir" not in argv:
            argv.extend(["--plugin-dir", str(self.base_plugin_dir)])
        for alias in plugin_aliases:
            argv.extend(["--plugin-dir", str(self.resolve_plugin_path(alias))])
        return argv

    def _reader_loop(self, session: ClaudeSession) -> None:
        try:
            while True:
                if session.process.poll() is not None:
                    ready, _, _ = select.select([session.master_fd], [], [], 0)
                    if not ready:
                        break
                ready, _, _ = select.select([session.master_fd], [], [], 0.5)
                if not ready:
                    continue
                chunk = os.read(session.master_fd, 4096)
                if not chunk:
                    break
                text = chunk.decode("utf-8", errors="replace")
                with session.condition:
                    session.buffer = (session.buffer + text)[-self.session_buffer_chars :]
                    session.output_seq += 1
                    session.last_activity = time.monotonic()
                    session.condition.notify_all()
        except OSError:
            pass
        finally:
            with session.condition:
                session.output_seq += 1
                session.condition.notify_all()

    def create_session(self, chat_id: int, name: str, plugin_aliases: list[str] | None = None) -> tuple[ClaudeSession, str]:
        plugin_aliases = list(plugin_aliases or [])
        master_fd, slave_fd = pty.openpty()
        argv = self.build_claude_argv(plugin_aliases)
        process = subprocess.Popen(
            argv,
            cwd=str(self.workdir),
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            shell=False,
            close_fds=True,
        )
        os.close(slave_fd)
        session = ClaudeSession(
            name=name,
            process=process,
            master_fd=master_fd,
            plugin_aliases=plugin_aliases,
        )
        thread = threading.Thread(target=self._reader_loop, args=(session,), daemon=True)
        session.reader_thread = thread
        thread.start()
        chat = self.get_chat_state(chat_id)
        with self.state_lock:
            old = chat.sessions.get(name)
            if old is not None:
                self.close_session_runtime(old)
            chat.sessions[name] = session
            chat.active_session = name
        banner = self.collect_output(session, start_seq=0, timeout=15)
        return session, self.normalize_banner(name, banner)

    def normalize_banner(self, name: str, banner: str) -> str:
        cleaned = normalize_terminal_output(banner)
        if not cleaned:
            return f"Started Claude session '{name}'."
        return cleaned[-self.max_output_chars :]

    def close_session_runtime(self, session: ClaudeSession) -> None:
        try:
            if session.process.poll() is None:
                session.process.terminate()
                try:
                    session.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    session.process.kill()
        finally:
            try:
                os.close(session.master_fd)
            except OSError:
                pass

    def get_active_session(self, chat_id: int) -> ClaudeSession | None:
        chat = self.get_chat_state(chat_id)
        if chat.active_session is None:
            return None
        session = chat.sessions.get(chat.active_session)
        if session and session.process.poll() is not None:
            return None
        return session

    def ensure_active_session(self, chat_id: int) -> tuple[ClaudeSession, str]:
        session = self.get_active_session(chat_id)
        if session is not None:
            return session, ""
        return self.create_session(chat_id, "default")

    def collect_output(self, session: ClaudeSession, start_seq: int, timeout: float | None = None) -> str:
        timeout = timeout or self.command_timeout_seconds
        start_len = len(session.buffer)
        deadline = time.monotonic() + timeout
        min_wait_deadline = time.monotonic() + self.min_response_wait_seconds
        last_change = time.monotonic()
        seen_change = False
        with session.condition:
            current_seq = start_seq
            while time.monotonic() < deadline:
                if session.output_seq != current_seq:
                    current_seq = session.output_seq
                    seen_change = True
                    last_change = time.monotonic()
                if (
                    seen_change
                    and time.monotonic() >= min_wait_deadline
                    and (time.monotonic() - last_change) >= self.idle_timeout_seconds
                ):
                    break
                if session.process.poll() is not None and not seen_change:
                    break
                session.condition.wait(timeout=0.2)
        return normalize_terminal_output(session.buffer[start_len:])

    def send_to_claude(self, session: ClaudeSession, text: str) -> str:
        with session.lock:
            baseline = session.output_seq
            os.write(session.master_fd, (text.rstrip("\n") + "\n").encode("utf-8"))
            output = self.collect_output(session, baseline)
        translated = remove_input_echo(normalize_terminal_output(output), text)
        self.last_output = translated[-self.max_output_chars :] if translated else "(no output)"
        return self.last_output

    def run_alias(self, alias: str, extra_args: list[str]) -> str:
        spec = self.commands.get(alias)
        if spec is None:
            raise ValueError(f"Unknown shell alias: {alias}")
        if extra_args and not spec.allow_extra_args:
            raise ValueError(f"Alias does not accept extra args: {alias}")
        proc = subprocess.run(
            [*spec.argv, *extra_args],
            cwd=str(self.workdir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=spec.timeout_seconds or self.command_timeout_seconds,
            shell=False,
        )
        combined = ((proc.stdout or "") + ("\n" if proc.stdout and proc.stderr else "") + (proc.stderr or "")).strip()
        output = combined or "(no output)"
        self.last_output = output[-self.max_output_chars :]
        return f"Exit code: {proc.returncode}\n\n{self.last_output}"

    def format_help(self) -> str:
        return (
            "Telegram bridge commands:\n"
            "/tg-help\n"
            "/tg-status\n"
            "/tg-new [name]\n"
            "/tg-use <name>\n"
            "/tg-sessions\n"
            "/tg-close [name]\n"
            "/tg-restart [name]\n"
            "/tg-tail\n"
            "/tg-plugins list|active|add <alias> <path>|remove <alias>|enable <alias>|disable <alias>|clear|validate <alias>\n"
            "/tg-shell <alias> [args]\n"
            "Any other text, including Claude slash commands like /help or /model, is forwarded directly into the active Claude CLI session."
        )

    def format_status(self, chat_id: int) -> str:
        chat = self.get_chat_state(chat_id)
        active = chat.active_session or "(none)"
        sessions = ", ".join(sorted(chat.sessions)) if chat.sessions else "(none)"
        return (
            "Bridge is online.\n"
            f"Workdir: {self.workdir}\n"
            f"Active session: {active}\n"
            f"Sessions: {sessions}\n"
            f"Registered plugin aliases: {', '.join(sorted(self.plugin_registry)) or '(none)'}"
        )

    def handle_tg_plugins(self, chat_id: int, text: str) -> str:
        parts = shlex.split(text)
        chat = self.get_chat_state(chat_id)
        if len(parts) == 2 and parts[1] == "list":
            if not self.plugin_registry:
                return "No extra plugin aliases registered."
            return "\n".join(f"{alias}: {path}" for alias, path in sorted(self.plugin_registry.items()))
        if len(parts) == 2 and parts[1] == "active":
            session = self.get_active_session(chat_id)
            aliases = session.plugin_aliases if session else []
            return "Active extra plugins: " + (", ".join(aliases) if aliases else "(none)")
        if len(parts) >= 4 and parts[1] == "add":
            alias = parts[2]
            path = str(Path(parts[3]).expanduser().resolve())
            self.plugin_registry[alias] = path
            self.save_plugin_registry()
            return f"Registered plugin alias '{alias}' -> {path}"
        if len(parts) >= 3 and parts[1] == "remove":
            alias = parts[2]
            if alias not in self.plugin_registry:
                return f"Unknown plugin alias: {alias}"
            self.plugin_registry.pop(alias)
            self.save_plugin_registry()
            for session in chat.sessions.values():
                session.plugin_aliases = [item for item in session.plugin_aliases if item != alias]
            return f"Removed plugin alias '{alias}'."
        if len(parts) >= 3 and parts[1] in {"enable", "disable"}:
            session = self.get_active_session(chat_id)
            if session is None:
                return "No active Claude session. Create one first with /tg-new."
            alias = parts[2]
            if parts[1] == "enable":
                self.resolve_plugin_path(alias)
                aliases = list(session.plugin_aliases)
                if alias not in aliases:
                    aliases.append(alias)
            else:
                aliases = [item for item in session.plugin_aliases if item != alias]
            name = session.name
            self.close_session_runtime(session)
            with self.state_lock:
                chat.sessions.pop(name, None)
            _, banner = self.create_session(chat_id, name, aliases)
            return f"Restarted session '{name}' with plugins: {', '.join(aliases) or '(none)'}\n\n{banner[-2000:]}"
        if len(parts) == 2 and parts[1] == "clear":
            session = self.get_active_session(chat_id)
            if session is None:
                return "No active Claude session."
            name = session.name
            self.close_session_runtime(session)
            with self.state_lock:
                chat.sessions.pop(name, None)
            _, banner = self.create_session(chat_id, name, [])
            return f"Restarted session '{name}' with no extra plugins.\n\n{banner[-2000:]}"
        if len(parts) >= 3 and parts[1] == "validate":
            path = self.resolve_plugin_path(parts[2])
            return f"Plugin alias '{parts[2]}' resolved to {path}"
        return "Usage: /tg-plugins list|active|add <alias> <path>|remove <alias>|enable <alias>|disable <alias>|clear|validate <alias>"

    def handle_bridge_command(self, chat_id: int, text: str) -> str:
        parts = shlex.split(text)
        if parts[0] == "/tg-help":
            return self.format_help()
        if parts[0] == "/tg-status":
            return self.format_status(chat_id)
        if parts[0] == "/tg-tail":
            return self.last_output
        if parts[0] == "/tg-new":
            name = parts[1] if len(parts) >= 2 else "default"
            _, banner = self.create_session(chat_id, name)
            return banner[-self.max_output_chars :]
        if parts[0] == "/tg-use":
            if len(parts) < 2:
                return "Usage: /tg-use <name>"
            chat = self.get_chat_state(chat_id)
            session = chat.sessions.get(parts[1])
            if session is None or session.process.poll() is not None:
                return f"Unknown or closed session: {parts[1]}"
            chat.active_session = parts[1]
            return f"Switched to session '{parts[1]}'."
        if parts[0] == "/tg-sessions":
            chat = self.get_chat_state(chat_id)
            if not chat.sessions:
                return "No Claude sessions yet."
            lines = []
            for name, session in sorted(chat.sessions.items()):
                marker = "*" if chat.active_session == name else "-"
                status = "running" if session.process.poll() is None else "closed"
                plugins = ", ".join(session.plugin_aliases) if session.plugin_aliases else "(none)"
                lines.append(f"{marker} {name}: {status}; plugins={plugins}")
            return "\n".join(lines)
        if parts[0] == "/tg-close":
            chat = self.get_chat_state(chat_id)
            name = parts[1] if len(parts) >= 2 else chat.active_session
            if not name:
                return "No active session."
            session = chat.sessions.get(name)
            if session is None:
                return f"Unknown session: {name}"
            self.close_session_runtime(session)
            with self.state_lock:
                chat.sessions.pop(name, None)
                if chat.active_session == name:
                    chat.active_session = next(iter(chat.sessions), None)
            return f"Closed session '{name}'."
        if parts[0] == "/tg-restart":
            chat = self.get_chat_state(chat_id)
            name = parts[1] if len(parts) >= 2 else chat.active_session
            if not name:
                return "No active session."
            session = chat.sessions.get(name)
            aliases = session.plugin_aliases if session else []
            if session is not None:
                self.close_session_runtime(session)
                with self.state_lock:
                    chat.sessions.pop(name, None)
            _, banner = self.create_session(chat_id, name, aliases)
            return banner[-self.max_output_chars :]
        if parts[0] == "/tg-shell":
            if len(parts) < 2:
                return "Usage: /tg-shell <alias> [args]"
            return self.run_alias(parts[1], parts[2:])
        if parts[0] == "/tg-plugins":
            return self.handle_tg_plugins(chat_id, text)
        return "Unknown bridge command. Use /tg-help."

    def authorize(self, message: dict[str, Any]) -> bool:
        from_user = message.get("from") or {}
        user_id = from_user.get("id")
        return isinstance(user_id, int) and user_id in self.allowed_user_ids

    def process_message(self, message: dict[str, Any]) -> None:
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if not isinstance(chat_id, int):
            return
        if not self.authorize(message):
            self.send_message(chat_id, "Access denied.")
            return
        text = (message.get("text") or "").strip()
        if not text:
            self.send_message(chat_id, "Only text commands are supported.")
            return
        try:
            if text.startswith("/tg-"):
                self.send_message(chat_id, self.handle_bridge_command(chat_id, text))
                return
            session, banner = self.ensure_active_session(chat_id)
            if banner:
                self.send_message(chat_id, banner[-self.max_output_chars :])
            output = self.send_to_claude(session, text)
            self.send_message(chat_id, output or "(no output)")
        except Exception as exc:
            logging.exception("Failed to process message")
            self.send_message(chat_id, f"Task failed: {exc}")

    def serve_forever(self) -> None:
        logging.info("Bridge started in %s", self.workdir)
        while True:
            try:
                updates = self.get_updates()
                for item in updates:
                    self.offset = max(self.offset, item["update_id"] + 1)
                    message = item.get("message")
                    if message:
                        self.process_message(message)
            except Exception:
                logging.exception("Polling loop failed")
            time.sleep(self.poll_interval)


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    bridge = TelegramBridge(base_dir)
    print(f"Telegram bridge started. Workdir={bridge.workdir}")
    bridge.serve_forever()


if __name__ == "__main__":
    main()
