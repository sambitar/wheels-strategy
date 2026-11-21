"""All Yahoo access lives here. Everything above this module sees plain floats.

If the provider ever changes, this is the only file that needs rewriting.
Failures are captured per symbol rather than raised, so one broken ticker
never takes down a scan.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import bsm
from .config import CACHE_DIR

CACHE_TTL = 600  # seconds

_chain_cache: dict[tuple[str, str], tuple[float, list[dict], list[dict]]] = {}


@dataclass
class Quote:
    symbol: str
    price: float = 0.0
    prev_close: float = 0.0
    change_pct: float = 0.0
    high_52w: float = 0.0
    low_52w: float = 0.0
    avg_volume: float = 0.0
    closes: list[float] = field(default_factory=list)
    sma20: float = 0.0
    sma50: float = 0.0
    hv: float = 0.0
    atr: float = 0.0
    earnings: list[str] = field(default_factory=list)
    ex_div: str | None = None
    expiries: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.price > 0

    @property
    def range_pos(self) -> float:
        """0 = at the 52w low, 1 = at the 52w high."""
        span = self.high_52w - self.low_52w
        if span <= 0:
            return 0.5
        return max(0.0, min(1.0, (self.price - self.low_52w) / span))

    @property
    def drawdown_pct(self) -> float:
        if self.high_52w <= 0:
            return 0.0
        return (self.price / self.high_52w - 1.0) * 100.0

    @property
    def atr_pct(self) -> float:
        return (self.atr / self.price * 100.0) if self.price > 0 else 0.0

    def next_earnings(self) -> date | None:
        best: date | None = None
        for raw in self.earnings:
            try:
                d = date.fromisoformat(raw)
            except ValueError:
                continue
            if d >= date.today() and (best is None or d < best):
                best = d
        return best


@dataclass
class Contract:
    symbol: str
    kind: str  # "put" | "call"
    expiry: str
    strike: float
    bid: float
    ask: float
    last: float
    mid: float
    iv: float
    oi: int
    volume: int
    dte: int
    delta: float = 0.0

    @property
    def spread_pct(self) -> float:
        if self.mid <= 0:
            return 999.0
        return (self.ask - self.bid) / self.mid * 100.0

    @property
    def collateral(self) -> float:
        return self.strike * 100.0

    @property
    def contract_symbol(self) -> str:
        return f"{self.symbol} {self.expiry} {self.strike:g}{'P' if self.kind == 'put' else 'C'}"


# --------------------------------------------------------------------------
# caching
# --------------------------------------------------------------------------

def _cache_path(key: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{key}.json"


def _cache_get(key: str) -> dict | None:
    path = _cache_path(key)
    if not path.exists():
        return None
    try:
        if time.time() - path.stat().st_mtime > CACHE_TTL:
            return None
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _cache_put(key: str, payload: dict) -> None:
    try:
        _cache_path(key).write_text(json.dumps(payload, default=str))
    except (OSError, TypeError):
        pass


def clear_cache() -> None:
    if CACHE_DIR.exists():
        for p in CACHE_DIR.glob("*.json"):
            p.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# quotes
# --------------------------------------------------------------------------

def _fetch_quote(symbol: str) -> Quote:
    import yfinance as yf

    symbol = symbol.upper()
    cached = _cache_get(f"quote_{symbol}")
    if cached:
        return Quote(**cached)

    q = Quote(symbol=symbol)
    try:
        tk = yf.Ticker(symbol)
        fi = tk.fast_info
        price = float(getattr(fi, "last_price", 0) or 0)
        prev = float(getattr(fi, "previous_close", 0) or 0)

        hist = tk.history(period="1y", auto_adjust=False)
        if price <= 0 and not hist.empty:
            price = float(hist["Close"].iloc[-1])
        if prev <= 0 and len(hist) > 1:
            prev = float(hist["Close"].iloc[-2])

        q.price = price
        q.prev_close = prev
        q.change_pct = ((price / prev - 1.0) * 100.0) if prev > 0 else 0.0
        q.high_52w = float(getattr(fi, "year_high", 0) or 0)
        q.low_52w = float(getattr(fi, "year_low", 0) or 0)
        q.avg_volume = float(getattr(fi, "three_month_average_volume", 0) or 0)

        if not hist.empty:
            closes = [float(c) for c in hist["Close"].tolist()]
            highs = [float(h) for h in hist["High"].tolist()]
            lows = [float(l) for l in hist["Low"].tolist()]
            q.closes = closes[-90:]
            if not q.high_52w:
                q.high_52w = max(highs) if highs else 0.0
            if not q.low_52w:
                q.low_52w = min(lows) if lows else 0.0
            if len(closes) >= 20:
                q.sma20 = sum(closes[-20:]) / 20
            if len(closes) >= 50:
                q.sma50 = sum(closes[-50:]) / 50
            q.hv = bsm.historical_volatility(closes)
            q.atr = bsm.average_true_range(highs, lows, closes)

        try:
            cal = tk.calendar or {}
            earn = cal.get("Earnings Date") or []
            if not isinstance(earn, list):
                earn = [earn]
            q.earnings = [d.isoformat() for d in earn if hasattr(d, "isoformat")]
            ex = cal.get("Ex-Dividend Date")
            q.ex_div = ex.isoformat() if hasattr(ex, "isoformat") else None
        except Exception:
            pass

        q.expiries = list(tk.options or ())
        if not q.expiries:
            q.error = "no options listed"
    except Exception as exc:  # noqa: BLE001 - degrade, never crash a scan
        q.error = f"{type(exc).__name__}: {str(exc)[:120]}"

    if q.ok:
        _cache_put(f"quote_{symbol}", {k: v for k, v in q.__dict__.items()})
    return q


def fetch_quotes(symbols: list[str], progress=None) -> dict[str, Quote]:
    """Fetch quotes concurrently. `progress(symbol, done, total)` is optional."""
    symbols = [s.upper().strip() for s in symbols if s and s.strip()]
    out: dict[str, Quote] = {}
    total = len(symbols)
    if not symbols:
        return out
    with ThreadPoolExecutor(max_workers=min(8, max(1, total))) as pool:
        futures = {pool.submit(_fetch_quote, s): s for s in symbols}
        for done, fut in enumerate(as_completed(futures), start=1):
            sym = futures[fut]
            try:
                out[sym] = fut.result()
            except Exception as exc:  # noqa: BLE001
                q = Quote(symbol=sym)
                q.error = f"{type(exc).__name__}: {str(exc)[:120]}"
                out[sym] = q
            if progress:
                progress(sym, done, total)
    return out


# --------------------------------------------------------------------------
# option chains
# --------------------------------------------------------------------------

def _fetch_chain(symbol: str, expiry: str) -> tuple[list[dict], list[dict]]:
    import yfinance as yf

    key = (symbol.upper(), expiry)
    hit = _chain_cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1], hit[2]

    cached = _cache_get(f"chain_{symbol}_{expiry}")
    if cached:
        _chain_cache[key] = (time.time(), cached["puts"], cached["calls"])
        return cached["puts"], cached["calls"]

    tk = yf.Ticker(symbol)
    chain = tk.option_chain(expiry)
    puts = chain.puts.to_dict("records")
    calls = chain.calls.to_dict("records")
    _cache_put(f"chain_{symbol}_{expiry}", {"puts": puts, "calls": calls})
    _chain_cache[key] = (time.time(), puts, calls)
    return puts, calls


def _clean_rows(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        try:
            out.append(
                {
                    "strike": float(r.get("strike", 0) or 0),
                    "bid": float(r.get("bid", 0) or 0),
                    "ask": float(r.get("ask", 0) or 0),
                    "last": float(r.get("lastPrice", 0) or 0),
                    "iv": float(r.get("impliedVolatility", 0) or 0),
                    "oi": int(r.get("openInterest", 0) or 0),
                    "volume": int(r.get("volume", 0) or 0),
                }
            )
        except (TypeError, ValueError):
            continue
    return out


def contracts_for(
    quote: Quote,
    kind: str,
    dte_min: int,
    dte_max: int,
    rate: float,
) -> tuple[list[Contract], list[str]]:
    """Return in-window contracts plus one note per expiry that failed."""
    today = date.today()
    notes: list[str] = []
    out: list[Contract] = []

    for expiry in quote.expiries:
        try:
            d = date.fromisoformat(expiry)
        except ValueError:
            continue
        dte = (d - today).days
        if dte < dte_min or dte > dte_max:
            continue
        try:
            puts, calls = _fetch_chain(quote.symbol, expiry)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{expiry}: {type(exc).__name__}")
            continue
        rows = _clean_rows(puts if kind == "put" else calls)
        for r in rows:
            mid = (r["bid"] + r["ask"]) / 2 if r["bid"] > 0 and r["ask"] > 0 else r["last"]
            c = Contract(
                symbol=quote.symbol,
                kind=kind,
                expiry=expiry,
                strike=r["strike"],
                bid=r["bid"],
                ask=r["ask"],
                last=r["last"],
                mid=mid,
                iv=r["iv"],
                oi=r["oi"],
                volume=r["volume"],
                dte=dte,
            )
            if c.iv > 0:
                c.delta = abs(bsm.delta(quote.price, c.strike, dte, c.iv, rate, kind))
            out.append(c)
    return out, notes


def expiries_in_window(quote: Quote, dte_min: int, dte_max: int) -> list[tuple[str, int]]:
    today = date.today()
    out = []
    for expiry in quote.expiries:
        try:
            dte = (date.fromisoformat(expiry) - today).days
        except ValueError:
            continue
        if dte_min <= dte <= dte_max:
            out.append((expiry, dte))
    return out
