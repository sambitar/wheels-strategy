"""Local web UI for the wheel desk.

Deliberately dependency-free: stdlib `http.server` plus a hand-written
frontend. No build step, no npm, no FastAPI — `uv sync` stays exactly as it
was. The engine already returns plain dicts, so this layer is pure transport.

Binds to 127.0.0.1 by default. Pass --host 0.0.0.0 only if you want it on your
LAN, and know that anyone who can reach it can record trades.
"""

from __future__ import annotations

import json
import mimetypes
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import engine, ledger
from ..config import Config
from . import chat

STATIC = Path(__file__).parent / "static"


def _book_payload() -> dict:
    cfg = Config.load()
    return engine.book_summary(cfg, ledger.load_book())


def _settings_payload() -> dict:
    cfg = Config.load()
    return {"kind": "settings", "settings": cfg.as_dict()}


def api_scan(query: dict) -> dict:
    cfg = Config.load()
    projected = query.get("project", ["0"])[0] in ("1", "true", "yes")
    symbols = query.get("symbols", [None])[0]
    return engine.scan(
        cfg,
        symbols=symbols,
        cash_override=cfg.budget if projected else None,
    )


def api_settings_save(body: dict) -> dict:
    cfg = Config.load()
    for key, value in body.items():
        if key == "symbols":
            cfg.symbols = [str(s).upper().strip() for s in value if str(s).strip()]
            continue
        if not hasattr(cfg, key):
            continue
        try:
            if key in ("dte_min", "dte_max", "min_open_interest", "top_n"):
                setattr(cfg, key, int(value))
            elif key in ("budget", "max_spread_pct", "risk_free_rate"):
                setattr(cfg, key, float(value))
            else:
                setattr(cfg, key, str(value))
        except (TypeError, ValueError):
            raise ValueError(f"bad value for {key}: {value!r}")
    if cfg.dte_max < cfg.dte_min:
        raise ValueError("dte_max must be >= dte_min")
    if cfg.budget <= 0:
        raise ValueError("budget must be positive")
    cfg.save()
    return {"ok": True, "settings": cfg.as_dict()}


def api_open(body: dict) -> dict:
    required = ("symbol", "strike", "expiry")
    missing = [k for k in required if not body.get(k)]
    if missing:
        raise ValueError(f"missing {', '.join(missing)}")

    # Over-commitment is allowed (you may be recording a trade before the old
    # put expires) but never silent: the response says how far past budget it goes.
    cfg = Config.load()
    book = ledger.load_book()
    strike = float(body["strike"])
    contracts = int(body.get("contracts", 1))
    kind = body.get("kind", "put")
    added = strike * 100 * contracts if kind == "put" else 0.0
    projected = book.committed + added
    over = max(0.0, projected - cfg.budget)

    result = ledger.open_position(
        str(body["symbol"]),
        kind,
        strike,
        str(body["expiry"]),
        contracts,
        float(body.get("premium", 0)),
    )
    if result.get("duplicate"):
        return {"ok": False, "duplicate": True, "message": "That trade is already in the ledger"}
    response = {"ok": True, "event": result}
    if over > 0:
        response["warning"] = (
            f"This puts {money(projected)} of collateral against a {money(cfg.budget)} budget "
            f"({money(over)} over)."
        )
    elif kind == "call":
        # A call is only covered by shares actually held. Recording one from
        # the Plan screen's projection books a naked leg — allowed, loud.
        held = book.lots.get(str(body["symbol"]).upper().strip(), None)
        covered = (held.shares // 100) if held else 0
        if contracts > covered:
            response["warning"] = (
                f"This call is not covered: {covered} of {contracts} contract(s) "
                f"have shares behind them. Calls you can sell today live in Calls."
            )
    return response


def money(v: float) -> str:
    return f"${v:,.0f}"


def api_close(body: dict) -> dict:
    ledger.close_position(
        _resolve_parent(_require_id(body)),
        float(body.get("price", 0)),
        body.get("contracts"),
    )
    return {"ok": True}


def _require_id(body: dict) -> str:
    if not body.get("id"):
        raise ValueError("missing id")
    return str(body["id"])


def _resolve_parent(id_or_label: str) -> str:
    """Accept either the ledger id or the display label as a position handle.

    The Plan screen only ever had the label (`from`), and an assign recorded
    against an unknown parent is silently dropped at replay — the put keeps
    holding its collateral and free cash never comes back. Resolve both forms
    so every entry point hits the same short.
    """
    book = ledger.load_book()
    if id_or_label in book.shorts:
        return id_or_label
    for short in book.shorts.values():
        if short.label == id_or_label:
            return short.id
    return id_or_label


def api_expire(body: dict) -> dict:
    ledger.expire_position(_resolve_parent(_require_id(body)), body.get("contracts"))
    return {"ok": True}


def api_assign(body: dict) -> dict:
    ledger.assign_position(_resolve_parent(_require_id(body)), body.get("contracts"))
    book = ledger.load_book()
    lot = book.lots.get(str(body.get("symbol", "")).upper())
    return {
        "ok": True,
        "holdings": {s: {"shares": l.shares, "basis": round(l.basis, 2)} for s, l in book.lots.items()},
        "basis": round(lot.basis, 2) if lot else None,
    }


def api_called_away(body: dict) -> dict:
    ledger.called_away(_resolve_parent(_require_id(body)), body.get("contracts"))
    return {"ok": True}


def api_fix(body: dict) -> dict:
    """Correct a trade with what the broker actually filled.

    Append-only: the original ticket stays, the fix rides on top of it, and
    every number (collateral, basis, cost, realized, total) recomputes.
    """
    handle = _require_id(body)
    set_fields = body.get("set")
    if not isinstance(set_fields, dict):
        raise ValueError("fix needs a 'set' object of actual values")
    # Note: record_fix resolves id-or-label itself, and accepts positions
    # that are already closed — unlike _resolve_parent, which needs live shorts.
    event = ledger.record_fix(
        str(body.get("note", "")), target=handle, set_fields=set_fields
    )
    book = ledger.load_book()
    return {"ok": True, "event": event, "book": engine.book_summary(Config.load(), book)}


def api_sync_apply(body: dict) -> dict:
    ids = body.get("ids")
    if not ids and not body.get("all"):
        raise ValueError("pass ids or all=true")
    return engine.sync_apply(Config.load(), None if body.get("all") else list(ids))


CHAT_MESSAGE_LIMIT = 8000


GET_ROUTES = {
    "/api/book": lambda q: _book_payload(),
    "/api/status": lambda q: engine.status(Config.load()),
    "/api/scan": api_scan,
    "/api/calls": lambda q: engine.calls(Config.load(), symbol=q.get("symbol", [None])[0]),
    "/api/plan": lambda q: engine.plan(Config.load(), symbol=q.get("symbol", [None])[0]),
    "/api/journal": lambda q: engine.journal(),
    "/api/settings": lambda q: _settings_payload(),
    "/api/sync": lambda q: engine.sync_preview(Config.load()),
    "/api/chat/info": lambda q: chat.status(),
}

POST_ROUTES = {
    "/api/trades/open": api_open,
    "/api/trades/close": api_close,
    "/api/trades/expire": api_expire,
    "/api/trades/assign": api_assign,
    "/api/trades/called-away": api_called_away,
    "/api/trades/fix": api_fix,
    "/api/settings": api_settings_save,
    "/api/sync": api_sync_apply,
}


class Handler(BaseHTTPRequestHandler):
    server_version = "Wheels"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - silence access logs
        if self.server.verbose:  # type: ignore[attr-defined]
            super().log_message(fmt, *args)

    # -- helpers -----------------------------------------------------------
    def _send_json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self._send_json({"error": "not found"}, 404)
            return
        data = path.read_bytes()
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            raise ValueError("body is not valid JSON")
        if not isinstance(payload, dict):
            raise ValueError("body must be a JSON object")
        return payload

    # -- chat: one prompt, streamed back as server-sent events -------------
    def _write_frame(self, event: str, payload: dict) -> None:
        frame = f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"
        self.wfile.write(frame.encode())
        self.wfile.flush()

    def _chat(self) -> None:
        """Open one turn. Reached only before any header is written, so the
        validation errors can still be plain JSON with a 4xx status."""
        try:
            body = self._body()
        except ValueError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        message = str(body.get("message") or "").strip()
        if not message:
            self._send_json({"error": "message is required"}, HTTPStatus.BAD_REQUEST)
            return
        if len(message) > CHAT_MESSAGE_LIMIT:
            self._send_json(
                {"error": f"message is too long (max {CHAT_MESSAGE_LIMIT} characters)"},
                HTTPStatus.BAD_REQUEST,
            )
            return
        session = body.get("session") or None
        if session is not None and not chat.valid_session(session):
            self._send_json({"error": "bad session id"}, HTTPStatus.BAD_REQUEST)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True

        try:
            events = chat.stream(message, session)
            try:
                for event, payload in events:
                    self._write_frame(event, payload)
            finally:
                events.close()  # kills the subprocess if the bubble went away
        except (BrokenPipeError, ConnectionResetError):
            # The bubble was collapsed or the page closed; the generator's
            # finally block kills the subprocess.
            pass
        except Exception as exc:  # noqa: BLE001 - headers are gone, only SSE left
            try:
                self._write_frame("error", {"message": f"{type(exc).__name__}: {exc}"})
                self._write_frame("done", {"ok": False})
            except Exception:  # noqa: BLE001
                pass

    # -- verbs -------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path in GET_ROUTES:
            try:
                self._send_json(GET_ROUTES[path](query))
            except Exception as exc:  # noqa: BLE001 - surface, never 500-crash
                self._send_json({"error": f"{type(exc).__name__}: {exc}"}, 500)
            return

        if path in ("/", "/index.html"):
            self._send_file(STATIC / "index.html")
            return

        rel = path.lstrip("/")
        if rel and ".." not in rel:
            self._send_file(STATIC / rel)
            return

        self._send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/chat":
            try:
                self._chat()
            except Exception as exc:  # noqa: BLE001
                try:
                    self._send_json({"error": f"{type(exc).__name__}: {exc}"}, 500)
                except Exception:  # noqa: BLE001 - headers may already be sent
                    pass
            return
        route = POST_ROUTES.get(parsed.path)
        if route is None:
            self._send_json({"error": "not found"}, 404)
            return
        try:
            body = self._body()
            self._send_json(route(body))
        except ValueError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": f"{type(exc).__name__}: {exc}"}, 500)


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True,
          verbose: bool = False) -> None:
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.verbose = verbose  # type: ignore[attr-defined]
    url = f"http://{host if host != '0.0.0.0' else '127.0.0.1'}:{port}/"

    print(f"wheels  →  {url}")
    if host == "0.0.0.0":  # noqa: S104 - explicit opt-in
        print("         listening on all interfaces — anyone on your network can record trades and chat with the agent")
    print("         ctrl-c to stop")

    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 - headless is fine
            pass

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
