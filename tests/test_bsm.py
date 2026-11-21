from __future__ import annotations

import math

from wheel import bsm


def test_norm_cdf_known_values():
    assert abs(bsm.norm_cdf(0.0) - 0.5) < 1e-12
    assert abs(bsm.norm_cdf(1.96) - 0.975) < 1e-3
    assert bsm.norm_cdf(-10) < 1e-9


def test_put_delta_is_negative_between_minus_one_and_zero():
    d = bsm.delta(100, 95, 45, 0.30, 0.04, "put")
    assert -1.0 < d < 0.0
    assert abs(d) < 0.55  # OTM put


def test_call_delta_is_positive_and_pairs_with_put():
    spot, strike, days, vol, rate = 100.0, 100.0, 30, 0.25, 0.04
    c = bsm.delta(spot, strike, days, vol, rate, "call")
    p = bsm.delta(spot, strike, days, vol, rate, "put")
    assert 0.0 < c < 1.0
    # put-call parity on deltas: call - put = 1
    assert abs(c - p - 1.0) < 1e-9


def test_delta_decreases_as_strike_moves_away_from_spot():
    far = abs(bsm.delta(100, 80, 45, 0.3, 0.04, "put"))
    near = abs(bsm.delta(100, 95, 45, 0.3, 0.04, "put"))
    assert far < near


def test_delta_increases_with_time_and_volatility():
    short = abs(bsm.delta(100, 95, 10, 0.3, 0.04, "put"))
    long = abs(bsm.delta(100, 95, 60, 0.3, 0.04, "put"))
    assert short < long
    low_vol = abs(bsm.delta(100, 95, 45, 0.15, 0.04, "put"))
    high_vol = abs(bsm.delta(100, 95, 45, 0.60, 0.04, "put"))
    assert low_vol < high_vol


def test_degenerate_inputs_do_not_raise():
    # at expiry, delta is a step function rather than 0.5
    assert bsm.delta(95, 100, 0, 0.3, 0.04, "put") == -1.0   # ITM put
    assert bsm.delta(100, 95, 0, 0.3, 0.04, "put") == 0.0    # OTM put
    assert bsm.delta(100, 105, 0, 0.3, 0.04, "call") == 0.0
    assert bsm.delta(100, 95, 30, 0.0, 0.04, "put") == 0.0
    assert bsm.put_price(100, 95, 0, 0.3, 0.04) == 0.0


def test_put_call_parity_holds():
    s, k, d, v, r = 100.0, 105.0, 60, 0.35, 0.04
    c = bsm.call_price(s, k, d, v, r)
    p = bsm.put_price(s, k, d, v, r)
    expected = s - k * math.exp(-r * d / 365)
    assert abs((c - p) - expected) < 1e-8


def test_historical_volatility_of_flat_series_is_zero():
    assert bsm.historical_volatility([100.0] * 30) == 0.0
    assert bsm.historical_volatility([100.0]) == 0.0


def test_historical_volatility_is_positive_for_a_moving_series():
    closes = [100 + (5 if i % 2 else -5) for i in range(40)]
    assert bsm.historical_volatility(closes) > 0.1


def test_average_true_range():
    highs = [10, 11, 12, 13]
    lows = [9, 10, 11, 12]
    closes = [9.5, 10.5, 11.5, 12.5]
    assert bsm.average_true_range(highs, lows, closes, window=3) > 0

def test_implied_move_scales_with_sqrt_time():
    one = bsm.implied_move(100, 30, 0.4)
    four = bsm.implied_move(100, 120, 0.4)
    assert abs(four / one - 2.0) < 1e-9
