from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


@dataclass
class CommandSpec:
    argv: list[str]
    allow_extra_args: bool = False
    timeout_seconds: int | None = None


class TelegramBridge:
    def __init__(self, base_dir: Path) -> None:
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
        plugin_dir_value = os.environ.get("CLAUDE_PLUGIN_DIR", str(base_dir))
        self.claude_plugin_dir = Path(plugin_dir_value).resolve()
        self.claude_prompt_template = self.require_env("CLAUDE_PROMPT_TEMPLATE")
        self.commands_file = Path(
            os.environ.get("COMMANDS_FILE", str(base_dir / "commands.json"))
        )
        self.log_file = Path(os.environ.get("LOG_FILE", str(base_dir / "bridge.log")))
        self.poll_interval = float(os.environ.get("POLL_INTERVAL_SECONDS", "2"))
        self.max_output_chars = int(os.environ.get("MAX_OUTPUT_CHARS", "3500"))
        self.default_timeout = int(os.environ.get("DEFAULT_TIMEOUT_SECONDS", "900"))

        self.api_base = f"https://api.telegram.org/bot{self.bot_token}"
        self.offset = 0
        self.busy_lock = threading.Lock()
        self.last_output = "No task has run yet."
        self.commands = self.load_commands()

        self.configure_logging()

    def require_env(self, key: str) -> str:
        value = os.environ.get(key)
        if not value:
            raise RuntimeError(f"Missing required environment variable: {key}")
        return value

    def configure_logging(self) -> None:
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
            if not isinstance(argv, list) or not argv or not all(
                isinstance(item, str) for item in argv
            ):
                raise RuntimeError(f"Invalid argv for alias: {alias}")
            commands[alias] = CommandSpec(
                argv=argv,
                allow_extra_args=bool(spec.get("allow_extra_args", False)),
                timeout_seconds=(
                    int(spec["timeout_seconds"])
                    if spec.get("timeout_seconds") is not None
                    else None
                ),
            )
        return commands

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
        payload = self.api_request(
            "getUpdates",
            {"offset": self.offset, "timeout": 25},
        )
        return payload.get("result", [])

    def format_help(self) -> str:
        aliases = ", ".join(sorted(self.commands)) if self.commands else "(none)"
        return (
            "Commands:\n"
            "/help - show this help\n"
            "/status - bridge status\n"
            "/run <prompt> - send a prompt to local Claude CLI\n"
            "/run shell:<alias> [args] - run a whitelisted local command\n"
            "/tail - show the tail of the last task output\n"
            f"Shell aliases: {aliases}"
        )

    def format_status(self) -> str:
        aliases = ", ".join(sorted(self.commands)) if self.commands else "(none)"
        return (
            "Bridge is online.\n"
            f"Workdir: {self.workdir}\n"
            f"Allowed users: {len(self.allowed_user_ids)}\n"
            f"Shell aliases: {aliases}\n"
            f"Busy: {'yes' if self.busy_lock.locked() else 'no'}"
        )

    def run_subprocess(self, argv: list[str], timeout: int | None) -> tuple[int, str]:
        proc = subprocess.run(
            argv,
            cwd=str(self.workdir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout or self.default_timeout,
            shell=False,
        )
        combined = (proc.stdout or "") + ("\n" if proc.stdout and proc.stderr else "") + (proc.stderr or "")
        return proc.returncode, combined.strip() or "(no output)"

    def run_claude_prompt(self, prompt: str, user_label: str) -> tuple[int, str]:
        rendered = self.claude_prompt_template.format(
            cwd=str(self.workdir),
            prompt=prompt,
            user=user_label,
        )
        argv = [*self.claude_cmd, *self.claude_args]
        if "--plugin-dir" not in argv:
            argv.extend(["--plugin-dir", str(self.claude_plugin_dir)])
        argv.append(rendered)
        return self.run_subprocess(argv, self.default_timeout)

    def run_alias(self, alias: str, extra_args: list[str]) -> tuple[int, str]:
        spec = self.commands.get(alias)
        if spec is None:
            raise ValueError(f"Unknown shell alias: {alias}")
        if extra_args and not spec.allow_extra_args:
            raise ValueError(f"Alias does not accept extra args: {alias}")
        argv = [*spec.argv, *extra_args]
        return self.run_subprocess(argv, spec.timeout_seconds)

    def handle_run(self, command_text: str, user_label: str) -> str:
        payload = command_text.strip()
        if not payload:
            return "Usage: /run <prompt> or /run shell:<alias> [args]"

        if payload.startswith("shell:"):
            shell_command = payload[len("shell:") :].strip()
            parts = shlex.split(shell_command)
            if not parts:
                return "Usage: /run shell:<alias> [args]"
            alias, *extra_args = parts
            code, output = self.run_alias(alias, extra_args)
        else:
            code, output = self.run_claude_prompt(payload, user_label)

        trimmed = output[-self.max_output_chars :]
        self.last_output = trimmed
        return f"Exit code: {code}\n\n{trimmed}"

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
            logging.warning("Denied access for message: %s", message)
            return

        text = (message.get("text") or "").strip()
        from_user = message.get("from") or {}
        user_label = (
            from_user.get("username")
            or from_user.get("first_name")
            or str(from_user.get("id", "unknown"))
        )

        if not text:
            self.send_message(chat_id, "Only text commands are supported.")
            return

        if text == "/help" or text == "/start":
            self.send_message(chat_id, self.format_help())
            return
        if text == "/status":
            self.send_message(chat_id, self.format_status())
            return
        if text == "/tail":
            self.send_message(chat_id, self.last_output)
            return
        if not text.startswith("/run"):
            self.send_message(chat_id, "Unknown command. Use /help.")
            return

        if not self.busy_lock.acquire(blocking=False):
            self.send_message(chat_id, "A task is already running. Wait for it to finish.")
            return

        try:
            self.send_message(chat_id, "Task accepted. Running on the local machine.")
            run_text = text[len("/run") :].strip()
            logging.info("Running task from %s: %s", user_label, run_text)
            result = self.handle_run(run_text, user_label)
            self.send_message(chat_id, result)
        except subprocess.TimeoutExpired:
            self.last_output = "Task timed out."
            self.send_message(chat_id, "Task timed out.")
            logging.exception("Task timed out")
        except Exception as exc:
            self.last_output = f"Task failed: {exc}"
            self.send_message(chat_id, f"Task failed: {exc}")
            logging.exception("Task failed")
        finally:
            self.busy_lock.release()

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
