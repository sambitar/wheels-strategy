"""Headless engine: everything both the TUI and the JSON CLI call.

Returns plain dicts / dataclasses so the TUI renders it and the DeepSeek agent
reads it as JSON. No printing, no terminal assumptions.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Callable

from .config import Config
from . import ledger, market, score


def _sym_list(symbols: str | list[str] | None, cfg: Config) -> list[str]:
    if not symbols:
        return list(cfg.symbols)
    if isinstance(symbols, str):
        return [s.strip().upper() for s in symbols.split(",") if s.strip()]
    return [s.strip().upper() for s in symbols if s.strip()]


def _free_cash(cfg: Config, book: ledger.Book) -> float:
    return max(0.0, cfg.budget - book.committed)


def _candidate_dict(c: score.Candidate) -> dict:
    ct = c.contract
    return {
        "symbol": ct.symbol,
        "kind": ct.kind,
        "expiry": ct.expiry,
        "strike": ct.strike,
        "dte": ct.dte,
        "spot": round(c.quote.price, 2),
        "bid": ct.bid,
        "ask": ct.ask,
        "mid": round(c.mid, 2),
        "delta": round(ct.delta, 3),
        "iv": round(ct.iv, 4),
        "oi": ct.oi,
        "volume": ct.volume,
        "spread_pct": round(ct.spread_pct, 1),
        "premium_yield_pct": round(c.premium_yield, 2),
        "annualized_pct": round(c.annualized, 1),
        "breakeven": round(c.breakeven, 2),
        "cushion_pct": round(c.cushion_pct, 2),
        "collateral": round(c.collateral, 0),
        "contracts_affordable": c.contracts_affordable,
        "iv_hv": round(c.iv_hv, 2),
        "score": c.score,
        "scores": c.scores,
        "flags": c.flags,
        "ticket": c.ticket,
        "earnings": c.quote.next_earnings().isoformat() if c.quote.next_earnings() else None,
    }


def scan(
    cfg: Config,
    symbols: str | list[str] | None = None,
    progress: Callable[[str, int, int], None] | None = None,
    cash_override: float | None = None,
) -> dict:
    """Rank cash-secured puts across the watchlist.

    `cash_override` projects against a different capital figure — useful when
    you are planning the *next* put while an existing one still holds your
    collateral. The response says which figure was used.
    """
    syms = _sym_list(symbols, cfg)
    book = ledger.load_book()
    free = _free_cash(cfg, book)
    capital = free if cash_override is None else max(0.0, float(cash_override))
    projected = cash_override is not None and abs(capital - free) > 0.5

    quotes = market.fetch_quotes(syms, progress=progress)
    results: list[score.Candidate] = []
    errors: list[dict] = []
    per_symbol: dict[str, dict] = {}

    for sym, q in quotes.items():
        if not q.ok:
            errors.append({"symbol": sym, "error": q.error or "no quote"})
            per_symbol[sym] = {"error": q.error, "price": 0}
            continue

        contracts, notes = market.contracts_for(q, "put", cfg.dte_min, cfg.dte_max, cfg.risk_free_rate)
        cands = [score.build_candidate(c, q, cfg.profile, cfg.min_open_interest, cfg.max_spread_pct)
                 for c in contracts]
        cands = score.filter_by_delta(cands, cfg.profile)
        score.assign_affordability(cands, capital)
        results.extend(cands)
        per_symbol[sym] = {
            "price": round(q.price, 2),
            "change_pct": round(q.change_pct, 2),
            "expiries": len(market.expiries_in_window(q, cfg.dte_min, cfg.dte_max)),
            "contracts_seen": len(contracts),
            "qualifying": sum(1 for c in cands if c.passes),
            "notes": notes,
            "earnings": q.next_earnings().isoformat() if q.next_earnings() else None,
            "range_position": round(q.range_pos, 2),
            "hv": round(q.hv, 3),
        }

    ranked = score.rank(results, cfg.top_n)
    top = [_candidate_dict(c) for c in ranked]
    rejected = [c for c in results if not c.passes]
    return {
        "kind": "scan",
        "as_of": date.today().isoformat(),
        "profile": cfg.profile,
        "budget": cfg.budget,
        "free_cash": round(free, 0),
        "capital_used": round(capital, 0),
        "capital_mode": "projected" if projected else "free now",
        "dte_window": [cfg.dte_min, cfg.dte_max],
        "candidates": top,
        "rejected_reasons": score.rejected_summary(rejected),
        "per_symbol": per_symbol,
        "errors": errors,
        "book": book_summary(cfg, book, {s: q.price for s, q in quotes.items() if q.ok}),
    }


def plan(
    cfg: Config,
    symbol: str | None = None,
    progress=None,
) -> dict:
    """Forward-looking plan: what happens if the open puts are assigned, and
    which covered call to sell first.

    This is the piece that lets you decide *before* assignment. For each open
    put it projects shares, cost basis, cash needed, and ranks the calls that
    would be sellable against those shares — filtered to at/above basis.
    """
    book = ledger.load_book()
    puts = book.puts()
    if symbol:
        puts = [s for s in puts if s.symbol == symbol.upper()]
    if not puts:
        return {
            "kind": "plan",
            "mode": "projection",
            "message": "No open puts. Nothing to project into shares.",
            "positions": [],
        }

    quotes = market.fetch_quotes(sorted({s.symbol for s in puts}), progress=progress)

    positions = []
    total_cash = 0.0
    total_shares = 0
    for short in puts:
        q = quotes.get(short.symbol)
        if q is None or not q.ok:
            positions.append({"symbol": short.symbol, "error": q.error if q else "no quote"})
            continue

        shares = short.contracts * 100
        basis = short.strike - short.premium
        cash_needed = short.strike * 100 * short.contracts
        total_cash += cash_needed
        total_shares += shares

        # what the position looks like the day you take the shares
        projected = {
            "symbol": short.symbol,
            "id": short.id,
            "from": short.label,
            "contracts": short.contracts,
            "shares": shares,
            "strike": short.strike,
            "basis": round(basis, 2),
            "cash_needed": round(cash_needed, 0),
            "spot": round(q.price, 2),
            "unrealized_at_assignment": round((q.price - basis) * shares, 0),
            "break_even_spot": round(basis, 2),
        }

        # the covered call you would sell. Shares exist in projection only, so
        # afford the calls ourselves rather than relying on ledger state.
        contracts, notes = market.contracts_for(
            q, "call", cfg.dte_min, cfg.dte_max, cfg.risk_free_rate
        )
        cands = [
            score.build_candidate(c, q, cfg.profile, cfg.min_open_interest, cfg.max_spread_pct)
            for c in contracts
        ]
        cands = score.filter_by_delta(cands, cfg.profile, kind="call")
        for c in cands:
            c.contracts_affordable = min(short.contracts, max(1, c.contracts_affordable or short.contracts))
        cands = score.covered_call_rows(cands, basis, shares)
        ranked = score.rank_calls(cands, cfg.top_n)

        call_rows = []
        for c in ranked:
            d = _candidate_dict(c)
            d["return_on_shares_pct"] = (
                round(c.mid * 100 * c.contracts_affordable / (basis * c.contracts_affordable * 100) * 100, 2)
                if basis > 0
                else 0.0
            )
            d["called_away_gain"] = round((c.contract.strike - basis) * 100 * c.contracts_affordable, 0)
            d["called_away_total"] = round(d["called_away_gain"] + c.mid * 100 * c.contracts_affordable, 0)
            d["projected"] = True   # shares behind this call exist only if assigned
            call_rows.append(d)

        projected["calls"] = call_rows
        projected["notes"] = notes
        projected["rejected"] = score.rejected_summary([c for c in cands if not c.passes])
        positions.append(projected)

    return {
        "kind": "plan",
        "mode": "projection",  # nothing in here is real until assignment happens
        "as_of": date.today().isoformat(),
        "positions": positions,
        "totals": {
            "cash_needed_if_all_assigned": round(total_cash, 0),
            "shares_if_all_assigned": total_shares,
            "free_cash_now": round(max(0.0, cfg.budget - book.committed), 0),
            "cash_shortfall": round(max(0.0, total_cash - cfg.budget), 0),
        },
    }


def calls(
    cfg: Config,
    symbol: str | None = None,
    spot_override: float | None = None,
    progress: Callable[[str, int, int], None] | None = None,
) -> dict:
    """Covered calls for shares actually held, filtered to at-or-above basis."""
    book = ledger.load_book()
    lots = book.lots
    if symbol:
        lots = {symbol.upper(): lots[symbol.upper()]} if symbol.upper() in lots else {}

    if not lots:
        return {
            "kind": "calls",
            "mode": "actual",
            "message": "No shares held. Record an assignment first (journal → assign).",
            "positions": [],
            "book": book_summary(cfg, book),
        }

    quotes = market.fetch_quotes(list(lots), progress=progress)
    positions = []
    for sym, lot in lots.items():
        # Calls you already SOLD are positions (Book), and the shares they
        # pledge are no longer free. Candidates are sized to what is left.
        sold = [c for c in book.calls() if c.symbol == sym]
        pledged = 100 * sum(c.contracts for c in sold)
        free_shares = max(0, lot.shares - pledged)
        q = quotes.get(sym)
        if q is None or not q.ok:
            positions.append({
                "symbol": sym, "error": q.error if q else "no quote",
                "shares": lot.shares, "basis": round(lot.basis, 2),
                "sold_calls": [
                    {"label": c.label, "contracts": c.contracts, "strike": c.strike,
                     "expiry": c.expiry, "premium": c.premium, "dte": c.dte}
                    for c in sold
                ],
                "pledged_shares": pledged,
                "free_shares": free_shares,
            })
            continue
        contracts, notes = market.contracts_for(q, "call", cfg.dte_min, cfg.dte_max, cfg.risk_free_rate)
        cands = [score.build_candidate(c, q, cfg.profile, cfg.min_open_interest, cfg.max_spread_pct)
                 for c in contracts]
        cands = score.filter_by_delta(cands, cfg.profile, kind="call")
        # A covered call's collateral is the stock, not cash — the budget must
        # never gate it out. Sizing is strictly the shares not already pledged.
        for c in cands:
            c.contracts_affordable = free_shares // 100
        cands = score.covered_call_rows(cands, lot.basis, free_shares)
        ranked = score.rank_calls(cands, cfg.top_n)
        rows = []
        for c in ranked:
            d = _candidate_dict(c)
            d["return_on_shares_pct"] = round(
                c.mid * 100 * c.contracts_affordable / (lot.basis * c.contracts_affordable * 100) * 100, 2
            ) if lot.basis > 0 and c.contracts_affordable else 0.0
            d["called_away_gain"] = round((c.contract.strike - lot.basis) * 100 * c.contracts_affordable, 0)
            rows.append(d)
        positions.append(
            {
                "symbol": sym,
                "price": round(q.price, 2),
                "shares": lot.shares,
                "basis": round(lot.basis, 2),
                "unrealized": round((q.price - lot.basis) * lot.shares, 0),
                "sold_calls": [
                    {"label": c.label, "contracts": c.contracts, "strike": c.strike,
                     "expiry": c.expiry, "premium": c.premium, "dte": c.dte}
                    for c in sold
                ],
                "pledged_shares": pledged,
                "free_shares": free_shares,
                "candidates": rows,
                "notes": notes,
                "rejected": score.rejected_summary([c for c in cands if not c.passes]),
            }
        )
    return {
        "kind": "calls",
        "mode": "actual",  # every candidate here is covered by shares really held
        "as_of": date.today().isoformat(),
        "positions": positions,
        "book": book_summary(cfg, book, {s: q.price for s, q in quotes.items() if q.ok}),
    }


def status(cfg: Config, progress=None) -> dict:
    """Every open position with a live mark and one recommended action."""
    book = ledger.load_book()
    symbols = sorted({s.symbol for s in book.shorts.values()} | set(book.lots))
    quotes = market.fetch_quotes(symbols, progress=progress) if symbols else {}

    rows = []
    for short in book.shorts.values():
        q = quotes.get(short.symbol)
        spot = q.price if q and q.ok else 0.0
        mark = 0.0
        if q and q.ok:
            contracts, _ = market.contracts_for(q, short.kind, short.dte, short.dte, cfg.risk_free_rate)
            match = [c for c in contracts if abs(c.strike - short.strike) < 0.01]
            if match:
                mark = match[0].mid
        lot = book.lots.get(short.symbol)
        action = score.next_action(short, spot, mark, lot, q)
        rows.append(
            {
                "id": short.id,
                "label": short.label,
                "symbol": short.symbol,
                "kind": short.kind,
                "strike": short.strike,
                "expiry": short.expiry,
                "contracts": short.contracts,
                "premium": short.premium,
                "dte": short.dte,
                "spot": round(spot, 2),
                "mark": round(mark, 2),
                "unrealized": round((short.premium - mark) * 100 * short.contracts, 0) if mark else None,
                "itm": (spot > 0) and ((spot <= short.strike) if short.kind == "put" else (spot >= short.strike)),
                "action": action.text,
                "urgency": action.urgency,
                "jump": action.jump,
            }
        )

    for sym, lot in book.lots.items():
        if any(r["symbol"] == sym and r["kind"] == "call" for r in rows):
            continue
        q = quotes.get(sym)
        action = score.next_action(None, q.price if q and q.ok else 0.0, 0.0, lot, q)
        rows.append(
            {
                "id": f"{sym}:shares",
                "label": f"{sym} {lot.shares} shares",
                "symbol": sym,
                "kind": "shares",
                "strike": 0.0,
                "expiry": "",
                "contracts": lot.shares // 100,
                "shares": lot.shares,
                "premium": 0.0,
                "dte": None,
                "spot": round(q.price, 2) if q and q.ok else 0.0,
                "mark": 0.0,
                "unrealized": round((q.price - lot.basis) * lot.shares, 0) if q and q.ok else None,
                "basis": round(lot.basis, 2),
                "cost": round(lot.cost, 4),
                "ids": list(lot.ids),
                "itm": False,
                "action": action.text,
                "urgency": action.urgency,
                "jump": action.jump,
            }
        )

    return {
        "kind": "status",
        "as_of": date.today().isoformat(),
        "positions": rows,
        "book": book_summary(cfg, book, {s: q.price for s, q in quotes.items() if q.ok}),
        "warnings": book.warnings,
    }


def book_summary(cfg: Config, book: ledger.Book, spots: dict[str, float] | None = None) -> dict:
    # Identity: total = budget + realized = cash + collateral + shares at
    # cost. With live quotes, total_market = that same identity but pricing
    # shares at the market — what walking away today is actually worth.
    # Symbols without a quote fall back to cost: never a made-up number.
    spots = spots or {}
    total = cfg.budget + book.realized_pnl
    unrealized = 0.0
    shares_live = 0.0
    for sym, lot in book.lots.items():
        px = spots.get(sym) or lot.cost
        unrealized += (px - lot.cost) * lot.shares
        shares_live += px * lot.shares
    collateral = sum(s.collateral for s in book.puts())
    return {
        "budget": cfg.budget,
        "total": round(total, 0),
        "total_market": round(total + unrealized, 0),
        "unrealized": round(unrealized, 0),
        "cash": round(total - book.committed, 0),
        "committed": round(book.committed, 0),
        "collateral": round(collateral, 0),
        "shares_cost": round(book.shares_cost, 0),
        "shares_live": round(shares_live, 0),
        "free_cash": round(max(0.0, cfg.budget - book.committed), 0),
        "open_puts": len(book.puts()),
        "open_calls": len(book.calls()),
        "shares_value": round(book.share_value, 0),
        "premium_collected": round(book.premium_collected, 0),
        "realized_pnl": round(book.realized_pnl, 0),
        "holdings": {s: {"shares": l.shares, "basis": round(l.basis, 2)} for s, l in book.lots.items()},
    }


def sync_preview(cfg: Config, progress=None) -> dict:
    book = ledger.load_book()
    symbols = sorted({s.symbol for s in book.shorts.values()})
    quotes = market.fetch_quotes(symbols, progress=progress) if symbols else {}
    spots = {s: q.price for s, q in quotes.items() if q.ok}
    proposals, skipped = ledger.propose_expired(book, spots)
    return {
        "kind": "sync_preview",
        "proposed": [
            {"id": p.key, "parent": p.parent, "label": p.label, "action": p.action, "reason": p.reason}
            for p in proposals
        ],
        "skipped": [{"label": s["short"].label, "reason": s["reason"]} for s in skipped],
    }


def sync_apply(cfg: Config, ids: list[str] | None, progress=None) -> dict:
    preview = sync_preview(cfg, progress=progress)
    items = preview["proposed"]
    if ids:
        wanted = set(ids)
        items = [i for i in items if i["id"] in wanted]
    applied = []
    for item in items:
        action = item["action"]
        if action == "assign":
            ledger.assign_position(item["parent"])
        elif action == "expire":
            ledger.expire_position(item["parent"])
        elif action == "called_away":
            ledger.called_away(item["parent"])
        else:
            continue
        applied.append(item)
    return {"kind": "sync_apply", "applied": applied, "skipped": preview["skipped"]}


def journal(days: int = 365) -> dict:
    book = ledger.load_book()
    events = ledger.read_events()
    return {
        "kind": "journal",
        "events": events[-200:],
        "event_count": len(events),
        "realized": [asdict(r) for r in book.realized],
        "book": {
            "premium_collected": round(book.premium_collected, 0),
            "realized_pnl": round(book.realized_pnl, 0),
            "closed_positions": len(book.realized),
            "wins": sum(1 for r in book.realized if r.pnl > 0),
            "losses": sum(1 for r in book.realized if r.pnl <= 0),
        },
        "warnings": book.warnings,
    }
