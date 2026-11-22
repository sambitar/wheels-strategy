"""The in-app chat: a thin, streaming bridge to `opencode run`.

The bubble in the web UI talks to the `wheels-chat` agent defined in
`.opencode/agents/wheels-chat.md`. This module owns only the transport: find
the opencode CLI, run one prompt with `--format json`, and hand back the
line-delimited events it prints. All the financial reasoning — and every
number — still comes from the engine, through the agent's own
`uv run wheel --json …` calls.

No API key lives here and nothing is stored: the browser holds the opencode
session id, and each turn resumes it.
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from collections.abc import Iterator
from pathlib import Path

from .. import config

AGENT = "wheels-chat"
DEFAULT_TIMEOUT = 180.0

# Where a normal install puts the CLI. `which` covers the rest.
_BIN_CANDIDATES = (
    "~/.opencode/bin/opencode",
    "/opt/homebrew/bin/opencode",
    "/usr/local/bin/opencode",
    "~/.local/bin/opencode",
)

# opencode ids look like `ses_dff4b3cddffeCGjuuEfNxo6VwI`. Browsers only ever
# echo back what the server sent, but validate before handing it to a process.
_SESSION_RE = re.compile(r"^ses_[A-Za-z0-9]+$")

_TOOL_LABELS = {
    "shell": "checking the engine",
    "read": "reading the project",
    "grep": "reading the project",
    "glob": "reading the project",
    "skill": "consulting the playbook",
    "subagent": "asking a specialist",
    "websearch": "looking that up",
    "webfetch": "looking that up",
    "todowrite": "organising the reply",
}


def resolve_bin() -> str | None:
    """Find the opencode CLI, explicit setting first.

    `WHEELS_OPENCODE_BIN` is the escape hatch for a binary that is not on PATH
    (a downloaded single-file build, for instance).
    """
    for env in ("WHEELS_OPENCODE_BIN", "OPENCODE_BIN_PATH"):
        raw = os.environ.get(env, "").strip()
        if raw:
            return str(Path(raw).expanduser())
    found = shutil.which("opencode")
    if found:
        return found
    for candidate in _BIN_CANDIDATES:
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def valid_session(value: object) -> bool:
    return isinstance(value, str) and bool(_SESSION_RE.fullmatch(value))


def build_command(binary: str, message: str, session: str | None) -> list[str]:
    cmd = [
        binary,
        "run",
        "--agent",
        AGENT,
        "--format",
        "json",
        "--title",
        "Wheels chat",
    ]
    if session:
        cmd += ["--session", session]
    # The message is variadic and last, so no flag can be swallowed by it.
    cmd.append(message)
    return cmd


def status() -> dict:
    binary = resolve_bin()
    agent_file = config.ROOT / ".opencode" / "agents" / f"{AGENT}.md"
    if binary is None:
        hint = (
            "The opencode CLI was not found. Install it, or set "
            "WHEELS_OPENCODE_BIN to its path, then reload this page."
        )
    elif not agent_file.is_file():
        hint = (
            f"The agent definition is missing at {agent_file}. "
            "Run the app from this project's checkout."
        )
    else:
        hint = None
    return {
        "available": binary is not None and agent_file.is_file(),
        "binary": binary,
        "agent": AGENT,
        "agent_file": str(agent_file) if agent_file.is_file() else None,
        "hint": hint,
    }


def stream(message: str, session: str | None = None,
           timeout: float = DEFAULT_TIMEOUT) -> Iterator[tuple[str, dict]]:
    """Run one prompt and yield `(event, payload)` pairs as the agent replies.

    Events: `session` (once, the id to resume), `status`, `tool`, `delta`,
    `error`, `done`. The subprocess is killed if the caller stops iterating,
    which is what happens when the browser closes the stream.
    """
    binary = resolve_bin()
    if binary is None:
        yield "error", {
            "message": "The opencode CLI was not found. Install it, or set "
            "WHEELS_OPENCODE_BIN to its path, then reload the page."
        }
        yield "done", {"ok": False}
        return

    cmd = build_command(binary, message, session)
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(config.ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        yield "error", {"message": f"Could not start opencode ({binary}): {exc}"}
        yield "done", {"ok": False}
        return

    lines: queue.Queue[str | None] = queue.Queue()
    stderr_tail: deque[str] = deque(maxlen=8)

    def pump(pipe, sink, collect: bool = False) -> None:
        try:
            for line in pipe:
                if collect:
                    stderr_tail.append(line.rstrip())
                else:
                    sink.put(line)
        finally:
            if not collect:
                sink.put(None)

    reader = threading.Thread(target=pump, args=(proc.stdout, lines), daemon=True)
    errors = threading.Thread(target=pump, args=(proc.stderr, None, True), daemon=True)
    reader.start()
    errors.start()

    deadline = time.monotonic() + timeout
    seen_session = bool(session)
    failed = False
    timed_out = False
    code: int | None = None

    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            try:
                line = lines.get(timeout=min(1.0, remaining))
            except queue.Empty:
                continue
            if line is None:
                break
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue

            session_id = event.get("sessionID")
            if isinstance(session_id, str) and session_id and not seen_session:
                seen_session = True
                yield "session", {"session": session_id}

            kind = event.get("type")
            part = event.get("part") if isinstance(event.get("part"), dict) else {}
            if kind == "text":
                text = part.get("text")
                if isinstance(text, str) and text:
                    yield "delta", {"text": text}
            elif kind == "tool_use":
                tool = str(part.get("tool") or "")
                yield "tool", {"label": _TOOL_LABELS.get(tool, "working")}
            elif kind == "retry":
                yield "status", {"label": "retrying"}
            elif kind == "error":
                failed = True
                detail = event.get("error") if isinstance(event.get("error"), dict) else {}
                yield "error", {"message": str(detail.get("message") or "The agent stopped.")}

        if timed_out:
            yield "error", {
                "message": f"The agent did not finish within {int(timeout)}s. "
                "Try a narrower question, or ask again."
            }
        try:
            code = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            code = None
        if not failed and not timed_out and code not in (0, None):
            failed = True
            yield "error", {"message": _exit_message(code, binary, stderr_tail)}
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        reader.join(timeout=1)
        errors.join(timeout=1)
        for pipe in (proc.stdout, proc.stderr):
            try:
                pipe.close()
            except Exception:  # noqa: BLE001 - closing a dead pipe is best effort
                pass

    yield "done", {"ok": not failed and not timed_out}


def _exit_message(code: int, binary: str, stderr_tail: deque[str]) -> str:
    if code in (-9, 137):
        return (
            f"macOS killed opencode ({binary}) before it could answer. A downloaded "
            "binary may still be quarantined; run "
            f"`xattr -d com.apple.quarantine {binary}` once, then try again."
        )
    detail = " ".join(line for line in stderr_tail if line).strip()
    suffix = f" — {detail[-400:]}" if detail else ""
    return f"opencode exited with code {code}{suffix}"
