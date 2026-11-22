"""Chat transport tests.

No model and no network: a stand-in executable prints the same line-delimited
JSON that `opencode run --format json` prints, so these check the contract the
bubble depends on — argv, session capture, streaming, and failure messages.
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request

import pytest

from wheel import config as config_mod
from wheel import ledger
from wheel.web import chat
from wheel.web import server as webserver

FAKE_AGENT = """#!{python}
import json, os, sys, time

log = os.environ.get("FAKE_ARGV_LOG")
if log:
    with open(log, "w") as fh:
        json.dump(sys.argv[1:], fh)

mode = os.environ.get("FAKE_MODE", "ok")
if mode == "crash":
    print("error: agent not found", file=sys.stderr, flush=True)
    sys.exit(2)

events = [
    {{"type": "step_start", "sessionID": "ses_test0123", "part": {{}}}},
    {{"type": "tool_use", "sessionID": "ses_test0123", "part": {{"tool": "skill"}}}},
    {{"type": "tool_use", "sessionID": "ses_test0123", "part": {{"tool": "shell"}}}},
    {{"type": "text", "sessionID": "ses_test0123", "part": {{"text": "Free cash is "}}}},
    {{"type": "text", "sessionID": "ses_test0123", "part": {{"text": "**$50**."}}}},
]
for event in events:
    print(json.dumps(event), flush=True)

if mode == "hang":
    time.sleep(30)
"""


@pytest.fixture
def fake_bin(tmp_path, monkeypatch):
    path = tmp_path / "opencode"
    path.write_text(FAKE_AGENT.format(python=sys.executable))
    path.chmod(0o755)
    monkeypatch.setenv("WHEELS_OPENCODE_BIN", str(path))
    return path


@pytest.fixture
def live_server(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.toml"
    seed = config_mod.Config(symbols=["FIG"], budget=2000.0)
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(ledger, "JOURNAL_PATH", tmp_path / "trades.jsonl")
    seed.save(cfg_path)

    httpd = webserver.ThreadingHTTPServer(("127.0.0.1", 0), webserver.Handler)
    httpd.verbose = False
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def collect(message="hello", session=None, timeout=30.0):
    return list(chat.stream(message, session, timeout=timeout))


def test_env_override_wins(fake_bin):
    assert chat.resolve_bin() == str(fake_bin)


def test_missing_binary_is_an_explained_error(monkeypatch):
    monkeypatch.delenv("WHEELS_OPENCODE_BIN", raising=False)
    monkeypatch.delenv("OPENCODE_BIN_PATH", raising=False)
    monkeypatch.setattr(chat, "resolve_bin", lambda: None)
    events = collect()
    assert events[0][0] == "error"
    assert "not found" in events[0][1]["message"]
    assert events[-1] == ("done", {"ok": False})


def test_stream_captures_session_tool_and_text(fake_bin):
    events = collect()
    assert ("session", {"session": "ses_test0123"}) in events
    assert ("tool", {"label": "consulting the playbook"}) in events
    assert ("tool", {"label": "checking the engine"}) in events
    text = "".join(payload["text"] for event, payload in events if event == "delta")
    assert text == "Free cash is **$50**."
    assert events[-1] == ("done", {"ok": True})


def test_command_is_agent_scoped_and_passes_the_message_as_one_argument(fake_bin, tmp_path, monkeypatch):
    log = tmp_path / "argv.json"
    monkeypatch.setenv("FAKE_ARGV_LOG", str(log))
    collect("why is my free cash low?")

    argv = json.loads(log.read_text())
    assert argv[:5] == ["run", "--agent", "wheels-chat", "--format", "json"]
    assert argv[-1] == "why is my free cash low?"
    assert "--session" not in argv  # first turn starts a new thread


def test_existing_session_is_resumed(fake_bin, tmp_path, monkeypatch):
    log = tmp_path / "argv.json"
    monkeypatch.setenv("FAKE_ARGV_LOG", str(log))
    collect("again", session="ses_abc123")

    argv = json.loads(log.read_text())
    assert argv[argv.index("--session") + 1] == "ses_abc123"


def test_session_gating_rejects_control_characters():
    assert chat.valid_session("ses_abc123")
    assert not chat.valid_session("ses_abc123; rm -rf /")
    assert not chat.valid_session("--agent")
    assert not chat.valid_session("")


def test_agent_crash_is_reported_with_stderr(fake_bin, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "crash")
    events = collect()
    assert events[-1] == ("done", {"ok": False})
    error = next(payload["message"] for event, payload in events if event == "error")
    assert "code 2" in error


def test_timeout_does_not_leave_the_process_running(fake_bin, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "hang")
    events = collect(timeout=0.2)
    assert any(event == "error" and "did not finish" in payload["message"] for event, payload in events)


# -- the HTTP surface the bubble actually talks to --------------------------

def post_chat(base: str, payload: dict) -> tuple[int, str]:
    req = urllib.request.Request(
        base + "/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return res.status, res.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def test_chat_endpoint_streams_sse(live_server, fake_bin):
    status, raw = post_chat(live_server, {"message": "what should I sell?"})
    assert status == 200
    assert "event: session" in raw and "ses_test0123" in raw
    assert "event: delta" in raw and "Free cash is " in raw
    assert "event: done" in raw
    assert "data: " in raw


def test_chat_endpoint_resumes_a_session(live_server, fake_bin, tmp_path, monkeypatch):
    log = tmp_path / "argv.json"
    monkeypatch.setenv("FAKE_ARGV_LOG", str(log))
    status, _ = post_chat(live_server, {"message": "again", "session": "ses_abc123"})
    assert status == 200
    assert "ses_abc123" in json.loads(log.read_text())


def test_chat_endpoint_validates_input(live_server):
    status, body = post_chat(live_server, {"message": "   "})
    assert status == 400 and "message" in json.loads(body)["error"]
    status, body = post_chat(live_server, {"message": "hi", "session": "--evil"})
    assert status == 400 and "session" in json.loads(body)["error"]


def test_chat_info_reports_availability(live_server, fake_bin):
    with urllib.request.urlopen(live_server + "/api/chat/info", timeout=10) as res:
        info = json.loads(res.read())
    assert info["available"] is True
    assert info["agent"] == "wheels-chat"
    assert info["binary"] == str(fake_bin)


def test_chat_agent_definition_is_in_the_project():
    agent = config_mod.ROOT / ".opencode" / "agents" / "wheels-chat.md"
    assert agent.is_file()
    text = agent.read_text()
    assert "mode: subagent" in text
    assert "action: edit" in text and "effect: deny" in text
