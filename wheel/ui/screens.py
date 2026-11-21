"""List screens. One per keypress; each is a full-screen view.

Widgets are created exactly once, in `compose()`. Refreshing only clears rows and
re-queries the engine — it never re-mounts a table. That keeps IDs unique and
makes `r` / profile changes safe to hit as often as you like.
"""

from __future__ import annotations

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Static

from .. import engine, ledger
from ..config import Config
from .detail import (
    CallModal,
    CandidateModal,
    HelpModal,
    NewPositionModal,
    PositionModal,
)
from .fmt import (
    dte_text,
    money,
    pct,
    pl_text,
    score_bar,
    short_flags,
    truncate,
    urgency_text,
)
from .modals import ConfirmModal, TextModal
from .theme import ACCENT, AMBER, DIM, GREEN

LIST_BINDINGS = [
    Binding("down", "cursor_down", "Down", show=False),
    Binding("up", "cursor_up", "Up", show=False),
    Binding("n", "cursor_down", "Down", show=False),
    Binding("k", "cursor_up", "Up", show=False),
    Binding("enter", "select_row", "Open", show=False),
    Binding("escape", "back", "Back", show=False),
]

NAV_BINDINGS = [
    Binding("b", "goto('book')", "Book"),
    Binding("s", "goto('scan')", "Scan"),
    Binding("c", "goto('calls')", "Calls"),
    Binding("p", "goto('plan')", "Plan"),
    Binding("l", "goto('journal')", "Ledger"),
    Binding("comma", "goto('settings')", "Settings"),
    Binding("o", "add", "Add"),
    Binding("r", "refresh", "Refresh"),
    Binding("question_mark", "help", "Help", show=False),
    Binding("q", "quit", "Quit", show=False),
]


class BaseScreen(Screen):
    """Chrome + a single DataTable that is mounted once and reused."""

    COLUMNS: tuple[str, ...] = ()
    BINDINGS = NAV_BINDINGS + LIST_BINDINGS
    AUTO_FOCUS = "#table"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.cfg = Config.load()
        self._rows: list = []

    def compose(self) -> ComposeResult:
        yield Static("", id="topbar")
        yield Static("", id="progress")
        with Vertical(id="body"):
            yield DataTable(id="table", cursor_type="row")
        yield Static("", id="hint")

    def on_mount(self) -> None:
        table = self.query_one("#table", DataTable)
        table.add_columns(*self.COLUMNS)
        self.refresh_view()

    # -- subclass contract -------------------------------------------------
    def refresh_view(self) -> None:
        """Load data and repaint. Must be safe to call repeatedly."""
        raise NotImplementedError

    def on_screen_resume(self) -> None:
        """Another screen may have changed config.toml while we were hidden."""
        fresh = Config.load()
        if fresh.as_dict() != self.cfg.as_dict():
            self.cfg = fresh
            self.refresh_view()

    def draw_chrome(self) -> None:
        """Set topbar, hint and progress text for the current state."""

    def fill(self, table: DataTable) -> None:
        """Add rows for the current data."""
        raise NotImplementedError

    def open_row(self, idx: int) -> None:
        pass

    # -- helpers -----------------------------------------------------------
    def table(self) -> DataTable:
        return self.query_one("#table", DataTable)

    def topbar(self, text: str) -> None:
        self.query_one("#topbar", Static).update(text)

    def hint(self, text: str) -> None:
        self.query_one("#hint", Static).update(text)

    def progress(self, text: str) -> None:
        self.query_one("#progress", Static).update(text)

    def bookbar(self) -> str:
        book = ledger.load_book()
        free = max(0.0, self.cfg.budget - book.committed)
        total = self.cfg.budget + book.realized_pnl
        return (
            f"budget {money(self.cfg.budget)}  total {money(total)}  "
            f"free {money(free)}  prem {money(book.premium_collected)}"
        )

    def set_progress_from_thread(self, text: str) -> None:
        self.app.call_from_thread(self.progress, text)

    # -- actions -----------------------------------------------------------
    def action_goto(self, name: str) -> None:
        self.app.switch(name)

    def action_refresh(self) -> None:
        self.refresh_view()

    def action_add(self) -> None:
        """`o` adds a position. Screens with a prefill override this."""
        self.app.push_screen(NewPositionModal(), self._after_add)

    def action_help(self) -> None:
        self.app.push_screen(HelpModal())

    def action_quit(self) -> None:
        self.app.exit()

    def _after_add(self, recorded: bool | None) -> None:
        if recorded:
            self.refresh_view()

    def action_back(self) -> None:
        pass

    def action_cursor_down(self) -> None:
        self._move(1)

    def action_cursor_up(self) -> None:
        self._move(-1)

    def _move(self, delta: int) -> None:
        table = self.table()
        if table.row_count == 0:
            return
        table.move_cursor(row=(table.cursor_row + delta) % table.row_count)

    def action_select_row(self) -> None:
        table = self.table()
        if table.row_count:
            self.open_row(table.cursor_row)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.open_row(event.cursor_row)


class BookScreen(BaseScreen):
    COLUMNS = ("POSITION", "DTE", "SPOT", "MARK", "P/L", "NEXT ACTION")

    def draw_chrome(self) -> None:
        self.topbar(f"[{ACCENT}]Book[/]  {self.bookbar()}")
        self.hint("n/k move · enter detail · o ADD · p PLAN · s scan")

    def refresh_view(self) -> None:
        self.draw_chrome()
        self.progress("Fetching quotes…")
        self.load()

    @work(thread=True)
    def load(self) -> None:
        data = engine.status(self.cfg)
        self.app.call_from_thread(self.fill, self.table(), data)

    def fill(self, table: DataTable, data: dict | None = None) -> None:
        if data is None:
            return
        rows = data.get("positions", [])
        self._rows = rows
        table.clear()
        if not rows:
            table.add_row(
                Text("no open positions", style=DIM), "", "", "", "", Text("press s to scan", style=DIM)
            )
        for r in rows:
            table.add_row(
                Text(r["label"], style=ACCENT),
                dte_text(r.get("dte")),
                money(r.get("spot"), 2),
                money(r.get("mark"), 2) if r.get("mark") else "—",
                pl_text(r.get("unrealized")),
                urgency_text(truncate(r["action"], 40), r.get("urgency", "normal")),
            )
        open_n = len([r for r in rows if r.get("kind") != "shares"])
        self.progress(f"{open_n} open · {len(rows) - open_n} share lot(s)")

    def open_row(self, idx: int) -> None:
        if 0 <= idx < len(self._rows):
            self.app.push_screen(PositionModal(self._rows[idx], self.cfg))


class ScanScreen(BaseScreen):
    COLUMNS = ("TICKET", "DTE", "DELTA", "MID", "ANN%", "CUSH", "SCORE", "FLAGS")

    BINDINGS = BaseScreen.BINDINGS + [
        Binding("tab", "cycle_profile", "Profile"),
        Binding("x", "toggle_projection", "Project"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        # False = only free cash. True = assume the full budget is available,
        # which is how you plan the *next* put while one still holds collateral.
        self.projected = False

    def draw_chrome(self) -> None:
        mode = "PROJECTED" if self.projected else "free cash"
        self.topbar(f"[{ACCENT}]Scan[/] \\[{mode}] · {self.cfg.profile} · {self.cfg.dte_min}-{self.cfg.dte_max}d")
        self.hint("n/k move · enter detail · o RECORD · x project · tab profile")

    def refresh_view(self) -> None:
        self.draw_chrome()
        self.progress("Fetching quotes…")
        self.load()

    def action_toggle_projection(self) -> None:
        self.projected = not self.projected
        self.refresh_view()

    def action_cycle_profile(self) -> None:
        order = ["conservative", "balanced", "aggressive"]
        i = order.index(self.cfg.profile) if self.cfg.profile in order else 1
        self.cfg.profile = order[(i + 1) % len(order)]
        self.cfg.save()
        self.refresh_view()

    @work(thread=True)
    def load(self) -> None:
        def progress(sym: str, done: int, total: int) -> None:
            self.set_progress_from_thread(f"Fetching {sym} ({done}/{total})…")

        cash = self.cfg.budget if self.projected else None
        data = engine.scan(self.cfg, progress=progress, cash_override=cash)
        self.app.call_from_thread(self.fill, self.table(), data)

    def fill(self, table: DataTable, data: dict | None = None) -> None:
        if data is None:
            return
        cands = data.get("candidates", [])
        self._rows = cands
        table.clear()
        for c in cands:
            flags = short_flags(c["flags"])
            table.add_row(
                Text(c["ticket"], style=ACCENT),
                dte_text(c["dte"]),
                Text(f"{c['delta']:.2f}"),
                money(c["mid"], 2),
                Text(pct(c["annualized_pct"], 0, sign=False), style=GREEN),
                Text(pct(c["cushion_pct"], 1, sign=False)),
                score_bar(c["score"]),
                Text(truncate(flags, 20), style=AMBER if flags else DIM),
            )
        if not cands:
            reasons = data.get("rejected_reasons", {})
            rx = ", ".join(f"{k} {v}" for k, v in reasons.items()) or "nothing in window"
            table.add_row(
                Text("no qualifying contract", style=DIM), "", "", "", "", "", "", Text(rx, style=DIM)
            )

        per = data.get("per_symbol", {})
        seen = sum(v.get("contracts_seen", 0) for v in per.values())
        qual = sum(v.get("qualifying", 0) for v in per.values())
        note = f"{seen} scanned · {qual} passed · capital {money(data.get('capital_used', 0))}"
        if data.get("capital_mode") == "projected":
            note += " (projected)"
        elif not cands and data.get("free_cash", 0) < 100:
            note += " · no free cash, press x to project"
        errs = data.get("errors", [])
        if errs:
            note += f" · {errs[0]['symbol']}: {errs[0]['error'][:40]}"
        self.progress(note)

    def open_row(self, idx: int) -> None:
        if 0 <= idx < len(self._rows):
            self.app.push_screen(CandidateModal(self._rows[idx], self.cfg))

    def action_add(self) -> None:
        """On Scan, `o` records the highlighted candidate."""
        table = self.table()
        idx = table.cursor_row
        if 0 <= idx < len(self._rows):
            from .detail import RecordModal

            self.app.push_screen(RecordModal(self._rows[idx], self.cfg), self._after_add)
        else:
            super().action_add()


class CallsScreen(BaseScreen):
    COLUMNS = ("SYMBOL", "BASIS", "TICKET", "DTE", "DELTA", "MID", "ON SHARES", "CALLED AWAY")

    def draw_chrome(self) -> None:
        self.topbar(
            f"[{ACCENT}]Calls[/]  {self.cfg.profile} · "
            f"{self.cfg.dte_min}-{self.cfg.dte_max}d   {self.bookbar()}"
        )
        self.hint("n/k move · enter detail · o RECORD · above basis only")

    def refresh_view(self) -> None:
        self.draw_chrome()
        self.progress("Fetching quotes…")
        self.load()

    @work(thread=True)
    def load(self) -> None:
        data = engine.calls(self.cfg)
        self.app.call_from_thread(self.fill, self.table(), data)

    def fill(self, table: DataTable, data: dict | None = None) -> None:
        if data is None:
            return
        table.clear()
        self._rows = []
        if data.get("message"):
            table.add_row(Text("—", style=DIM), "", Text(data["message"], style=DIM), "", "", "", "", "")
            self.progress("")
            return
        for pos in data.get("positions", []):
            if "error" in pos:
                table.add_row(
                    Text(pos["symbol"], style=ACCENT), "", Text(str(pos["error"]), style=AMBER), "", "", "", "", ""
                )
                continue
            for c in pos["candidates"]:
                self._rows.append({"pos": pos, "cand": c})
                table.add_row(
                    Text(pos["symbol"], style=ACCENT),
                    money(pos["basis"], 2),
                    Text(c["ticket"].split(" ", 1)[-1], style=ACCENT),
                    dte_text(c["dte"]),
                    Text(f"{c['delta']:.2f}"),
                    money(c["mid"], 2),
                    Text(pct(c["return_on_shares_pct"], 2, sign=False), style=GREEN),
                    Text(money(c["called_away_gain"], 0, sign=True), style=GREEN),
                )
        n = len(self._rows)
        self.progress(f"{n} contract(s) above basis" if n else "no strikes above basis in window")

    def open_row(self, idx: int) -> None:
        if 0 <= idx < len(self._rows):
            row = self._rows[idx]
            self.app.push_screen(CallModal(row["pos"], row["cand"], self.cfg))

    def action_add(self) -> None:
        table = self.table()
        idx = table.cursor_row
        if 0 <= idx < len(self._rows):
            from .detail import RecordModal

            self.app.push_screen(RecordModal(self._rows[idx]["cand"], self.cfg), self._after_add)
        else:
            super().action_add()


class PlanScreen(BaseScreen):
    """What assignment would do to this book, and the call to sell next.

    Exists because the decision has to be made before assignment, not after.
    """

    COLUMNS = ("SYMBOL", "BASIS", "CASH", "TICKET", "DTE", "DELTA", "MID", "ON SHR", "IF CALLED")

    def draw_chrome(self) -> None:
        self.topbar(f"[{ACCENT}]Plan[/]  if your open puts get assigned   {self.bookbar()}")
        self.hint("n/k move · enter detail · o RECORD · strikes at or above basis")

    def refresh_view(self) -> None:
        self.draw_chrome()
        self.progress("Projecting assignment…")
        self.load()

    @work(thread=True)
    def load(self) -> None:
        data = engine.plan(self.cfg)
        self.app.call_from_thread(self.fill, self.table(), data)

    def fill(self, table: DataTable, data: dict | None = None) -> None:
        if data is None:
            return
        table.clear()
        self._rows = []
        if data.get("message"):
            table.add_row(Text(data["message"], style=DIM), "", "", "", "", "", "", "", "")
            self.progress("")
            return

        for pos in data.get("positions", []):
            if "error" in pos:
                table.add_row(
                    Text(pos["symbol"], style=ACCENT), "", "", Text(str(pos["error"]), style=AMBER),
                    "", "", "", "", "",
                )
                continue
            for c in pos.get("calls", []):
                self._rows.append({"pos": pos, "cand": c})
                table.add_row(
                    Text(pos["symbol"], style=ACCENT),
                    money(pos["basis"], 2),
                    money(pos["cash_needed"]),
                    Text(c["ticket"].split(" ", 1)[-1], style=ACCENT),
                    dte_text(c["dte"]),
                    Text(f"{c['delta']:.2f}"),
                    money(c["mid"], 2),
                    Text(pct(c["return_on_shares_pct"], 2, sign=False), style=GREEN),
                    Text(money(c["called_away_total"], 0, sign=True), style=GREEN),
                )
        tot = data.get("totals", {})
        note = (
            f"if assigned: {tot.get('shares_if_all_assigned', 0)} shares, "
            f"{money(tot.get('cash_needed_if_all_assigned'))} cash"
        )
        if tot.get("cash_shortfall"):
            note += f" · SHORT {money(tot['cash_shortfall'])}"
        self.progress(note)

    def open_row(self, idx: int) -> None:
        if 0 <= idx < len(self._rows):
            row = self._rows[idx]
            self.app.push_screen(CallModal(row["pos"], row["cand"], self.cfg))

    def action_add(self) -> None:
        table = self.table()
        idx = table.cursor_row
        if 0 <= idx < len(self._rows):
            from .detail import RecordModal

            self.app.push_screen(RecordModal(self._rows[idx]["cand"], self.cfg), self._after_add)
        else:
            super().action_add()


class JournalScreen(BaseScreen):
    COLUMNS = ("WHEN", "TYPE", "DETAIL")

    def draw_chrome(self) -> None:
        self.topbar(f"[{ACCENT}]Ledger[/]  {self.bookbar()}")
        self.hint("append-only journal · every change is one event")

    def refresh_view(self) -> None:
        self.draw_chrome()
        self.fill(self.table(), engine.journal())

    def fill(self, table: DataTable, data: dict | None = None) -> None:
        if data is None:
            return
        table.clear()
        events = data.get("events", [])
        self._rows = events
        for ev in reversed(events[-120:]):
            when = str(ev.get("ts", ""))[:16].replace("T", " ")
            etype = ev.get("type", "?")
            style = GREEN if etype in ("expire", "called_away") else AMBER if etype == "open" else DIM
            table.add_row(Text(when, style=DIM), Text(etype, style=style), Text(describe_event(ev), style=DIM))
        stats = data.get("book", {})
        self.progress(
            f"{data.get('event_count', 0)} events · {stats.get('closed_positions', 0)} closed · "
            f"{stats.get('wins', 0)}W {stats.get('losses', 0)}L · "
            f"realized {money(stats.get('realized_pnl'), 0, sign=True)}"
        )


class SettingsScreen(BaseScreen):
    COLUMNS = ("SETTING", "VALUE", "NOTE")

    def draw_chrome(self) -> None:
        self.topbar(f"[{ACCENT}]Settings[/]  saved to config.toml immediately")
        self.hint("n/k move · enter edit · a add symbol · d delete last symbol")

    def refresh_view(self) -> None:
        self.cfg = Config.load()
        self.draw_chrome()
        self.fill(self.table())

    def fill(self, table: DataTable, data: dict | None = None) -> None:
        table.clear()
        self._settings = [
            ("budget", money(self.cfg.budget), "capital for wheel positions"),
            ("profile", self.cfg.profile, "conservative / balanced / aggressive"),
            ("dte_min", str(self.cfg.dte_min), "days to expiry, lower bound"),
            ("dte_max", str(self.cfg.dte_max), "days to expiry, upper bound"),
            ("min_open_interest", str(self.cfg.min_open_interest), "liquidity gate"),
            ("max_spread_pct", f"{self.cfg.max_spread_pct:g}", "liquidity gate, % of mid"),
            ("risk_free_rate", f"{self.cfg.risk_free_rate:g}", "for delta, e.g. 0.04"),
            ("top_n", str(self.cfg.top_n), "candidates returned"),
            ("watchlist", ", ".join(self.cfg.symbols) or "empty", "enter add · d delete last"),
        ]
        for key, value, note in self._settings:
            table.add_row(Text(key, style=DIM), Text(str(value), style=ACCENT), Text(note, style=DIM))

    def open_row(self, idx: int) -> None:
        if not (0 <= idx < len(self._settings)):
            return
        key = self._settings[idx][0]
        if key == "watchlist":
            self.app.push_screen(
                TextModal("Add symbol", "Ticker", placeholder="FIG"),
                lambda v: self._add(v),
            )
            return
        current = str(getattr(self.cfg, key))
        self.app.push_screen(
            TextModal(f"Edit {key}", "New value", current),
            lambda v: self._save(key, v),
        )

    def _add(self, value: str | None) -> None:
        if value:
            self.cfg.add_symbol(value)
            self.cfg.save()
            self.refresh_view()

    def _save(self, key: str, value: str | None) -> None:
        if not value:
            return
        try:
            if key in ("dte_min", "dte_max", "min_open_interest", "top_n"):
                setattr(self.cfg, key, int(float(value)))
            elif key in ("budget", "max_spread_pct", "risk_free_rate"):
                setattr(self.cfg, key, float(value))
            else:
                setattr(self.cfg, key, value.strip().lower())
        except ValueError:
            self.app.notify("not a valid number", severity="error")
            return
        self.cfg.save()
        self.refresh_view()
        self.app.notify(f"{key} saved")

    def key_d(self) -> None:
        if not self.cfg.symbols:
            return
        sym = self.cfg.symbols[-1]
        self.app.push_screen(
            ConfirmModal("Remove symbol", f"Remove {sym} from the watchlist?"),
            lambda ok: self._remove(sym) if ok else None,
        )

    def _remove(self, sym: str) -> None:
        self.cfg.remove_symbol(sym)
        self.cfg.save()
        self.refresh_view()
        self.app.notify(f"{sym} removed")


def describe_event(ev: dict) -> str:
    t = ev.get("type")
    if t == "open":
        leg = "P" if ev.get("kind") == "put" else "C"
        return (
            f"SELL {ev.get('symbol')} {ev.get('expiry')} {ev.get('strike'):g}{leg} "
            f"x{ev.get('contracts')} @ {ev.get('premium')}"
        )
    if t == "close":
        return f"BTC {ev.get('parent')} @ {ev.get('price')}"
    if t == "roll":
        return f"ROLL {ev.get('parent')} -> {ev.get('expiry')} {ev.get('strike'):g} @ {ev.get('premium')}"
    if t == "fix":
        return f"fix: {ev.get('note')}"
    return f"{t} {ev.get('parent') or ''}".strip()
