"""Entrypoint.

    wheel                 → the TUI, nothing else needed
    wheel --json          → overview (book + scan) as JSON
    wheel --json scan     → just the scan
    wheel --json status   → just the book

The `--json` paths are how the DeepSeek agent reads the same numbers the TUI
shows, without needing a terminal.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import engine, ledger
from .config import Config


def _print(payload) -> None:
    print(json.dumps(payload, indent=2, default=str))


def _cmd_json(args) -> int:
    cfg = Config.load()
    book = ledger.load_book()
    cmd = args.command or "overview"

    if cmd == "overview":
        _print({"kind": "overview", "status": engine.status(cfg), "scan": engine.scan(cfg)})
    elif cmd == "scan":
        _print(engine.scan(cfg, symbols=args.symbols, cash_override=args.cash))
    elif cmd == "calls":
        _print(engine.calls(cfg, symbol=args.symbol))
    elif cmd == "plan":
        _print(engine.plan(cfg, symbol=args.symbol))
    elif cmd == "status":
        _print(engine.status(cfg))
    elif cmd == "sync":
        _print(engine.sync_apply(cfg, None) if args.apply else engine.sync_preview(cfg))
    elif cmd == "journal":
        _print(engine.journal())
    elif cmd == "open":
        if not (args.symbol and args.strike and args.expiry):
            print("open needs --symbol --strike --expiry --premium", file=sys.stderr)
            return 2
        ev = ledger.open_position(
            args.symbol, args.kind, args.strike, args.expiry, args.contracts, args.premium
        )
        _print(ev)
    elif cmd == "close":
        _print(ledger.close_position(args.parent, args.price, args.contracts))
    elif cmd == "expire":
        _print(ledger.expire_position(args.parent, args.contracts))
    elif cmd == "assign":
        _print(ledger.assign_position(args.parent, args.contracts))
    elif cmd == "called_away":
        _print(ledger.called_away(args.parent, args.contracts))
    elif cmd == "roll":
        _print(ledger.roll_position(args.parent, args.strike, args.expiry, args.premium, args.price, args.contracts))
    elif cmd == "fix":
        if args.set_json:
            try:
                overrides = json.loads(args.set_json)
            except json.JSONDecodeError as exc:
                print(f"fix: --set is not valid JSON: {exc}", file=sys.stderr)
                return 2
            _print(ledger.record_fix(args.note or "", target=args.parent, set_fields=overrides))
        elif args.parent:
            print("fix --parent needs --set '{\"cost\": 19.42}'", file=sys.stderr)
            return 2
        else:
            _print(ledger.record_fix(args.note or ""))
    else:
        print(f"unknown command {cmd}", file=sys.stderr)
        return 2

    if book.warnings and cmd not in ("journal", "status"):
        print(json.dumps({"journal_warnings": book.warnings}), file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wheel", add_help=True)
    parser.add_argument("--json", action="store_true", help="machine output instead of the TUI")
    parser.add_argument("--web", action="store_true", help="open the browser UI instead of the TUI")
    parser.add_argument("--host", default="127.0.0.1", help="web bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="web port (default 8765)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    parser.add_argument("command", nargs="?", default=None)
    parser.add_argument("--symbols", default=None)
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--kind", default="put", choices=["put", "call"])
    parser.add_argument("--strike", type=float, default=None)
    parser.add_argument("--expiry", default=None)
    parser.add_argument("--contracts", type=int, default=1)
    parser.add_argument("--premium", type=float, default=0.0)
    parser.add_argument("--price", type=float, default=0.0)
    parser.add_argument("--parent", default=None)
    parser.add_argument("--note", default=None)
    parser.add_argument("--set", dest="set_json", default=None,
                        help="fix: JSON object of actual values, e.g. '{\"cost\": 19.42, \"shares\": 100}'")
    parser.add_argument("--cash", type=float, default=None,
                        help="project against this capital instead of free cash")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    if args.json:
        return _cmd_json(args)

    if args.web:
        from .web.server import serve

        serve(host=args.host, port=args.port, open_browser=not args.no_browser)
        return 0

    from .app import WheelsApp

    WheelsApp().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
