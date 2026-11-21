"""Detail panes, and the two ways a trade gets recorded.

  * `RecordModal`    — prefilled from a scanned candidate (one keypress).
  * `NewPositionModal` — manual entry, for a trade you already made at your
                        broker that never came from a scan.
"""

from __future__ import annotations

from datetime import date

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Input, Label, Select, Static

from .. import ledger
from ..config import Config
from .fmt import money
from .modals import ConfirmModal, TextModal
from .theme import ACCENT, AMBER, DIM, GREEN, RED


class HelpModal(Screen):
    BINDINGS = [("escape", "close", "Close"), ("question_mark", "close", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-wide"):
            yield Static("Keys", classes="modal-title")
            yield Static(
                "b  Book        open positions and what to do next\n"
                "s  Scan        rank cash-secured puts in the DTE window\n"
                "c  Calls       covered calls for shares you hold\n"
                "p  Plan        what assignment does to your book, and the\n"
                "               covered call to sell next  <-- use this before\n"
                "               an ITM put expires\n"
                "l  Ledger      every event, plus realized P/L\n"
                ",  Settings    budget, DTE window, profile, watchlist\n"
                "r  Refresh     re-fetch quotes\n"
                "?  Help        this list\n"
                "q  Quit\n\n"
                "Planning\n"
                "  p  on any screen   what assignment does, and the call to sell\n"
                "  x  on Scan         project against full budget, not free cash\n"
                "                     (use when an open put holds your collateral)\n\n"
                "Adding a position\n"
                "  o  on Book     enter one by hand (any trade you already made)\n"
                "  o  on Scan     record a ranked put, prefilled\n"
                "  o  on Calls    record a covered call, prefilled\n\n"
                "In a list: n/k or arrows move, Enter opens the detail pane.\n"
                "On a position: x close, e expire, a assign, v called away.\n\n"
                "Nothing is written to the journal without confirming.",
                classes="modal-body",
            )

    def action_close(self) -> None:
        self.app.pop_screen()


class CandidateModal(Screen):
    BINDINGS = [("escape", "close", "Close"), ("o", "record", "Record")]

    def __init__(self, cand: dict, cfg: Config) -> None:
        super().__init__()
        self.cand = cand
        self.cfg = cfg

    def compose(self) -> ComposeResult:
        c = self.cand
        lines = [
            f"[{ACCENT}]{c['ticket']}[/]    score [b]{c['score']}[/]",
            "",
            f"spot {money(c.get('spot'), 2)}   mid {money(c['mid'], 2)}   "
            f"bid/ask {c['bid']:.2f}/{c['ask']:.2f}   spread {c['spread_pct']:g}%",
            f"delta {c['delta']:.2f}   IV {c['iv'] * 100:.0f}%   OI {c['oi']:,}   DTE {c['dte']}",
            "",
            f"1 contract: premium {money(c['mid'] * 100)}, "
            f"collateral {money(c['collateral'])}, breakeven {money(c['breakeven'], 2)}",
            f"annualized [{GREEN}]{c['annualized_pct']:g}%[/]   cushion {c['cushion_pct']:.1f}%   "
            f"affordable {c['contracts_affordable']}",
            "",
            "score  " + "   ".join(f"{k} {v}" for k, v in c["scores"].items()),
        ]
        if c["flags"]:
            lines += ["", f"[{AMBER}]flags[/] " + ", ".join(c["flags"])]
        if c.get("earnings"):
            lines.append(f"[{DIM}]next earnings {c['earnings']}[/]")
        lines += ["", f"[{DIM}]o record   Esc back[/]"]
        with Vertical(classes="modal-wide"):
            yield Static("\n".join(lines), classes="modal-body")

    def action_record(self) -> None:
        self.app.pop_screen()
        self.app.push_screen(RecordModal(self.cand, self.cfg))

    def action_close(self) -> None:
        self.app.pop_screen()


class CallModal(Screen):
    BINDINGS = [("escape", "close", "Close"), ("o", "record", "Record")]

    def __init__(self, pos: dict, cand: dict, cfg: Config) -> None:
        super().__init__()
        self.pos = pos
        self.cand = cand
        self.cfg = cfg

    def compose(self) -> ComposeResult:
        c, p = self.cand, self.pos
        lines = [
            f"[{ACCENT}]{c['ticket']}[/]    covered call on {p['shares']} shares",
            "",
            f"basis {money(p['basis'], 2)}   spot {money(p['price'], 2)}   "
            f"unrealized {money(p['unrealized'], 0, sign=True)}",
            f"strike is {money(c['strike'] - p['basis'], 2)} above basis",
            "",
            f"premium {money(c['mid'] * 100 * c['contracts_affordable'])} on "
            f"{c['contracts_affordable']} contract(s)",
            f"return on shares [{GREEN}]{c['return_on_shares_pct']:.2f}%[/]   "
            f"delta {c['delta']:.2f}   DTE {c['dte']}",
            f"if called away: [{GREEN}]{money(c['called_away_gain'], 0, sign=True)}[/] total",
        ]
        if c["flags"]:
            lines += ["", f"[{AMBER}]flags[/] " + ", ".join(c["flags"])]
        lines += ["", f"[{DIM}]o record   Esc back[/]"]
        with Vertical(classes="modal-wide"):
            yield Static("\n".join(lines), classes="modal-body")

    def action_record(self) -> None:
        self.app.pop_screen()
        self.app.push_screen(RecordModal(self.cand, self.cfg))

    def action_close(self) -> None:
        self.app.pop_screen()


class PositionModal(Screen):
    BINDINGS = [
        ("escape", "back", "Back"),
        ("x", "close_pos", "Close"),
        ("e", "expire", "Expire"),
        ("a", "assign", "Assign"),
        ("v", "called_away", "Called"),
        ("c", "sell_call", "Sell call"),
    ]

    def __init__(self, row: dict, cfg: Config) -> None:
        super().__init__()
        self.row = row
        self.cfg = cfg

    @property
    def _is_shares(self) -> bool:
        return self.row.get("kind") == "shares"

    def compose(self) -> ComposeResult:
        r = self.row
        if self._is_shares:
            # A share lot is shares, not contracts: what you really paid,
            # what it nets against premium, and the next real move.
            lines = [
                f"[{ACCENT}]{r['label']}[/]",
                "",
                f"{r.get('shares', r['contracts'] * 100)} shares"
                f"   cost {money(r.get('cost'), 2)}"
                f"   basis {money(r.get('basis'), 2)}",
                f"spot {money(r.get('spot'), 2)}   "
                f"unrealized {money(r.get('unrealized'), 0, sign=True)}",
                "",
                f"[b]{r['action']}[/]",
                "",
                f"[{DIM}]c sell a covered call   Esc back[/]",
            ]
        else:
            lines = [
                f"[{ACCENT}]{r['label']}[/]",
                "",
                f"{r['contracts']} contract(s)   premium {money(r['premium'], 2)}   DTE {r.get('dte')}",
                f"spot {money(r.get('spot'), 2)}   mark {money(r.get('mark'), 2)}   "
                f"unrealized {money(r.get('unrealized'), 0, sign=True)}",
            ]
            if r.get("basis"):
                lines.append(f"basis {money(r['basis'], 2)}")
            lines += [
                "",
                f"[b]{r['action']}[/]",
                "",
                f"[{DIM}]x close (BTC)   e expired   a assigned   v called away   Esc back[/]",
            ]
        with Vertical(classes="modal"):
            yield Static("\n".join(lines), classes="modal-body")

    def action_back(self) -> None:
        self.app.pop_screen()

    def _reject_if_shares(self) -> bool:
        """Option lifecycle keys are meaningless for a share lot — pressing
        them used to append `unknown parent` events to the journal."""
        if self._is_shares:
            self.app.pop_screen()
            self.app.notify(
                "That is a share lot, not an option — press c to sell a covered call",
                severity="warning",
            )
            return True
        return False

    def action_sell_call(self) -> None:
        self.app.pop_screen()
        self.app.switch("calls" if self._is_shares else "plan")

    def _done(self, msg: str) -> None:
        self.app.notify(msg)
        self.app.refresh_current()

    def action_close_pos(self) -> None:
        if self._reject_if_shares():
            return
        self.app.pop_screen()
        self.app.push_screen(
            TextModal("Buy to close", "Price paid per share", "0.00"),
            self._close,
        )

    def _close(self, value: str | None) -> None:
        if not value:
            return
        try:
            price = float(value)
        except ValueError:
            self.app.notify("Enter a number", severity="error")
            return
        ledger.close_position(self.row["id"], price)
        self._done(f"Closed {self.row['label']} at {price:.2f}")

    def action_expire(self) -> None:
        if self._reject_if_shares():
            return
        self.app.pop_screen()
        self.app.push_screen(
            ConfirmModal("Expired worthless", f"Mark {self.row['label']} as expired worthless?"),
            lambda ok: self._expire() if ok else None,
        )

    def _expire(self) -> None:
        ledger.expire_position(self.row["id"])
        self._done(f"Expired {self.row['label']}")

    def action_assign(self) -> None:
        if self._reject_if_shares():
            return
        self.app.pop_screen()
        shares = self.row["contracts"] * 100
        self.app.push_screen(
            ConfirmModal(
                "Assigned",
                f"Assign {self.row['label']}?\n\nYou will own {shares} shares at "
                f"{money(self.row['strike'], 2)} less the premium received. "
                "The covered-call screen becomes available immediately.",
                confirm_label="Assign",
            ),
            lambda ok: self._assign() if ok else None,
        )

    def _assign(self) -> None:
        ledger.assign_position(self.row["id"])
        self._done("Assigned — covered calls are now available")

    def action_called_away(self) -> None:
        if self._reject_if_shares():
            return
        self.app.pop_screen()
        self.app.push_screen(
            ConfirmModal(
                "Shares called away",
                f"Deliver shares against {self.row['label']}?",
                confirm_label="Called away",
            ),
            lambda ok: self._called() if ok else None,
        )

    def _called(self) -> None:
        ledger.called_away(self.row["id"])
        self._done("Shares called away")


class RecordModal(Screen[bool]):
    """Record a scanned candidate. Contracts and premium are editable, because
    the number you actually filled at is rarely the mid."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, cand: dict, cfg: Config) -> None:
        super().__init__()
        self.cand = cand
        self.cfg = cfg

    def compose(self) -> ComposeResult:
        c = self.cand
        leg = "put" if c["kind"] == "put" else "call"
        n = max(1, c["contracts_affordable"])
        with Vertical(classes="modal"):
            yield Static(f"Record sold {leg}", classes="modal-title")
            yield Static(f"{c['ticket']}\n\ncollateral {money(c['collateral'])} per contract")
            yield Label("Contracts")
            yield Input(value=str(n), id="contracts", type="integer")
            yield Label("Premium received per share")
            yield Input(value=f"{c['mid']:.2f}", id="premium")
            with Horizontal(classes="modal-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Record", id="ok", classes="-primary")

    def on_mount(self) -> None:
        self.query_one("#contracts", Input).focus()

    def _values(self) -> tuple[int, float]:
        raw_n = self.query_one("#contracts", Input).value.strip()
        raw_p = self.query_one("#premium", Input).value.strip()
        return int(float(raw_n)), float(raw_p)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "ok":
            self.dismiss(False)
            return
        c = self.cand
        try:
            n, premium = self._values()
            if n < 1 or premium < 0:
                raise ValueError
        except ValueError:
            self.app.notify("Enter a whole number of contracts and a premium", severity="error")
            return
        result = ledger.open_position(c["symbol"], c["kind"], c["strike"], c["expiry"], n, premium)
        if result.get("duplicate"):
            self.app.notify("That trade is already in the ledger", severity="warning")
        else:
            self.app.notify(f"Recorded {c['ticket']} x{n}")
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class NewPositionModal(Screen[bool]):
    """Manual entry for a trade made elsewhere.

    Accepts either an explicit expiry date or DTE-plus-standard-Friday, since
    that is how most people read a trade confirmation.
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-wide"):
            yield Static("Add a position", classes="modal-title")
            yield Static(
                f"[{DIM}]For a trade you already made. Scanning finds better prices —[/]\n"
                f"[{DIM}]use o on Scan or Calls when the trade came from here.[/]",
                classes="modal-body",
            )
            with Horizontal():
                with Vertical():
                    yield Label("Symbol")
                    yield Input(placeholder="FIG", id="symbol")
                with Vertical():
                    yield Label("Type")
                    yield Select(
                        [("Put", "put"), ("Call", "call")],
                        value="put",
                        id="kind",
                        allow_blank=False,
                    )
            with Horizontal():
                with Vertical():
                    yield Label("Strike")
                    yield Input(placeholder="17.5", id="strike")
                with Vertical():
                    yield Label("Contracts")
                    yield Input(value="1", id="contracts", type="integer")
            with Horizontal():
                with Vertical():
                    yield Label("Expiry (YYYY-MM-DD)")
                    yield Input(placeholder="2026-12-18", id="expiry")
                with Vertical():
                    yield Label("Premium per share")
                    yield Input(placeholder="1.17", id="premium")
            yield Static("", id="error")
            with Horizontal(classes="modal-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Add", id="ok", classes="-primary")

    def on_mount(self) -> None:
        self.query_one("#symbol", Input).focus()

    def _read(self) -> dict:
        def val(wid: str) -> str:
            return self.query_one(f"#{wid}", Input).value.strip()

        symbol = val("symbol").upper()
        if not symbol:
            raise ValueError("Symbol is required")
        kind = self.query_one("#kind", Select).value
        try:
            strike = float(val("strike"))
        except ValueError:
            raise ValueError("Strike must be a number")
        try:
            contracts = int(float(val("contracts")))
        except ValueError:
            raise ValueError("Contracts must be a whole number")
        try:
            premium = float(val("premium") or 0)
        except ValueError:
            raise ValueError("Premium must be a number")
        expiry_raw = val("expiry")
        try:
            expiry = date.fromisoformat(expiry_raw).isoformat()
        except ValueError:
            raise ValueError("Expiry must look like 2026-12-18")
        if strike <= 0 or contracts < 1:
            raise ValueError("Strike and contracts must be positive")
        if premium < 0:
            raise ValueError("Premium cannot be negative")
        return {
            "symbol": symbol,
            "kind": kind,
            "strike": strike,
            "expiry": expiry,
            "contracts": contracts,
            "premium": premium,
        }

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "ok":
            self.dismiss(False)
            return
        try:
            fields = self._read()
        except ValueError as exc:
            self.query_one("#error", Static).update(f"[{RED}]{exc}[/]")
            return
        result = ledger.open_position(**fields)
        if result.get("duplicate"):
            self.app.notify("That trade is already in the ledger", severity="warning")
        else:
            self.app.notify(f"Added {fields['symbol']} {fields['expiry']} {fields['strike']:g} x{fields['contracts']}")
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
