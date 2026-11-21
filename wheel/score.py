"""Scoring, gates, and the next-action rules. Pure functions over plain data.

Two hard rules encoded here rather than left to prose:
  * a contract outside a liquidity gate is *excluded*, not scored low;
  * a recommendation always carries its flags, so the reason is never hidden.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .market import Contract, Quote

# delta band per profile: (min, max)
DELTA_BANDS = {
    "conservative": (0.10, 0.22),
    "balanced": (0.10, 0.35),
    "aggressive": (0.25, 0.45),
}

# Covered calls need a wider band than puts: a 0.40-delta call is an ordinary
# covered call, whereas a 0.40-delta put is an aggressive cash-secured put.
CALL_DELTA_BANDS = {
    "conservative": (0.10, 0.30),
    "balanced": (0.10, 0.50),
    "aggressive": (0.20, 0.70),
}

# weight tables per profile
WEIGHTS = {
    "conservative": {"yield": 0.22, "cushion": 0.34, "iv": 0.20, "liquidity": 0.16, "trend": 0.08},
    "balanced": {"yield": 0.30, "cushion": 0.25, "iv": 0.20, "liquidity": 0.15, "trend": 0.10},
    "aggressive": {"yield": 0.38, "cushion": 0.18, "iv": 0.18, "liquidity": 0.14, "trend": 0.12},
}

# minimum cushion (distance to breakeven, as % of spot) per profile
MIN_CUSHION = {"conservative": 12.0, "balanced": 5.0, "aggressive": 2.0}


@dataclass
class Candidate:
    contract: Contract
    quote: Quote
    mid: float
    premium_yield: float       # % of spot
    annualized: float          # % per year
    breakeven: float
    cushion_pct: float         # (spot - breakeven) / spot * 100
    collateral: float
    contracts_affordable: int
    iv_hv: float
    range_pos: float
    flags: list[str] = field(default_factory=list)
    gates_failed: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    score: float = 0.0

    @property
    def label(self) -> str:
        return self.contract.contract_symbol

    @property
    def ticket(self) -> str:
        leg = "P" if self.contract.kind == "put" else "C"
        return f"{self.contract.symbol} {self.contract.expiry} ${self.contract.strike:g}{leg}"

    @property
    def passes(self) -> bool:
        return not self.gates_failed


# --------------------------------------------------------------------------
# gates
# --------------------------------------------------------------------------

def liquidity_gates(c: Contract, min_oi: int, max_spread_pct: float) -> list[str]:
    failed = []
    if c.bid <= 0:
        failed.append("no bid")
    if c.mid <= 0:
        failed.append("no price")
    if c.iv <= 0:
        failed.append("no IV")
    if c.oi < min_oi:
        failed.append(f"OI {c.oi} < {min_oi}")
    if c.spread_pct > max_spread_pct:
        failed.append(f"spread {c.spread_pct:.0f}% > {max_spread_pct:g}%")
    return failed


def _days_to(date_str: str | None) -> int | None:
    if not date_str:
        return None
    try:
        return (date.fromisoformat(date_str) - date.today()).days
    except ValueError:
        return None


def risk_flags(c: Contract, q: Quote, cushion_pct: float) -> list[str]:
    flags: list[str] = []
    earn = q.next_earnings()
    if earn is not None and earn <= date.fromisoformat(c.expiry):
        flags.append(f"earnings {earn.isoformat()} before expiry")

    ex = _days_to(q.ex_div)
    if ex is not None and 0 <= ex <= c.dte:
        if c.kind == "put" and c.strike >= q.price * 0.98:
            flags.append(f"ex-div in {ex}d, strike near spot")
        elif c.kind == "call":
            flags.append(f"ex-div in {ex}d, early-assignment risk")

    if c.kind == "put" and cushion_pct < 2.0:
        flags.append("thin cushion")
    if c.kind == "call" and cushion_pct < 3.0:
        flags.append("strike close to spot")
    if c.kind == "put" and q.drawdown_pct < -35:
        flags.append(f"falling knife ({q.drawdown_pct:.0f}% from 52w high)")
    if c.kind == "put" and q.range_pos < 0.15:
        flags.append("near 52-week low")
    if q.range_pos > 0.95 and c.kind == "call":
        flags.append("near 52-week high")
    if q.hv > 0 and c.iv > 0 and c.iv / q.hv > 1.8:
        flags.append("IV much richer than realised")
    return flags


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------

def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def score_candidate(c: Candidate, profile: str) -> Candidate:
    w = WEIGHTS.get(profile, WEIGHTS["balanced"])
    # annualized yield: 60% -> 0, 400% -> 1
    s_yield = _clamp01(c.annualized / 400.0)
    # cushion: 0% -> 0, 25% -> 1
    s_cushion = _clamp01(c.cushion_pct / 25.0)
    # IV richness vs realised: 0.8 -> 0, 1.8 -> 1
    s_iv = _clamp01((c.iv_hv - 0.8) / 1.0) if c.iv_hv > 0 else 0.3
    # liquidity: OI 100 -> 0.2, 5000 -> 1; spread narrows it
    s_liq = _clamp01(0.2 + min(c.contract.oi, 5000) / 5000.0 * 0.8)
    s_liq *= _clamp01(1.0 - c.contract.spread_pct / 25.0)
    # trend: prefer above SMA50, penalise sitting at the high
    s_trend = 0.5
    if c.quote.sma50 > 0:
        s_trend = 0.7 if c.quote.price > c.quote.sma50 else 0.35
    if c.quote.range_pos > 0.95:
        s_trend -= 0.2
    s_trend = _clamp01(s_trend)

    c.scores = {
        "yield": round(s_yield * w["yield"] * 100, 1),
        "cushion": round(s_cushion * w["cushion"] * 100, 1),
        "iv": round(s_iv * w["iv"] * 100, 1),
        "liquidity": round(s_liq * w["liquidity"] * 100, 1),
        "trend": round(s_trend * w["trend"] * 100, 1),
    }
    c.score = round(sum(c.scores.values()), 1)
    return c


def build_candidate(c: Contract, q: Quote, profile: str,
                    min_oi: int, max_spread_pct: float) -> Candidate:
    mid = c.mid
    premium_yield = (mid / q.price * 100.0) if q.price > 0 else 0.0
    annualized = premium_yield * 365.0 / c.dte if c.dte > 0 else 0.0
    if c.kind == "put":
        # protective cushion below spot before losses begin
        breakeven = c.strike - mid
        cushion = ((q.price - breakeven) / q.price * 100.0) if q.price > 0 else 0.0
    else:
        # for a covered call the relevant margin is spot up to the strike
        breakeven = c.strike + mid
        cushion = ((c.strike - q.price) / q.price * 100.0) if q.price > 0 else 0.0
    iv_hv = (c.iv / q.hv) if q.hv > 0 else 0.0

    cand = Candidate(
        contract=c,
        quote=q,
        mid=mid,
        premium_yield=premium_yield,
        annualized=annualized,
        breakeven=breakeven,
        cushion_pct=cushion,
        collateral=c.collateral,
        contracts_affordable=0,
        iv_hv=iv_hv,
        range_pos=q.range_pos,
    )
    cand.gates_failed = liquidity_gates(c, min_oi, max_spread_pct)
    cand.flags = risk_flags(c, q, cushion)
    return score_candidate(cand, profile)


def filter_by_delta(cands: list[Candidate], profile: str, kind: str = "put") -> list[Candidate]:
    """Mark out-of-band contracts as rejected rather than dropping them, so the
    rejection summary can explain a thin or empty scan."""
    bands = CALL_DELTA_BANDS if kind == "call" else DELTA_BANDS
    lo, hi = bands.get(profile, bands["balanced"])
    for c in cands:
        if not (lo <= c.contract.delta <= hi):
            c.gates_failed = c.gates_failed + [f"delta {c.contract.delta:.2f} outside {lo:g}-{hi:g}"]
    return cands


def assign_affordability(cands: list[Candidate], free_cash: float) -> None:
    for c in cands:
        c.contracts_affordable = int(free_cash // c.collateral) if c.collateral > 0 else 0
        if c.contracts_affordable < 1:
            c.gates_failed = c.gates_failed + [
                f"needs ${c.collateral:,.0f} collateral, ${free_cash:,.0f} free"
            ]


def rank_calls(cands: list[Candidate], top_n: int = 8) -> list[Candidate]:
    """Rank covered calls by income per dollar of basis, not raw score.

    A far-OTM call can win the generic score on "cushion" while paying almost
    nothing. For a covered call the useful ordering is annualized return on the
    shares you already own, with a small nudge toward being likely to be called
    away when the strike is still above basis.
    """
    passing = [c for c in cands if c.passes]

    def key(c: Candidate) -> float:
        on_basis = (c.mid / c.contract.strike) if c.contract.strike else 0.0
        annual = on_basis * 365.0 / c.contract.dte if c.contract.dte > 0 else 0.0
        # prefer a call that is actually likely to be exercised, but never below 0.15
        exercisable = min(1.0, max(0.0, c.contract.delta / 0.35))
        return annual * 100.0 * (0.75 + 0.25 * exercisable)

    passing.sort(key=key, reverse=True)
    return passing[:top_n]


def rank(cands: list[Candidate], top_n: int = 8) -> list[Candidate]:
    passing = [c for c in cands if c.passes]
    passing.sort(key=lambda c: c.score, reverse=True)
    return passing[:top_n]


def rejected_summary(cands: list[Candidate]) -> dict[str, int]:
    """Count why contracts were dropped, so an empty result is explainable."""
    counts: dict[str, int] = {}
    for c in cands:
        for reason in c.gates_failed:
            key = reason.split(" ")[0]
            counts[key] = counts.get(key, 0) + 1
    return counts


# --------------------------------------------------------------------------
# next-action rules
# --------------------------------------------------------------------------

@dataclass
class Action:
    text: str
    urgency: str  # "high" | "normal" | "idle"
    jump: str | None = None      # screen key to jump to
    symbol: str | None = None


def next_action(short, spot: float, mark: float, lot, quote: Quote | None) -> Action:
    """One recommended action for an open short option or idle share lot."""
    if short is None:
        # idle shares
        basis = lot.basis if lot else 0.0
        return Action(
            text=f"Idle shares — sell a call above ${basis:,.2f} basis",
            urgency="normal", jump="calls", symbol=lot.symbol if lot else None,
        )

    dte = short.dte
    premium_now = mark if mark > 0 else 0.0
    captured = ((short.premium - premium_now) / short.premium * 100.0) if short.premium > 0 else 0.0
    itm = (spot <= short.strike) if short.kind == "put" else (spot >= short.strike)

    if dte < 0:
        return Action(text="Expired — run sync to classify", urgency="high", symbol=short.symbol)

    if short.kind == "put":
        if captured >= 80 and dte <= 7:
            return Action(
                text=f"{captured:.0f}% captured, {dte}d left — close or let expire",
                urgency="normal", symbol=short.symbol,
            )
        if itm:
            if dte <= 3:
                return Action(
                    text="Deep in the money into expiry — expect assignment",
                    urgency="high", jump="book", symbol=short.symbol,
                )
            return Action(
                text="Threatened — roll out 30-60d lower, or take assignment",
                urgency="high", jump="scan", symbol=short.symbol,
            )
        if dte <= 7:
            return Action(
                text=f"{dte}d left, still OTM — let it expire",
                urgency="normal", symbol=short.symbol,
            )
        return Action(
            text=f"Let run ({dte}d, {captured:.0f}% captured)",
            urgency="normal", symbol=short.symbol,
        )

    # short call
    if quote is not None and quote.ex_div:
        try:
            ex_days = (date.fromisoformat(quote.ex_div) - date.today()).days
        except ValueError:
            ex_days = None
        if ex_days is not None and 0 <= ex_days <= dte and itm:
            return Action(
                text=f"Early-assignment risk (ex-div in {ex_days}d) — roll up/out",
                urgency="high", jump="calls", symbol=short.symbol,
            )
    basis = lot.basis if lot else 0.0
    if itm:
        gain = (short.strike - basis) * 100 * short.contracts if basis else 0.0
        return Action(
            text=f"Called away at ${short.strike:g} = +${gain:,.0f} vs basis — acceptable",
            urgency="normal", symbol=short.symbol,
        )
    if dte <= 7:
        return Action(text=f"{dte}d left, OTM — likely expires, sell another", urgency="normal",
                      jump="calls", symbol=short.symbol)
    return Action(text=f"Let run ({dte}d left)", urgency="normal", symbol=short.symbol)


def covered_call_rows(cands: list[Candidate], basis: float, shares: int) -> list[Candidate]:
    """Keep only calls at or above basis, and size them to shares held."""
    out = []
    for c in cands:
        if c.contract.strike < basis:
            c.gates_failed = c.gates_failed + [f"strike below ${basis:,.2f} basis"]
            continue
        c.contracts_affordable = min(c.contracts_affordable, shares // 100)
        if c.contracts_affordable < 1:
            c.gates_failed = c.gates_failed + ["not enough shares"]
            continue
        out.append(c)
    return out
