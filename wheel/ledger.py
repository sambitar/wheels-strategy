"""Append-only trade journal.

The file is the truth; positions are *derived* by replaying it. Nothing is ever
rewritten, so a bad entry is corrected by appending a `fix` event rather than
editing history. That also means every derived number can be recomputed from
`state/trades.jsonl` alone.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .config import JOURNAL_PATH

EVENT_TYPES = ("open", "close", "expire", "assign", "called_away", "roll", "fix")


# --------------------------------------------------------------------------
# state objects
# --------------------------------------------------------------------------

@dataclass
class Short:
    """An open short option position."""
    id: str
    symbol: str
    kind: str
    strike: float
    expiry: str
    contracts: int
    premium: float
    opened: str

    @property
    def collateral(self) -> float:
        return self.strike * 100.0 * self.contracts

    @property
    def dte(self) -> int:
        return (date.fromisoformat(self.expiry) - date.today()).days

    @property
    def label(self) -> str:
        leg = "P" if self.kind == "put" else "C"
        return f"{self.symbol} {self.expiry} {self.strike:g}{leg}"


@dataclass
class Lot:
    """Shares owned.

    Two numbers, deliberately separate:
      basis  net of premium, used to filter covered-call strikes (strike - premium)
      cost   the actual price paid per share (the strike), used for P/L

    Keeping them apart stops the put premium being counted twice: once when it is
    booked as realized on assignment, and again inside the basis.
    """
    symbol: str
    shares: int
    basis: float
    ids: list[str] = field(default_factory=list)
    cost: float = 0.0

    def __post_init__(self) -> None:
        if not self.cost:
            self.cost = self.basis

    @property
    def value_at(self) -> float:
        return self.basis * self.shares


@dataclass
class Realized:
    symbol: str
    kind: str
    strike: float
    expiry: str
    contracts: int
    outcome: str
    pnl: float
    premium: float
    ts: str


@dataclass
class Book:
    shorts: dict[str, Short] = field(default_factory=dict)
    lots: dict[str, Lot] = field(default_factory=dict)
    realized: list[Realized] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def puts(self) -> list[Short]:
        return [s for s in self.shorts.values() if s.kind == "put"]

    def calls(self) -> list[Short]:
        return [s for s in self.shorts.values() if s.kind == "call"]

    @property
    def shares_cost(self) -> float:
        """Cash actually spent on shares still held (the strike paid at assignment)."""
        return sum(l.cost * l.shares for l in self.lots.values())

    @property
    def committed(self) -> float:
        """Capital tied up in the wheel: open-put collateral plus shares held.

        Assignment does *not* free your cash — it converts collateral into
        stock, which you paid for at the strike. The capital stays committed
        until the shares are called away (or sold); until then it is not
        available to secure another put.
        """
        return sum(s.collateral for s in self.puts()) + self.shares_cost

    @property
    def share_value(self) -> float:
        return sum(lot.value_at for lot in self.lots.values())

    @property
    def premium_collected(self) -> float:
        """Premium banked from everything that has finished."""
        return sum(r.premium for r in self.realized)

    @property
    def realized_pnl(self) -> float:
        return sum(r.pnl for r in self.realized)

    def symbol_exposure(self, symbol: str) -> tuple[int, int]:
        """(open put contracts, shares) already held on this symbol."""
        puts = sum(s.contracts for s in self.puts() if s.symbol == symbol)
        shares = self.lots[symbol].shares if symbol in self.lots else 0
        return puts, shares


# --------------------------------------------------------------------------
# journal read/write
# --------------------------------------------------------------------------

def read_events(path: Path | None = None) -> list[dict]:
    path = path or JOURNAL_PATH
    if not path.exists():
        return []
    events = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def append_event(event: dict, path: Path | None = None) -> dict:
    path = path or JOURNAL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    event.setdefault("ts", datetime.now().isoformat(timespec="seconds"))
    with path.open("a") as fh:
        fh.write(json.dumps(event) + "\n")
    return event


def _event_id(event: dict) -> str:
    kind = event.get("kind", "put")
    stamp = str(event.get("opened_on") or event.get("ts", ""))[:10]
    return "-".join(
        [
            event.get("symbol", "?"),
            kind,
            str(event.get("strike", 0)),
            event.get("expiry", "?"),
            str(event.get("contracts", 1)),
            stamp or "na",
        ]
    )


def find_duplicate(candidate: dict, path: Path | None = None) -> dict | None:
    """Return an existing event that looks like the same trade."""
    for ev in read_events(path):
        if ev.get("type") != "open":
            continue
        if (
            ev.get("symbol") == candidate.get("symbol")
            and ev.get("kind") == candidate.get("kind", "put")
            and float(ev.get("strike", 0)) == float(candidate.get("strike", 0))
            and ev.get("expiry") == candidate.get("expiry")
            and int(ev.get("contracts", 1)) == int(candidate.get("contracts", 1))
        ):
            return ev
    return None


# --------------------------------------------------------------------------
# fixes: correct a trade with what REALLY happened
# --------------------------------------------------------------------------

# What the broker actually gave you, vs what the journal derived:
#   premium/strike/contracts/expiry  the ticket itself
#   cost      per-share price really paid when a put was assigned
#   price     per-share price really received when shares were called away
#   shares    shares really delivered (pin risk / partial assignment)
FIX_SET_FIELDS = ("premium", "strike", "expiry", "contracts", "cost", "shares", "price")


def _event_label(ev: dict) -> str:
    """The display label of a position from its raw open event."""
    leg = "P" if ev.get("kind", "put") == "put" else "C"
    return f"{ev.get('symbol', '?')} {ev.get('expiry', '')} {float(ev.get('strike', 0)):g}{leg}"


def find_position_id(handle: str, path: Path | None = None) -> str | None:
    """Resolve a position id *or* display label to the canonical id."""
    for ev in read_events(path):
        if ev.get("type") != "open":
            continue
        sid = ev.get("id") or _event_id(ev)
        if sid == handle or _event_label(ev) == handle:
            return sid
    return None


def _validate_overrides(set_fields: dict) -> dict:
    out: dict = {}
    for key, value in set_fields.items():
        if key not in FIX_SET_FIELDS:
            raise ValueError(f"fix: cannot edit '{key}' (allowed: {', '.join(FIX_SET_FIELDS)})")
        try:
            if key in ("premium", "cost", "price"):
                v: object = float(value)
                ok = v >= 0
            elif key == "strike":
                v = float(value)
                ok = v > 0
            elif key in ("contracts", "shares"):
                v = int(value)
                ok = v >= 1
            elif key == "expiry":
                v = str(value)
                date.fromisoformat(v)  # raises if not ISO
                ok = True
        except (TypeError, ValueError):
            raise ValueError(f"fix: bad value for '{key}': {value!r}") from None
        if not ok:
            raise ValueError(f"fix: bad value for '{key}': {value!r}")
        out[key] = v
    if not out:
        raise ValueError("fix: nothing to change")
    return out


# --------------------------------------------------------------------------
# replay
# --------------------------------------------------------------------------

def derive(events: list[dict]) -> Book:
    book = Book()

    # `fix` events replay as overrides on the position they target. The file
    # stays append-only; a correction says "the value you derived is not what
    # actually happened" and every later number (collateral, basis, cost,
    # realized) is computed from the real fill instead.
    fixes: dict[str, dict] = {}
    known: dict[str, str] = {}
    for ev in events:
        if ev.get("type") == "open":
            sid = ev.get("id") or _event_id(ev)
            known[sid] = sid
            known[_event_label(ev)] = sid
    for ev in events:
        if ev.get("type") != "fix":
            continue
        target = known.get(str(ev.get("target", "")))
        if target is None:
            if ev.get("target"):
                book.warnings.append(f"fix: unknown position {ev.get('target')}")
            continue  # a note-only fix is history, not a number
        overrides = ev.get("set") or {}
        if isinstance(overrides, dict) and overrides:
            fixes.setdefault(target, {}).update(overrides)

    for ev in events:
        etype = ev.get("type")
        ts = ev.get("ts", "")

        if etype == "open":
            sid = ev.get("id") or _event_id(ev)
            merged = {**ev, **fixes.get(sid, {})}
            book.shorts[sid] = Short(
                id=sid,
                symbol=merged.get("symbol", "?"),
                kind=merged.get("kind", "put"),
                strike=float(merged.get("strike", 0)),
                expiry=merged.get("expiry", ""),
                contracts=int(merged.get("contracts", 1)),
                premium=float(merged.get("premium", 0)),
                opened=ts[:10],
            )

        elif etype in ("close", "expire", "assign", "called_away"):
            short = book.shorts.get(ev.get("parent", ""))
            if short is None:
                book.warnings.append(f"{etype}: unknown parent {ev.get('parent')}")
                continue
            contracts = int(ev.get("contracts", short.contracts))
            contracts = max(1, min(contracts, short.contracts))

            if etype == "close":
                price = float(ev.get("price", 0))
                pnl = (short.premium - price) * 100.0 * contracts
                book.realized.append(
                    Realized(short.symbol, short.kind, short.strike, short.expiry,
                             contracts, "closed", pnl, short.premium * 100 * contracts, ts)
                )
                _reduce_short(book, short, contracts)

            elif etype == "expire":
                pnl = short.premium * 100.0 * contracts
                book.realized.append(
                    Realized(short.symbol, short.kind, short.strike, short.expiry,
                             contracts, "expired", pnl, pnl, ts)
                )
                _reduce_short(book, short, contracts)

            elif etype == "assign":
                pnl = short.premium * 100.0 * contracts
                book.realized.append(
                    Realized(short.symbol, short.kind, short.strike, short.expiry,
                             contracts, "assigned", pnl, pnl, ts)
                )
                # Actuals beat assumptions: a fix can carry the price you
                # really paid per share and the shares you really received
                # (pin risk, early assignment, fractional oddities).
                ov = fixes.get(short.id, {})
                new_cost = float(ov.get("cost", short.strike))
                shares = int(ov.get("shares", contracts * 100))
                new_basis = new_cost - short.premium   # net of premium
                lot = book.lots.get(short.symbol)
                if lot is None:
                    book.lots[short.symbol] = Lot(
                        short.symbol, shares, new_basis, [short.id], cost=new_cost
                    )
                else:
                    total = lot.shares + shares
                    lot.basis = (lot.basis * lot.shares + new_basis * shares) / total
                    lot.cost = (lot.cost * lot.shares + new_cost * shares) / total
                    lot.shares = total
                    lot.ids.append(short.id)
                _reduce_short(book, short, contracts)

            elif etype == "called_away":
                lot = book.lots.get(short.symbol)
                if lot is None or lot.shares <= 0:
                    book.warnings.append(f"called_away {short.label}: no shares to deliver")
                    _reduce_short(book, short, contracts)
                    continue
                # Deliver what you actually hold; price it at the actual
                # delivery price (a fix can override it for pin-risk fills).
                ov = fixes.get(short.id, {})
                delivery_price = float(ov.get("price", short.strike))
                want = int(ov.get("shares", contracts * 100))
                delivered = min(want, lot.shares)
                # Profit on this leg is the call premium plus the gain from the
                # actual cost (strike paid) to the called-away price. The put
                # premium was already booked as realized at assignment, so using
                # the net basis here would count it twice.
                strike_gain = (delivery_price - lot.cost) * float(delivered)
                premium = short.premium * 100.0 * contracts
                pnl = premium + strike_gain
                book.realized.append(
                    Realized(short.symbol, short.kind, short.strike, short.expiry,
                             contracts, "called_away", pnl, premium, ts)
                )
                lot.shares -= delivered
                if lot.shares <= 0:
                    book.lots.pop(short.symbol, None)
                _reduce_short(book, short, contracts)

        elif etype == "roll":
            short = book.shorts.get(ev.get("parent", ""))
            if short is None:
                book.warnings.append(f"roll: unknown parent {ev.get('parent')}")
                continue
            price = float(ev.get("price", 0))
            pnl = (short.premium - price) * 100.0 * short.contracts
            book.realized.append(
                Realized(short.symbol, short.kind, short.strike, short.expiry,
                         short.contracts, "rolled", pnl, short.premium * 100 * short.contracts, ts)
            )
            book.shorts.pop(short.id, None)
            new = {
                "type": "open",
                "symbol": short.symbol,
                "kind": short.kind,
                "strike": float(ev.get("strike", short.strike)),
                "expiry": ev.get("expiry", short.expiry),
                "contracts": int(ev.get("contracts", short.contracts)),
                "premium": float(ev.get("premium", 0)),
                "ts": ts,
                "id": ev.get("new_id") or f"{short.id}~roll{len(book.shorts)}",
            }
            book.shorts[new["id"]] = Short(
                id=new["id"], symbol=new["symbol"], kind=new["kind"],
                strike=new["strike"], expiry=new["expiry"],
                contracts=new["contracts"], premium=new["premium"], opened=ts[:10],
            )

    return book


def _reduce_short(book: Book, short: Short, contracts: int) -> None:
    short.contracts -= contracts
    if short.contracts <= 0:
        book.shorts.pop(short.id, None)


def load_book(path: Path | None = None) -> Book:
    return derive(read_events(path))


# --------------------------------------------------------------------------
# mutations (each one appends exactly one event)
# --------------------------------------------------------------------------

def open_position(
    symbol: str,
    kind: str,
    strike: float,
    expiry: str,
    contracts: int,
    premium: float,
    path: Path | None = None,
) -> dict:
    if kind not in ("put", "call"):
        raise ValueError("kind must be 'put' or 'call'")
    if strike <= 0 or contracts <= 0:
        raise ValueError("strike and contracts must be positive")
    date.fromisoformat(expiry)  # validate

    event = {
        "type": "open",
        "symbol": symbol.upper().strip(),
        "kind": kind,
        "strike": float(strike),
        "expiry": expiry,
        "contracts": int(contracts),
        "premium": float(premium),
    }
    event["ts"] = datetime.now().isoformat(timespec="seconds")
    event["id"] = _event_id(event)
    duplicate = find_duplicate(event, path)
    if duplicate is not None:
        return {"duplicate": True, "existing": duplicate}
    return append_event(event, path)


def close_position(parent: str, price: float, contracts: int | None = None,
                   path: Path | None = None) -> dict:
    event = {"type": "close", "parent": parent, "price": float(price)}
    if contracts:
        event["contracts"] = int(contracts)
    return append_event(event, path)


def expire_position(parent: str, contracts: int | None = None,
                    path: Path | None = None) -> dict:
    event = {"type": "expire", "parent": parent}
    if contracts:
        event["contracts"] = int(contracts)
    return append_event(event, path)


def assign_position(parent: str, contracts: int | None = None,
                    path: Path | None = None) -> dict:
    event = {"type": "assign", "parent": parent}
    if contracts:
        event["contracts"] = int(contracts)
    return append_event(event, path)


def called_away(parent: str, contracts: int | None = None,
                path: Path | None = None) -> dict:
    event = {"type": "called_away", "parent": parent}
    if contracts:
        event["contracts"] = int(contracts)
    return append_event(event, path)


def roll_position(parent: str, strike: float, expiry: str, premium: float,
                  price: float, contracts: int | None = None,
                  path: Path | None = None) -> dict:
    event = {
        "type": "roll",
        "parent": parent,
        "price": float(price),
        "strike": float(strike),
        "expiry": expiry,
        "premium": float(premium),
    }
    if contracts:
        event["contracts"] = int(contracts)
    return append_event(event, path)


def record_fix(
    note: str = "",
    path: Path | None = None,
    *,
    target: str | None = None,
    set_fields: dict | None = None,
) -> dict:
    """Append a correction to the journal.

    With just a note it is a memo. With a target position (id or label) and a
    `set` of actual values, every later replay computes the book from what
    really happened instead of what the ticket implied. History is never
    rewritten — the truth is appended on top of it.
    """
    event: dict = {"type": "fix", "note": note}
    if set_fields:
        if not target:
            raise ValueError("fix needs a target position id")
        sid = find_position_id(str(target), path)
        if sid is None:
            raise ValueError(f"fix: unknown position {target!r}")
        event["target"] = sid
        event["set"] = _validate_overrides(set_fields)
    elif not note:
        raise ValueError("fix needs a note or a set of actual values")
    return append_event(event, path)


# --------------------------------------------------------------------------
# sync: propose, then apply
# --------------------------------------------------------------------------

@dataclass
class Proposal:
    parent: str
    label: str
    symbol: str
    kind: str
    action: str  # assign | expire
    reason: str
    spot: float
    strike: float

    @property
    def key(self) -> str:
        return f"{self.parent}:{self.action}"


def propose_expired(book: Book, spots: dict[str, float],
                    today: date | None = None) -> tuple[list[Proposal], list[dict]]:
    """Classify expired positions. Anything uncertain is returned separately."""
    today = today or date.today()
    proposals: list[Proposal] = []
    skipped: list[dict] = []

    for short in book.shorts.values():
        try:
            expiry = date.fromisoformat(short.expiry)
        except ValueError:
            continue
        if expiry >= today:
            continue
        spot = spots.get(short.symbol, 0.0)
        if spot <= 0:
            skipped.append({"short": short, "reason": "no closing price available"})
            continue
        if short.kind == "put":
            action = "assign" if spot <= short.strike else "expire"
        else:
            action = "called_away" if spot >= short.strike else "expire"
        reason = (
            f"close {spot:.2f} {'<=' if action in ('assign',) else '>'} strike {short.strike:g}"
        )
        proposals.append(
            Proposal(short.id, short.label, short.symbol, short.kind, action, reason, spot, short.strike)
        )
    return proposals, skipped
