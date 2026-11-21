"""Web layer tests.

The engine is already covered elsewhere; these check the transport — routes,
validation, and that a write actually reaches the journal.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from wheel import ledger
from wheel.web import server as webserver


@pytest.fixture
def live_server(tmp_path, monkeypatch):
    """A real server on an ephemeral port, bound to a temp journal and config."""
    from wheel import config as config_mod

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


def get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as res:
        return json.loads(res.read())


def post(url: str, payload: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return res.status, json.loads(res.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


# -- static ----------------------------------------------------------------

def test_index_is_served(live_server):
    with urllib.request.urlopen(live_server + "/", timeout=10) as res:
        body = res.read().decode()
    assert res.status == 200
    assert "Wheels" in body
    assert "app.js" in body


def test_static_assets_are_served(live_server):
    for asset in ("style.css", "app.js", "views.js", "modals.js", "favicon.svg"):
        with urllib.request.urlopen(f"{live_server}/{asset}", timeout=10) as res:
            assert res.status == 200, asset


def test_path_traversal_is_refused(live_server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(live_server + "/../server.py", timeout=10)
    assert exc.value.code == 404


# -- reads -----------------------------------------------------------------

def test_book_reads_empty(live_server):
    data = get(live_server + "/api/book")
    assert data["budget"] == 2000.0
    assert data["open_puts"] == 0


def test_status_reflects_an_open_position(live_server):
    post(live_server + "/api/trades/open", {
        "symbol": "FIG", "kind": "put", "strike": 19.5,
        "expiry": "2026-11-20", "contracts": 1, "premium": 0.63,
    })
    data = get(live_server + "/api/status")
    assert len(data["positions"]) == 1
    assert data["positions"][0]["label"] == "FIG 2026-11-20 19.5P"
    assert data["book"]["committed"] == 1950.0


def test_book_reports_total_money_and_where_it_sits(live_server):
    """The accounting identity: total = budget + realized = cash + put
    collateral + shares at cost. Assignment must move capital between
    categories, never mint new spendable money."""
    data = get(live_server + "/api/book")
    assert data["total"] == 2000.0 and data["cash"] == 2000.0  # nothing yet

    _, opened = post(live_server + "/api/trades/open", {
        "symbol": "FIG", "kind": "put", "strike": 19.5,
        "expiry": "2026-12-18", "contracts": 1, "premium": 0.63,
    })
    data = get(live_server + "/api/book")
    assert data["total"] == 2000.0    # an open premium is not banked until finished
    assert data["collateral"] == 1950.0
    assert data["cash"] == 50.0

    post(live_server + "/api/trades/assign", {"id": opened["event"]["id"], "symbol": "FIG"})
    data = get(live_server + "/api/book")
    assert data["collateral"] == 0
    assert data["shares_cost"] == 1950.0   # the cash bought stock, it did not return
    assert data["total"] == 2063.0         # 2000 budget + 63 banked premium
    assert data["cash"] == 113.0           # what the broker would show as sweep
    assert data["free_cash"] == 50.0       # collateral available for a NEW put
    assert data["total"] == pytest.approx(
        data["cash"] + data["collateral"] + data["shares_cost"], abs=1.0
    )


def test_called_away_closes_the_wheel_with_capital_home(live_server):
    """Full cycle: put open → assign → sell call → called away. The total
    should equal every cash the trades actually produced, and all of it
    free again."""
    _, put = post(live_server + "/api/trades/open", {
        "symbol": "FIG", "kind": "put", "strike": 19.5,
        "expiry": "2026-12-18", "contracts": 1, "premium": 0.63,
    })
    post(live_server + "/api/trades/assign", {"id": put["event"]["id"], "symbol": "FIG"})
    _, call = post(live_server + "/api/trades/open", {
        "symbol": "FIG", "kind": "call", "strike": 20.0,
        "expiry": "2026-12-18", "contracts": 1, "premium": 1.00,
    })
    post(live_server + "/api/trades/called-away", {"id": call["event"]["id"]})

    data = get(live_server + "/api/book")
    # Cash in: +63 put premium, -1950 shares, +100 call premium, +2000 delivered.
    assert data["realized_pnl"] == 213.0
    assert data["total"] == 2213.0
    assert data["committed"] == 0
    assert data["cash"] == 2213.0
    assert data["free_cash"] == 2000.0  # free_cash is capped at budget; the
    # extra 213 lives in total/realized, waiting to be reinvested or withdrawn.
    data = get(live_server + "/api/settings")
    for key in ("budget", "profile", "dte_min", "dte_max", "symbols"):
        assert key in data["settings"]


def test_journal_starts_empty(live_server):
    data = get(live_server + "/api/journal")
    assert data["event_count"] == 0
    assert data["events"] == []


# -- writes ----------------------------------------------------------------

def test_post_open_creates_an_event(live_server):
    status, body = post(live_server + "/api/trades/open", {
        "symbol": "FIG", "kind": "put", "strike": 17.5,
        "expiry": "2026-12-18", "contracts": 2, "premium": 1.17,
    })
    assert status == 200 and body["ok"]
    events = ledger.read_events()
    assert len(events) == 1
    assert events[0]["contracts"] == 2


def test_duplicate_open_is_refused_without_writing(live_server):
    payload = {
        "symbol": "FIG", "kind": "put", "strike": 17.5,
        "expiry": "2026-12-18", "contracts": 1, "premium": 1.0,
    }
    post(live_server + "/api/trades/open", payload)
    status, body = post(live_server + "/api/trades/open", payload)
    assert status == 200
    assert body["duplicate"] is True
    assert len(ledger.read_events()) == 1


def test_bad_expiry_is_400_and_writes_nothing(live_server):
    status, body = post(live_server + "/api/trades/open", {
        "symbol": "FIG", "strike": 17.5, "expiry": "next friday", "premium": 1.0,
    })
    assert status == 400
    assert "isoformat" in body["error"].lower() or "invalid" in body["error"].lower()
    assert ledger.read_events() == []


def test_missing_fields_are_400(live_server):
    status, body = post(live_server + "/api/trades/open", {"symbol": "FIG"})
    assert status == 400
    assert "missing" in body["error"]


def test_over_commitment_returns_a_warning(live_server):
    status, body = post(live_server + "/api/trades/open", {
        "symbol": "FIG", "kind": "put", "strike": 19.5,
        "expiry": "2026-11-20", "contracts": 1, "premium": 0.6,
    })
    assert body["ok"]
    assert "warning" not in body  # 1950 fits inside 2000

    status, body = post(live_server + "/api/trades/open", {
        "symbol": "SOFI", "kind": "put", "strike": 15.0,
        "expiry": "2027-01-15", "contracts": 1, "premium": 0.4,
    })
    assert "warning" in body
    assert "over" in body["warning"]


def test_assign_creates_shares_at_correct_basis(live_server):
    _, opened = post(live_server + "/api/trades/open", {
        "symbol": "FIG", "kind": "put", "strike": 17.5,
        "expiry": "2026-12-18", "contracts": 1, "premium": 1.17,
    })
    trade_id = opened["event"]["id"]
    status, body = post(live_server + "/api/trades/assign", {"id": trade_id, "symbol": "FIG"})
    assert status == 200
    assert body["basis"] == 16.33
    assert body["holdings"]["FIG"]["shares"] == 100


def test_calls_payload_carries_a_fresh_book_after_assignment(live_server):
    """Regression, two layers: the assign modal lands on the Calls view, which
    paints the sidebar from this payload — so the payload must exist AND must
    not pretend freed put collateral is spendable. Assignment spent it on
    shares; free cash stays where it was."""
    _, opened = post(live_server + "/api/trades/open", {
        "symbol": "FIG", "kind": "put", "strike": 19.5,
        "expiry": "2026-12-18", "contracts": 1, "premium": 0.63,
    })
    trade_id = opened["event"]["id"]
    assert get(live_server + "/api/book")["free_cash"] == 50.0  # 1950 collateral

    post(live_server + "/api/trades/assign", {"id": trade_id, "symbol": "FIG"})
    data = get(live_server + "/api/calls")
    assert "book" in data, "/api/calls must return the book so the sidebar refreshes"
    assert data["book"]["collateral"] == 0        # the put is gone
    assert data["book"]["shares_cost"] == 1950.0  # the cash became 100 shares
    assert data["book"]["committed"] == 1950.0    # still not spendable
    assert data["book"]["free_cash"] == 50.0


def test_assign_by_position_label_still_books_the_shares(live_server):
    """Regression: the Plan screen records assignments using the position
    label, not the ledger id. An unknown parent is dropped silently at
    replay, so the shares never appear and the capital vanishes from the
    book entirely. Label-or-id must land on the same short."""
    post(live_server + "/api/trades/open", {
        "symbol": "FIG", "kind": "put", "strike": 19.5,
        "expiry": "2026-12-18", "contracts": 1, "premium": 0.63,
    })
    status, body = post(live_server + "/api/trades/assign",
                        {"id": "FIG 2026-12-18 19.5P", "symbol": "FIG"})
    assert status == 200 and body["ok"]
    assert body["basis"] == 18.87
    book = get(live_server + "/api/book")
    assert book["shares_cost"] == 1950.0
    assert book["committed"] == 1950.0
    assert book["free_cash"] == 50.0
    assert get(live_server + "/api/status")["warnings"] == []


def test_expire_and_close_reduce_the_book(live_server):
    _, opened = post(live_server + "/api/trades/open", {
        "symbol": "FIG", "kind": "put", "strike": 15.0,
        "expiry": "2026-12-18", "contracts": 1, "premium": 0.6,
    })
    trade_id = opened["event"]["id"]
    post(live_server + "/api/trades/expire", {"id": trade_id})
    assert get(live_server + "/api/book")["open_puts"] == 0


def test_settings_post_persists(live_server):
    status, body = post(live_server + "/api/settings", {"budget": 5000, "profile": "aggressive"})
    assert status == 200
    assert body["settings"]["budget"] == 5000.0
    again = get(live_server + "/api/settings")["settings"]
    assert again["budget"] == 5000.0
    assert again["profile"] == "aggressive"


def test_settings_rejects_a_bad_dte_window(live_server):
    status, body = post(live_server + "/api/settings", {"dte_min": 90, "dte_max": 10})
    assert status == 400
    assert "dte_max" in body["error"]


def test_settings_watchlist_round_trips(live_server):
    post(live_server + "/api/settings", {"symbols": ["FIG", "SOFI", "NVDA"]})
    assert get(live_server + "/api/settings")["settings"]["symbols"] == ["FIG", "SOFI", "NVDA"]


def test_settings_rejects_a_non_positive_budget(live_server):
    status, body = post(live_server + "/api/settings", {"budget": 0})
    assert status == 400


def test_open_uncovered_call_is_loud_not_silent(live_server):
    """Recording a call from the Plan screen's projection must say it is
    naked — and go quiet once shares are actually held."""
    status, body = post(live_server + "/api/trades/open", {
        "symbol": "FIG", "kind": "call", "strike": 20.0,
        "expiry": "2026-12-18", "contracts": 1, "premium": 0.50,
    })
    assert status == 200 and body["ok"]
    assert "not covered" in body["warning"]

    _, opened = post(live_server + "/api/trades/open", {
        "symbol": "FIG", "kind": "put", "strike": 19.5,
        "expiry": "2026-12-18", "contracts": 1, "premium": 0.63,
    })
    post(live_server + "/api/trades/assign", {"id": opened["event"]["id"], "symbol": "FIG"})
    status, body = post(live_server + "/api/trades/open", {
        "symbol": "FIG", "kind": "call", "strike": 21.0,
        "expiry": "2026-12-18", "contracts": 1, "premium": 0.40,
    })
    assert body["ok"] and "warning" not in body  # 100 real shares cover it


def test_panels_declare_their_mode(tmp_path, monkeypatch):
    """The agent reads JSON, not CSS: every pane states whether its numbers
    are actual holdings or projections."""
    from wheel import engine, ledger
    from wheel.config import Config

    monkeypatch.setattr(ledger, "JOURNAL_PATH", tmp_path / "trades.jsonl")
    cfg = Config.load()
    assert engine.plan(cfg)["mode"] == "projection"
    assert engine.calls(cfg)["mode"] == "actual"


def test_calls_pane_separates_sold_calls_from_candidates(tmp_path, monkeypatch):
    """Sold calls are positions; the table lists only NEW candidates, sized
    to shares not already pledged behind what you sold."""
    from types import SimpleNamespace

    from wheel import engine, ledger, market
    from wheel.config import Config

    journal = tmp_path / "trades.jsonl"
    monkeypatch.setattr(ledger, "JOURNAL_PATH", journal)
    ev = ledger.open_position("FIG", "put", 19.5, "2026-12-18", 1, 0.63, path=journal)
    ledger.assign_position(ev["id"], path=journal)

    q = SimpleNamespace(ok=True, price=15.8)
    monkeypatch.setattr(market, "fetch_quotes", lambda *a, **k: {"FIG": q})
    monkeypatch.setattr(market, "contracts_for", lambda *a, **k: ([], []))

    cfg = Config.load()
    pos = engine.calls(cfg)["positions"][0]
    assert pos["sold_calls"] == []           # nothing written yet
    assert pos["pledged_shares"] == 0
    assert pos["free_shares"] == 100         # all shares available

    ledger.open_position("FIG", "call", 21.0, "2027-02-20", 1, 0.40, path=journal)
    pos = engine.calls(cfg)["positions"][0]
    assert pos["sold_calls"][0]["label"] == "FIG 2027-02-20 21C"  # position, disclosed
    assert pos["pledged_shares"] == 100
    assert pos["free_shares"] == 0
    assert pos["candidates"] == []           # the same shares are never offered twice


def test_sync_apply_requires_explicit_confirmation(live_server):
    status, body = post(live_server + "/api/sync", {})
    assert status == 400
    assert "ids" in body["error"]


# -- fixes: editing what really happened ---------------------------------

def test_status_marks_the_book_to_market(live_server):
    """The live total must equal cash + collateral + shares at today's
    price — the identity has to survive the quote round-trip."""
    _, opened = post(live_server + "/api/trades/open", {
        "symbol": "FIG", "kind": "put", "strike": 19.5,
        "expiry": "2026-12-18", "contracts": 1, "premium": 0.63,
    })
    post(live_server + "/api/trades/assign", {"id": opened["event"]["id"], "symbol": "FIG"})

    st = get(live_server + "/api/status")
    b = st["book"]
    shares_row = [r for r in st["positions"] if r["kind"] == "shares"][0]
    assert "total_market" in b and "unrealized" in b
    assert abs(b["cash"] + b["collateral"] + b["shares_live"] - b["total_market"]) <= 1.0
    if shares_row["spot"] > 0:  # quotes live; otherwise cost fallback holds
        assert abs(b["unrealized"] - (shares_row["spot"] - 19.5) * 100) <= 1.0
        assert abs(b["total_market"] - (b["total"] + b["unrealized"])) <= 1.0


def test_fix_endpoint_recomputes_every_number(live_server):
    """One correction should flow through collateral, basis, committed,
    free cash, cash and total — and arrive in the response so no screen
    can show a stale version."""
    _, opened = post(live_server + "/api/trades/open", {
        "symbol": "FIG", "kind": "put", "strike": 19.5,
        "expiry": "2026-12-18", "contracts": 1, "premium": 0.63,
    })
    post(live_server + "/api/trades/assign", {"id": opened["event"]["id"], "symbol": "FIG"})
    before = get(live_server + "/api/book")
    assert before["shares_cost"] == 1950.0

    status, body = post(live_server + "/api/trades/fix", {
        "id": "FIG 2026-12-18 19.5P",          # label, as the Plan screen uses it
        "set": {"cost": 19.42},
    })
    assert status == 200 and body["ok"]
    book = body["book"]  # fresh book in the payload: the sidebar has no way to lag
    assert book["shares_cost"] == 1942.0
    assert book["committed"] == 1942.0
    assert book["free_cash"] == 58.0
    assert book["cash"] == 121.0
    assert book["total"] == 2063.0
    assert get(live_server + "/api/book")["committed"] == 1942.0

    # Append-only: the original ticket is untouched, the fix rides on top.
    events = ledger.read_events()
    assert events[0]["strike"] == 19.5
    assert events[-1]["type"] == "fix" and events[-1]["set"] == {"cost": 19.42}


def test_fix_endpoint_rejects_nonsense(live_server):
    _, opened = post(live_server + "/api/trades/open", {
        "symbol": "FIG", "kind": "put", "strike": 19.5,
        "expiry": "2026-12-18", "contracts": 1, "premium": 0.63,
    })
    trade_id = opened["event"]["id"]
    status, body = post(live_server + "/api/trades/fix", {"id": trade_id, "set": {"symbol": "X"}})
    assert status == 400 and "symbol" in body["error"]
    status, body = post(live_server + "/api/trades/fix", {"id": "nope 1P", "set": {"cost": 1}})
    assert status == 400
    status, body = post(live_server + "/api/trades/fix", {"id": trade_id})
    assert status == 400
    assert all(ev.get("type") != "fix" for ev in ledger.read_events())


def test_fix_changes_the_next_move(live_server):
    """Accurate fills must change affordability, not just bookkeeping."""
    _, opened = post(live_server + "/api/trades/open", {
        "symbol": "FIG", "kind": "put", "strike": 19.5,
        "expiry": "2026-12-18", "contracts": 1, "premium": 0.63,
    })
    post(live_server + "/api/trades/assign", {"id": opened["event"]["id"], "symbol": "FIG"})
    post(live_server + "/api/trades/fix", {"id": opened["event"]["id"], "set": {"cost": 19.42, "shares": 100}})
    status = get(live_server + "/api/status")
    shares = [r for r in status["positions"] if r["kind"] == "shares"][0]
    assert shares["shares"] == 100
    assert shares["cost"] == 19.42
    assert shares["ids"] == [opened["event"]["id"]]
    # basis shown to the user is net of the premium, off the REAL cost
    assert abs(shares["basis"] - 18.79) < 0.01
