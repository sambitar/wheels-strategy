from __future__ import annotations

from datetime import date, timedelta

import pytest

from wheel import ledger


def _exp(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


@pytest.fixture
def journal(tmp_path):
    return tmp_path / "trades.jsonl"


def test_open_creates_a_derived_position(journal):
    ledger.open_position("FIG", "put", 17.5, _exp(30), 1, 1.17, path=journal)
    book = ledger.load_book(journal)
    assert len(book.puts()) == 1
    short = book.puts()[0]
    assert short.symbol == "FIG"
    assert short.premium == 1.17
    assert short.collateral == 1750.0
    assert book.committed == 1750.0


def test_ids_are_stable_and_duplicates_are_detected(journal):
    first = ledger.open_position("FIG", "put", 17.5, _exp(30), 1, 1.17, path=journal)
    second = ledger.open_position("FIG", "put", 17.5, _exp(30), 1, 1.20, path=journal)
    assert first["id"] == second["existing"]["id"]
    assert second["duplicate"] is True
    assert len(ledger.read_events(journal)) == 1


def test_assignment_derives_cost_basis_net_of_premium(journal):
    ev = ledger.open_position("FIG", "put", 17.5, _exp(30), 1, 1.17, path=journal)
    ledger.assign_position(ev["id"], path=journal)
    book = ledger.load_book(journal)

    assert book.puts() == []
    assert "FIG" in book.lots
    lot = book.lots["FIG"]
    assert lot.shares == 100
    assert abs(lot.basis - (17.5 - 1.17)) < 1e-9  # 16.33
    assert abs(book.realized_pnl - 117.0) < 1e-9


def test_assignment_keeps_capital_committed_as_shares(journal):
    """The cash did not come back at assignment — it bought stock. Until the
    shares are called away, that capital cannot secure another put."""
    ev = ledger.open_position("FIG", "put", 19.5, _exp(30), 1, 0.63, path=journal)
    assert ledger.load_book(journal).committed == 1950.0

    ledger.assign_position(ev["id"], path=journal)
    book = ledger.load_book(journal)
    assert sum(s.collateral for s in book.puts()) == 0.0  # put is gone
    assert book.shares_cost == 1950.0            # cash became stock at strike
    assert book.committed == 1950.0              # still tied up, honestly


def test_full_wheel_cycle_returns_the_capital(journal):
    """Only when the shares leave — called away here — does free cash return."""
    put = ledger.open_position("FIG", "put", 19.5, _exp(30), 1, 0.63, path=journal)
    ledger.assign_position(put["id"], path=journal)
    call = ledger.open_position("FIG", "call", 20.0, _exp(30), 1, 1.00, path=journal)
    ledger.called_away(call["id"], path=journal)

    book = ledger.load_book(journal)
    assert book.lots == {}
    assert book.committed == 0.0


def test_expiry_banks_the_whole_premium_and_frees_collateral(journal):
    ev = ledger.open_position("FIG", "put", 15.0, _exp(30), 2, 0.60, path=journal)
    ledger.expire_position(ev["id"], path=journal)
    book = ledger.load_book(journal)

    assert book.shorts == {}
    assert book.committed == 0.0
    assert abs(book.realized_pnl - 120.0) < 1e-9  # 0.60 * 100 * 2


def test_closing_realizes_the_difference(journal):
    ev = ledger.open_position("FIG", "put", 17.5, _exp(30), 1, 1.17, path=journal)
    ledger.close_position(ev["id"], 0.40, path=journal)
    book = ledger.load_book(journal)
    assert abs(book.realized_pnl - 77.0) < 1e-9  # (1.17 - 0.40) * 100


def test_partial_close_leaves_the_remainder_open(journal):
    ev = ledger.open_position("FIG", "put", 17.5, _exp(30), 3, 1.00, path=journal)
    ledger.close_position(ev["id"], 0.50, contracts=1, path=journal)
    book = ledger.load_book(journal)
    assert len(book.puts()) == 1
    assert book.puts()[0].contracts == 2
    assert abs(book.realized_pnl - 0.5 * 100) < 1e-9


def test_called_away_includes_strike_gain_above_basis(journal):
    put = ledger.open_position("FIG", "put", 17.5, _exp(30), 1, 1.17, path=journal)
    ledger.assign_position(put["id"], path=journal)
    call = ledger.open_position("FIG", "call", 20.0, _exp(30), 1, 0.53, path=journal)
    ledger.called_away(call["id"], path=journal)

    book = ledger.load_book(journal)
    assert book.lots == {}
    # Actual cash: +117 put premium, -1750 to buy shares, +53 call premium,
    # +2000 to deliver shares = 420. The put premium must not be counted twice.
    expected = 117.0 - 1750.0 + 53.0 + 2000.0
    assert abs(book.realized_pnl - expected) < 1e-6
    assert abs(book.realized_pnl - 420.0) < 1e-6


def test_full_cycle_pnl_equals_cash_in_minus_cash_out(journal):
    """The invariant that caught the double-count: realized P/L over a complete
    wheel must equal the net cash the trades actually produced."""
    put_strike, put_prem = 19.5, 0.63
    call_strike, call_prem = 20.0, 1.00

    ev = ledger.open_position("FIG", "put", put_strike, _exp(30), 1, put_prem, path=journal)
    book = ledger.load_book(journal)
    ledger.assign_position(ev["id"], path=journal)
    # basis is net of premium, hence lower than the cash actually paid
    assigned = ledger.load_book(journal)
    assert abs(assigned.lots["FIG"].basis - (put_strike - put_prem)) < 1e-9
    assert abs(assigned.lots["FIG"].cost - put_strike) < 1e-9

    call = ledger.open_position("FIG", "call", call_strike, _exp(30), 1, call_prem, path=journal)
    ledger.called_away(call["id"], path=journal)

    cash = put_prem * 100 - put_strike * 100 + call_prem * 100 + call_strike * 100
    book = ledger.load_book(journal)
    assert abs(book.realized_pnl - cash) < 1e-6


def test_roll_closes_and_opens_in_one_event(journal):
    ev = ledger.open_position("FIG", "put", 17.5, _exp(10), 1, 1.17, path=journal)
    ledger.roll_position(ev["id"], 15.0, _exp(45), 0.62, 2.10, path=journal)
    book = ledger.load_book(journal)

    assert len(book.puts()) == 1
    rolled = book.puts()[0]
    assert rolled.strike == 15.0
    assert rolled.premium == 0.62
    assert abs(book.realized_pnl - (1.17 - 2.10) * 100) < 1e-9


def test_unknown_parent_is_a_warning_not_a_crash(journal):
    ledger.close_position("nope", 1.0, path=journal)
    book = ledger.load_book(journal)
    assert book.warnings
    assert book.realized == []


def test_missing_journal_reads_as_empty(tmp_path):
    book = ledger.load_book(tmp_path / "absent.jsonl")
    assert book.shorts == {} and book.lots == {} and book.realized == []


def test_corrupt_line_is_skipped(tmp_path):
    path = tmp_path / "trades.jsonl"
    path.write_text('{"type": "open", "symbol": "FIG", "kind": "put", "strike": 15, "expiry": "2026-12-18", "contracts": 1, "premium": 0.6, "id": "x"}\nnot json\n')
    events = ledger.read_events(path)
    assert len(events) == 1


# -- sync classification ----------------------------------------------------

def test_sync_proposes_assignment_when_close_below_strike(journal):
    ledger.open_position("FIG", "put", 17.5, "2026-01-16", 1, 1.0, path=journal)
    book = ledger.load_book(journal)
    proposals, skipped = ledger.propose_expired(book, {"FIG": 16.0}, today=date(2026, 2, 1))

    assert len(proposals) == 1
    assert proposals[0].action == "assign"
    assert not skipped


def test_sync_proposes_expiry_when_close_above_strike(journal):
    ledger.open_position("FIG", "put", 15.0, "2026-01-16", 1, 1.0, path=journal)
    book = ledger.load_book(journal)
    proposals, _ = ledger.propose_expired(book, {"FIG": 18.0}, today=date(2026, 2, 1))
    assert proposals[0].action == "expire"


def test_sync_proposes_called_away_for_calls_above_strike(journal):
    ledger.open_position("FIG", "call", 20.0, "2026-01-16", 1, 0.5, path=journal)
    book = ledger.load_book(journal)
    proposals, _ = ledger.propose_expired(book, {"FIG": 21.0}, today=date(2026, 2, 1))
    assert proposals[0].action == "called_away"


def test_sync_skips_when_no_price_is_available(journal):
    ledger.open_position("FIG", "put", 15.0, "2026-01-16", 1, 1.0, path=journal)
    book = ledger.load_book(journal)
    proposals, skipped = ledger.propose_expired(book, {}, today=date(2026, 2, 1))
    assert proposals == []
    assert skipped[0]["reason"] == "no closing price available"


def test_sync_ignores_positions_that_have_not_expired(journal):
    ledger.open_position("FIG", "put", 15.0, "2026-06-19", 1, 1.0, path=journal)
    book = ledger.load_book(journal)
    proposals, _ = ledger.propose_expired(book, {"FIG": 10.0}, today=date(2026, 2, 1))
    assert proposals == []


# -- fixes: actual values override what the ticket implied ---------------

def test_fix_corrects_the_real_purchase_price_of_an_assignment(journal):
    """The strike is the assumption; the broker fill is the truth."""
    ev = ledger.open_position("FIG", "put", 19.5, _exp(30), 1, 0.63, path=journal)
    ledger.assign_position(ev["id"], path=journal)
    assert ledger.load_book(journal).committed == 1950.0

    ledger.record_fix("filled at 19.42", target=ev["id"],
                      set_fields={"cost": 19.42}, path=journal)
    book = ledger.load_book(journal)
    lot = book.lots["FIG"]
    assert abs(lot.cost - 19.42) < 1e-9
    assert abs(lot.basis - (19.42 - 0.63)) < 1e-9
    assert book.committed == pytest.approx(1942.0)  # the money actually out
    assert abs(book.realized_pnl - 63.0) < 1e-9     # premium untouched


def test_fix_corrects_shares_actually_delivered(journal):
    ev = ledger.open_position("FIG", "put", 19.5, _exp(30), 1, 0.63, path=journal)
    ledger.assign_position(ev["id"], path=journal)
    ledger.record_fix("", target=ev["id"], set_fields={"shares": 90}, path=journal)
    book = ledger.load_book(journal)
    assert book.lots["FIG"].shares == 90
    assert book.committed == 19.5 * 90


def test_fix_corrects_the_ticket_itself(journal):
    ev = ledger.open_position("FIG", "put", 19.5, _exp(30), 1, 0.63, path=journal)
    ledger.record_fix("", target=ev["id"], set_fields={"premium": 0.55}, path=journal)
    assert ledger.load_book(journal).puts()[0].premium == 0.55
    ledger.assign_position(ev["id"], path=journal)
    assert abs(ledger.load_book(journal).realized_pnl - 55.0) < 1e-9


def test_fix_resolves_position_labels(journal):
    ev = ledger.open_position("FIG", "put", 19.5, "2026-12-18", 1, 0.63, path=journal)
    ledger.assign_position(ev["id"], path=journal)
    ledger.record_fix("", target="FIG 2026-12-18 19.5P", set_fields={"cost": 19.0}, path=journal)
    assert abs(ledger.load_book(journal).lots["FIG"].cost - 19.0) < 1e-9


def test_fix_corrects_the_delivery_price_when_called_away(journal):
    put = ledger.open_position("FIG", "put", 17.5, _exp(30), 1, 1.17, path=journal)
    ledger.assign_position(put["id"], path=journal)
    call = ledger.open_position("FIG", "call", 20.0, _exp(30), 1, 0.53, path=journal)
    ledger.record_fix("pin risk", target=call["id"], set_fields={"price": 19.94}, path=journal)
    ledger.called_away(call["id"], path=journal)
    book = ledger.load_book(journal)
    # put premium 117 + call premium 53 + (19.94 − 17.50 cost) × 100
    assert abs(book.realized_pnl - (117.0 + 53.0 + 244.0)) < 1e-6


def test_fix_rejects_bad_edits(journal):
    ev = ledger.open_position("FIG", "put", 19.5, _exp(30), 1, 0.63, path=journal)
    for bad in ({"symbol": "SOFI"}, {"cost": -1}, {"expiry": "soon"}, {"shares": 0}):
        with pytest.raises(ValueError):
            ledger.record_fix("", target=ev["id"], set_fields=bad, path=journal)
    with pytest.raises(ValueError):
        ledger.record_fix("", target="nope 1P", set_fields={"cost": 1.0}, path=journal)
    assert all(ev.get("type") != "fix" for ev in ledger.read_events(journal))


def test_note_only_fix_is_a_memo_not_a_warning(journal):
    ledger.record_fix("adjusted for commission", path=journal)
    assert ledger.load_book(journal).warnings == []
