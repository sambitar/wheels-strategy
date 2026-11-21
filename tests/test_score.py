from __future__ import annotations

from datetime import date, timedelta

from wheel import ledger, score
from wheel.market import Contract, Quote


def make_quote(price=17.69, hv=0.70, sma50=19.0, high=40.0, low=16.6, expiries=None):
    q = Quote(symbol="FIG", price=price, hv=hv, sma50=sma50, high_52w=high, low_52w=low)
    q.expiries = expiries or []
    return q


def make_contract(strike=15.0, kind="put", dte=58, bid=0.57, ask=0.64, iv=0.60, oi=5000, delta=0.21):
    c = Contract(
        symbol="FIG", kind=kind, expiry=(date.today() + timedelta(days=dte)).isoformat(),
        strike=strike, bid=bid, ask=ask, last=0.60, mid=(bid + ask) / 2,
        iv=iv, oi=oi, volume=10, dte=dte,
    )
    c.delta = delta
    return c


def test_covered_call_collateral_is_shares_not_budget():
    """A strike above the cash budget is still sellable when shares back it.
    Budget gates must never reject a covered call; only shares size it."""
    q = make_quote(price=17.69)
    c = score.build_candidate(
        make_contract(strike=25.0, kind="call", bid=0.30, ask=0.40, delta=0.20),
        q, "balanced", 100, 15.0,
    )
    c.contracts_affordable = 1  # what the engine now sets: free_shares // 100
    kept = score.covered_call_rows([c], basis=18.87, shares=100)
    assert kept == [c]
    assert c.contracts_affordable == 1

    # ...and zero unpledged shares means zero candidates, loudly rejected.
    c2 = score.build_candidate(
        make_contract(strike=25.0, kind="call", bid=0.30, ask=0.40, delta=0.20, dte=59),
        q, "balanced", 100, 15.0,
    )
    c2.contracts_affordable = 0
    assert score.covered_call_rows([c2], basis=18.87, shares=0) == []
    assert any("shares" in g for g in c2.gates_failed)


# -- metrics ---------------------------------------------------------------

def test_put_breakeven_and_cushion():
    q = make_quote()
    c = score.build_candidate(make_contract(), q, "balanced", 100, 15.0)
    assert abs(c.breakeven - (15.0 - 0.605)) < 1e-9
    expected = (17.69 - c.breakeven) / 17.69 * 100
    assert abs(c.cushion_pct - expected) < 1e-9
    assert c.cushion_pct > 10


def test_annualized_yield_scales_with_dte():
    q = make_quote()
    short = score.build_candidate(make_contract(dte=30), q, "balanced", 100, 15.0)
    long = score.build_candidate(make_contract(dte=60), q, "balanced", 100, 15.0)
    assert short.annualized > long.annualized


def test_covered_call_cushion_is_room_to_the_strike():
    q = make_quote(price=17.69)
    c = score.build_candidate(make_contract(strike=20.0, kind="call", delta=0.29), q, "balanced", 100, 15.0)
    # spot 17.69 -> strike 20.00 is ~13% of headroom
    assert 12.0 < c.cushion_pct < 14.0


# -- gates -----------------------------------------------------------------

def test_low_open_interest_is_excluded_with_a_reason():
    cand = score.build_candidate(make_contract(oi=5), make_quote(), "balanced", 100, 15.0)
    assert not cand.passes
    assert any("OI" in g for g in cand.gates_failed)


def test_wide_spread_is_excluded():
    cand = score.build_candidate(make_contract(bid=0.10, ask=0.90), make_quote(), "balanced", 100, 15.0)
    assert not cand.passes
    assert any("spread" in g for g in cand.gates_failed)


def test_zero_bid_is_excluded():
    cand = score.build_candidate(make_contract(bid=0.0, ask=0.05), make_quote(), "balanced", 100, 15.0)
    assert not cand.passes


def test_delta_band_rejects_outliers_but_keeps_the_candidate_visible():
    cand = score.build_candidate(make_contract(delta=0.55), make_quote(), "balanced", 100, 15.0)
    out = score.filter_by_delta([cand], "balanced")
    assert len(out) == 1                 # kept for the rejection summary
    assert not out[0].passes
    assert any("delta" in g for g in out[0].gates_failed)


def test_conservative_profile_rejects_a_balanced_delta():
    cand = score.build_candidate(make_contract(delta=0.30), make_quote(), "conservative", 100, 15.0)
    score.filter_by_delta([cand], "conservative")
    assert not cand.passes


def test_unaffordable_collateral_is_flagged():
    cand = score.build_candidate(make_contract(strike=60.0, bid=2.0, ask=2.2), make_quote(price=60.0), "balanced", 100, 15.0)
    score.assign_affordability([cand], free_cash=2000.0)
    assert cand.contracts_affordable == 0
    assert not cand.passes


def test_affordability_counts_whole_contracts():
    cand = score.build_candidate(make_contract(strike=15.0), make_quote(), "balanced", 100, 15.0)
    score.assign_affordability([cand], free_cash=2000.0)
    assert cand.contracts_affordable == 1  # 1500 <= 2000, 3000 > 2000


# -- ranking ---------------------------------------------------------------

def test_ranking_prefers_more_cushion_at_equal_yield():
    q = make_quote()
    deep = score.build_candidate(make_contract(strike=12.5, bid=0.10, ask=0.12, delta=0.12), q, "balanced", 100, 30.0)
    tight = score.build_candidate(make_contract(strike=17.0, bid=1.10, ask=1.20, delta=0.33), q, "balanced", 100, 30.0)
    score.assign_affordability([deep, tight], 2000.0)
    ranked = score.rank([deep, tight])
    assert ranked[0].cushion_pct > ranked[-1].cushion_pct


def test_rejected_summary_explains_an_empty_scan():
    a = score.build_candidate(make_contract(oi=1), make_quote(), "balanced", 100, 15.0)
    b = score.build_candidate(make_contract(bid=0.01, ask=1.0), make_quote(), "balanced", 100, 15.0)
    summary = score.rejected_summary([a, b])
    assert sum(summary.values()) >= 2


# -- flags -----------------------------------------------------------------

def test_earnings_before_expiry_is_flagged():
    q = make_quote()
    q.earnings = [(date.today() + timedelta(days=10)).isoformat()]
    cand = score.build_candidate(make_contract(dte=58), q, "balanced", 100, 15.0)
    assert any("earnings" in f for f in cand.flags)


def test_earnings_after_expiry_is_not_flagged():
    q = make_quote()
    q.earnings = [(date.today() + timedelta(days=200)).isoformat()]
    cand = score.build_candidate(make_contract(dte=58), q, "balanced", 100, 15.0)
    assert not any("earnings" in f for f in cand.flags)


def test_falling_knife_flag_only_applies_to_puts():
    q = make_quote()
    put = score.build_candidate(make_contract(), q, "balanced", 100, 15.0)
    call = score.build_candidate(make_contract(strike=20.0, kind="call"), q, "balanced", 100, 15.0)
    assert any("falling knife" in f for f in put.flags)
    assert not any("falling knife" in f for f in call.flags)


# -- next action -----------------------------------------------------------

def test_assigned_put_points_at_covered_calls():
    lot = ledger.Lot("FIG", 100, 16.33)
    action = score.next_action(None, 17.69, 0.0, lot, None)
    assert action.jump == "calls"
    assert "16.33" in action.text


def test_put_captured_most_value_short_dte_suggests_closing():
    short = ledger.Short("id", "FIG", "put", 15.0, (date.today() + timedelta(days=5)).isoformat(), 1, 1.00, "")
    action = score.next_action(short, 17.0, 0.05, None, None)
    assert "captured" in action.text


def test_threatened_put_suggests_roll_or_assignment():
    short = ledger.Short("id", "FIG", "put", 17.5, (date.today() + timedelta(days=30)).isoformat(), 1, 1.00, "")
    action = score.next_action(short, 16.0, 2.5, None, None)
    assert action.urgency == "high"
    assert "roll" in action.text.lower() or "assignment" in action.text.lower()


def test_itm_call_with_dividend_is_high_urgency():
    q = make_quote()
    q.ex_div = (date.today() + timedelta(days=5)).isoformat()
    short = ledger.Short("id", "FIG", "call", 20.0, (date.today() + timedelta(days=30)).isoformat(), 1, 0.5, "")
    action = score.next_action(short, 21.0, 1.2, ledger.Lot("FIG", 100, 16.33), q)
    assert action.urgency == "high"
    assert "early-assignment".lower() in action.text.lower()


def test_covered_call_rows_reject_strikes_below_basis():
    q = make_quote()
    below = score.build_candidate(make_contract(strike=15.0, kind="call", delta=0.29), q, "balanced", 100, 15.0)
    above = score.build_candidate(make_contract(strike=20.0, kind="call", delta=0.29), q, "balanced", 100, 15.0)
    score.assign_affordability([below, above], 5000.0)
    kept = score.covered_call_rows([below, above], basis=16.33, shares=100)
    assert [c.contract.strike for c in kept] == [20.0]


# -- plan / covered call ranking -------------------------------------------

def test_call_delta_band_is_wider_than_put_band():
    from wheel.score import CALL_DELTA_BANDS, DELTA_BANDS

    for profile in ("conservative", "balanced", "aggressive"):
        assert CALL_DELTA_BANDS[profile][1] > DELTA_BANDS[profile][1]


def test_a_038_delta_call_passes_but_03_put_band_would_reject_it():
    """The bug this fixed: the obvious $20C was filtered out of the plan."""
    q = make_quote(price=17.76)
    call = score.build_candidate(
        make_contract(strike=20.0, kind="call", delta=0.37), q, "balanced", 100, 15.0
    )
    score.filter_by_delta([call], "balanced", kind="call")
    assert call.passes


def test_call_ranking_prefers_income_on_basis_over_far_otm_capital_gain():
    q = make_quote(price=17.76)
    near = score.build_candidate(
        make_contract(strike=20.0, kind="call", dte=30, bid=0.50, ask=0.52, delta=0.28),
        q, "balanced", 100, 15.0,
    )
    far = score.build_candidate(
        make_contract(strike=25.0, kind="call", dte=58, bid=0.20, ask=0.22, delta=0.11),
        q, "balanced", 100, 15.0,
    )
    score.assign_affordability([near, far], 5000.0)
    ranked = score.rank_calls([near, far])
    assert ranked[0].contract.strike == 20.0
