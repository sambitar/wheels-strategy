"""Black-Scholes primitives. Pure functions, no network, no pandas.

Delta is all the scanner actually needs; everything else here exists so the
detail pane can show a plausible picture and so tests can pin the maths.
"""

from __future__ import annotations

import math

SQRT_2PI = math.sqrt(2.0 * math.pi)


def norm_cdf(x: float) -> float:
    """Standard normal CDF, via the error function (no scipy)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / SQRT_2PI


def _d1_d2(spot: float, strike: float, years: float, vol: float, rate: float) -> tuple[float, float]:
    if years <= 0 or vol <= 0 or spot <= 0 or strike <= 0:
        return 0.0, 0.0
    vt = vol * math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * years) / vt
    return d1, d1 - vt


def delta(spot: float, strike: float, days: float, vol: float, rate: float, kind: str) -> float:
    """Signed Black-Scholes delta. Puts are negative, calls positive.

    At or past expiry delta collapses to a step function: the option is worth
    either 1 or 0, never 0.5.
    """
    years = days / 365.0
    if years <= 0:
        if kind == "call":
            return 1.0 if spot > strike else 0.0
        return -1.0 if spot < strike else 0.0
    if vol <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    d1, _ = _d1_d2(spot, strike, years, vol, rate)
    if kind == "call":
        return norm_cdf(d1)
    return norm_cdf(d1) - 1.0


def call_price(spot: float, strike: float, days: float, vol: float, rate: float) -> float:
    years = days / 365.0
    if years <= 0:
        return max(spot - strike, 0.0)
    d1, d2 = _d1_d2(spot, strike, years, vol, rate)
    return spot * norm_cdf(d1) - strike * math.exp(-rate * years) * norm_cdf(d2)


def put_price(spot: float, strike: float, days: float, vol: float, rate: float) -> float:
    years = days / 365.0
    if years <= 0:
        return max(strike - spot, 0.0)
    d1, d2 = _d1_d2(spot, strike, years, vol, rate)
    return strike * math.exp(-rate * years) * norm_cdf(-d2) - spot * norm_cdf(-d1)


def implied_move(spot: float, days: float, vol: float) -> float:
    """One-sigma expected move over the life of the option, in dollars."""
    if days <= 0 or vol <= 0:
        return 0.0
    return spot * vol * math.sqrt(days / 365.0)


def historical_volatility(closes: list[float], annualize: int = 252) -> float:
    """Annualized close-to-close volatility from a daily series."""
    if len(closes) < 3:
        return 0.0
    rets = []
    for prev, cur in zip(closes, closes[1:]):
        if prev > 0 and cur > 0:
            rets.append(math.log(cur / prev))
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(annualize)


def average_true_range(highs: list[float], lows: list[float], closes: list[float], window: int = 14) -> float:
    """Average true range over the last `window` bars, in dollars."""
    n = min(len(highs), len(lows), len(closes))
    if n < 2:
        return 0.0
    trs = []
    for i in range(1, n):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    tail = trs[-window:]
    return sum(tail) / len(tail) if tail else 0.0
