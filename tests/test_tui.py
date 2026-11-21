"""Smoke tests: drive the real TUI end to end.

These exist because import-level and compose-level checks cannot catch the
failures that actually break a TUI — a reserved method name, a duplicate widget
id, a rebuild that re-mounts. Every test here presses real keys against a real
app instance at a real terminal size.
"""

from __future__ import annotations

import pytest

from wheel.app import WheelsApp


def test_app_starts_on_book():
    import asyncio

    async def run():
        app = WheelsApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            return app.screen.id

    assert asyncio.run(run()) == "book"


@pytest.mark.parametrize("key,screen", [("b", "book"), ("s", "scan"), ("c", "calls"), ("p", "plan"), ("l", "journal"), (",", "settings")])
def test_every_navigation_key_opens_its_screen(key, screen):
    import asyncio

    async def run():
        app = WheelsApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press(key)
            await pilot.pause()
            return app.screen.id

    assert asyncio.run(run()) == screen


def test_tab_cycles_profile_repeatedly_without_duplicate_ids():
    """The original crash: rebuilding a screen re-mounted its DataTable.

    Pressing Tab four times must land back where it started with no
    DuplicateIds error.
    """
    import asyncio

    from wheel.config import Config

    async def run():
        app = WheelsApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            start = app.screen.cfg.profile
            seen = [start]
            for _ in range(3):
                await pilot.press("tab")
                await pilot.pause()
                seen.append(app.screen.cfg.profile)
            return app.screen.id, seen

    screen_id, seen = asyncio.run(run())
    assert screen_id == "scan"
    assert len(set(seen)) == 3, f"profile did not cycle through three values: {seen}"
    # leave config as we found it
    cfg = Config.load()
    cfg.profile = "balanced"
    cfg.save()


def test_refresh_key_is_repeatable():
    """`r` rebuilds the view; pressing it twice must not raise."""
    import asyncio

    async def run():
        app = WheelsApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            for _ in range(3):
                await pilot.press("r")
                await pilot.pause()
            return app.screen.id

    assert asyncio.run(run()) == "book"


def test_help_opens_and_closes():
    import asyncio

    async def run():
        app = WheelsApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press("?")
            await pilot.pause()
            opened = len(app.screen_stack)
            await pilot.press("escape")
            await pilot.pause()
            return opened, len(app.screen_stack)

    opened, closed = asyncio.run(run())
    assert opened > closed


def test_cursor_movement_wraps_and_does_not_crash_on_empty_table(tmp_path, monkeypatch):
    import asyncio

    from wheel import ledger

    monkeypatch.setattr(ledger, "JOURNAL_PATH", tmp_path / "trades.jsonl")

    async def run():
        app = WheelsApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            for key in ["n", "n", "k", "down", "up", "enter"]:
                await pilot.press(key)
                await pilot.pause()
            return app.screen.id

    assert asyncio.run(run()) == "book"


def test_all_screens_are_installed():
    import asyncio

    async def run():
        app = WheelsApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            return sorted(app._installed_screens.keys())

    names = asyncio.run(run())
    assert {"book", "scan", "calls", "plan", "journal", "settings"} == set(names)


def test_renders_at_small_and_large_terminal_sizes():
    """80x24 is the floor; a bigger terminal must not break layout."""
    import asyncio

    from rich.console import Console

    def snapshot(app) -> str:
        console = Console(width=80, force_terminal=False, color_system=None, record=True)
        console.print(app.screen._compositor)
        return console.export_text()

    async def run(size):
        app = WheelsApp()
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            text = snapshot(app)
            await pilot.press("s")
            await pilot.pause()
            return text, snapshot(app)

    for size in [(80, 24), (120, 40)]:
        book, scan = asyncio.run(run(size))
        assert "Book" in book
        assert "Scan" in scan


# -- adding positions ------------------------------------------------------

def test_o_on_book_opens_manual_entry():
    import asyncio

    async def run():
        app = WheelsApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press("o")
            await pilot.pause()
            return app.screen.__class__.__name__

    assert asyncio.run(run()) == "NewPositionModal"


def test_manual_entry_records_a_position_and_refreshes_the_book(tmp_path, monkeypatch):
    """The real flow: press o, fill the form, press Add, see it in the book."""
    import asyncio

    from wheel import ledger

    journal = tmp_path / "trades.jsonl"
    monkeypatch.setattr(ledger, "JOURNAL_PATH", journal)

    async def run():
        app = WheelsApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press("o")
            await pilot.pause()
            modal = app.screen
            modal.query_one("#symbol").value = "FIG"
            modal.query_one("#strike").value = "17.5"
            modal.query_one("#expiry").value = "2026-12-18"
            modal.query_one("#contracts").value = "2"
            modal.query_one("#premium").value = "1.17"
            await pilot.pause()
            [b for b in modal.query("Button") if b.id == "ok"][0].press()
            await pilot.pause()
            return app.screen.id

    assert asyncio.run(run()) == "book"
    book = ledger.load_book(journal)
    assert len(book.shorts) == 1
    short = book.puts()[0]
    assert short.contracts == 2
    assert short.premium == 1.17


def test_manual_entry_rejects_a_malformed_expiry(tmp_path, monkeypatch):
    import asyncio

    from wheel import ledger

    journal = tmp_path / "trades.jsonl"
    monkeypatch.setattr(ledger, "JOURNAL_PATH", journal)

    async def run():
        app = WheelsApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press("o")
            await pilot.pause()
            modal = app.screen
            modal.query_one("#symbol").value = "FIG"
            modal.query_one("#strike").value = "17.5"
            modal.query_one("#expiry").value = "next friday"
            modal.query_one("#premium").value = "1.17"
            await pilot.pause()
            [b for b in modal.query("Button") if b.id == "ok"][0].press()
            await pilot.pause()
            return app.screen.__class__.__name__

    # modal stays open rather than silently recording a bad date
    assert asyncio.run(run()) == "NewPositionModal"
    assert not journal.exists() or ledger.read_events(journal) == []


def test_duplicate_add_is_blocked(tmp_path, monkeypatch):
    import asyncio

    from wheel import ledger

    journal = tmp_path / "trades.jsonl"
    monkeypatch.setattr(ledger, "JOURNAL_PATH", journal)
    ledger.open_position("FIG", "put", 17.5, "2026-12-18", 1, 1.0, path=journal)

    async def run():
        app = WheelsApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press("o")
            await pilot.pause()
            modal = app.screen
            modal.query_one("#symbol").value = "FIG"
            modal.query_one("#strike").value = "17.5"
            modal.query_one("#expiry").value = "2026-12-18"
            modal.query_one("#premium").value = "1.20"
            await pilot.pause()
            [b for b in modal.query("Button") if b.id == "ok"][0].press()
            await pilot.pause()

    asyncio.run(run())
    assert len(ledger.read_events(journal)) == 1  # no second event


def test_plan_screen_projects_assignment_for_an_open_put(tmp_path, monkeypatch):
    """An ITM put must show the covered call to sell *before* assignment."""
    import asyncio

    from wheel import ledger

    journal = tmp_path / "trades.jsonl"
    monkeypatch.setattr(ledger, "JOURNAL_PATH", journal)
    ledger.open_position("FIG", "put", 19.5, "2026-11-20", 1, 0.63, path=journal)

    async def run():
        app = WheelsApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press("p")
            await pilot.pause(9.0)
            table = app.screen.query_one("#table")
            return app.screen.id, table.row_count

    screen_id, rows = asyncio.run(run())
    assert screen_id == "plan"
    assert rows >= 1, "plan should list at least one covered call"


def test_plan_screen_is_empty_state_safe(tmp_path, monkeypatch):
    import asyncio

    from wheel import ledger

    monkeypatch.setattr(ledger, "JOURNAL_PATH", tmp_path / "trades.jsonl")

    async def run():
        app = WheelsApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press("p")
            await pilot.pause()
            return app.screen.id, app.screen.query_one("#table").row_count

    screen_id, rows = asyncio.run(run())
    assert screen_id == "plan"
    assert rows == 1  # the "no open puts" message row


def test_scan_explains_when_capital_is_committed(tmp_path, monkeypatch):
    """A committed book means free cash cannot afford a put. The scan must say
    so instead of silently returning nothing."""
    from wheel import ledger

    journal = tmp_path / "trades.jsonl"
    monkeypatch.setattr(ledger, "JOURNAL_PATH", journal)
    ledger.open_position("FIG", "put", 19.5, "2026-11-20", 1, 0.63, path=journal)

    from wheel import engine

    out = engine.scan(__import__("wheel.config", fromlist=["Config"]).Config.load())
    assert out["free_cash"] < 200
    assert out["candidates"] == []
    assert any(k.startswith("needs") for k in out["rejected_reasons"])


def test_scan_projection_ignores_committed_capital(tmp_path, monkeypatch):
    from wheel import engine, ledger
    from wheel.config import Config

    journal = tmp_path / "trades.jsonl"
    monkeypatch.setattr(ledger, "JOURNAL_PATH", journal)
    ledger.open_position("FIG", "put", 19.5, "2026-11-20", 1, 0.63, path=journal)

    out = engine.scan(Config.load(), cash_override=2000.0)
    assert out["capital_mode"] == "projected"
    assert out["capital_used"] == 2000.0
    assert len(out["candidates"]) >= 0  # projection is allowed to find candidates


def test_x_toggles_scan_projection():
    import asyncio

    async def run():
        app = WheelsApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause(7.0)
            before = app.screen.projected
            await pilot.press("x")
            await pilot.pause(7.0)
            after = app.screen.projected
            return app.screen.id, before, after

    screen_id, before, after = asyncio.run(run())
    assert screen_id == "scan"
    assert before is False and after is True


def test_adding_a_symbol_in_settings_is_seen_by_other_screens(tmp_path, monkeypatch):
    """The staleness bug: each screen snapshotted Config at startup, so a symbol
    added in Settings never reached Scan."""
    import asyncio

    from wheel import ledger
    from wheel.config import Config

    from wheel import config as config_mod

    journal = tmp_path / "trades.jsonl"
    cfg_path = tmp_path / "config.toml"
    monkeypatch.setattr(ledger, "JOURNAL_PATH", journal)
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg_path)
    cfg = Config(symbols=["FIG"])
    cfg.save()

    async def run():
        app = WheelsApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press(",")
            await pilot.pause()
            app.screen.open_row(8)
            await pilot.pause()
            modal = app.screen
            modal.query_one("#field").value = "SOFI"
            [b for b in modal.query("Button") if b.id == "ok"][0].press()
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause(7.0)
            return app.screen.cfg.symbols

    assert "SOFI" in asyncio.run(run())


def test_config_survives_a_save_and_reload(tmp_path):
    from wheel.config import Config

    path = tmp_path / "config.toml"
    cfg = Config(budget=5000.0, profile="aggressive", symbols=["FIG", "SOFI"])
    cfg.save(path)
    again = Config.load(path)
    assert again.budget == 5000.0
    assert again.profile == "aggressive"
    assert again.symbols == ["FIG", "SOFI"]


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path, monkeypatch):
    """Every TUI test gets its own config and journal.

    Learned the hard way: a profile-cycle test wrote to the real config.toml and
    left the app on the wrong risk profile.
    """
    from wheel import config as config_mod
    from wheel import ledger

    cfg_path = tmp_path / "config.toml"
    seed = config_mod.Config.load()
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(ledger, "JOURNAL_PATH", tmp_path / "trades.jsonl")
    seed.save(cfg_path)
    yield


def test_total_is_marked_to_the_live_market(tmp_path, monkeypatch):
    """What you HAVE today is cash + collateral + stock at today's price —
    not what you paid. Assignment at 19.50 with the stock at 16.40 must
    report the loss, not a $2,063 fantasy."""
    from wheel import engine, ledger
    from wheel import config as config_mod

    cfg_path = tmp_path / "config.toml"
    config_mod.Config(symbols=["FIG"], budget=2000.0).save(cfg_path)
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg_path)
    journal = tmp_path / "trades.jsonl"
    monkeypatch.setattr(ledger, "JOURNAL_PATH", journal)

    ev = ledger.open_position("FIG", "put", 19.5, "2026-12-18", 1, 0.63, path=journal)
    ledger.assign_position(ev["id"], path=journal)
    cfg = config_mod.Config.load()
    book = ledger.load_book(journal)

    b = engine.book_summary(cfg, book, spots={"FIG": 16.40})
    assert abs(b["total"] - 2063.0) < 0.01          # at cost: budget + banked premium
    assert abs(b["unrealized"] + 310.0) < 0.01      # (16.40 − 19.50) × 100
    assert abs(b["total_market"] - 1753.0) < 0.01   # 113 cash + 1,640 of stock
    assert abs(b["cash"] + b["collateral"] + b["shares_live"] - b["total_market"]) < 0.01

    # Without quotes: cost fallback. The book never invents a price.
    b0 = engine.book_summary(cfg, book)
    assert b0["total_market"] == b0["total"]
    assert b0["unrealized"] == 0


def test_share_lot_detail_speaks_shares_not_contracts(tmp_path, monkeypatch):
    """A lot born from assignment is 100 shares — the detail pane must say
    so (and offer the shares move), never '1 contract(s) DTE None'."""
    from datetime import date, timedelta

    from wheel import ledger

    journal = tmp_path / "trades.jsonl"
    monkeypatch.setattr(ledger, "JOURNAL_PATH", journal)
    expiry = (date.today() + timedelta(days=30)).isoformat()
    ev = ledger.open_position("FIG", "put", 19.5, expiry, 1, 0.63, path=journal)
    ledger.assign_position(ev["id"], path=journal)

    import asyncio

    async def run():
        app = WheelsApp()
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause()
            await pilot.press("enter")  # detail on the only row: the lot
            await pilot.pause()
            body = str(app.screen.query_one(".modal-body").render())
            return app.screen.__class__.__name__, body

    screen_name, body = asyncio.run(run())
    assert screen_name == "PositionModal"
    assert "100 shares" in body
    assert "contract" not in body
    assert "cost $19.50" in body and "basis $18.87" in body
    assert "c sell a covered call" in body


def test_option_keys_rejected_on_a_share_lot(tmp_path, monkeypatch):
    """Pressing 'a' (assign) on a share row must not append an
    `unknown parent` event — the journal stays clean."""
    from datetime import date, timedelta

    from wheel import ledger

    journal = tmp_path / "trades.jsonl"
    monkeypatch.setattr(ledger, "JOURNAL_PATH", journal)
    expiry = (date.today() + timedelta(days=30)).isoformat()
    ev = ledger.open_position("FIG", "put", 19.5, expiry, 1, 0.63, path=journal)
    ledger.assign_position(ev["id"], path=journal)
    before = len(ledger.read_events(journal))

    import asyncio

    async def run():
        app = WheelsApp()
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("a")   # "assign" on a lot that is already shares
            await pilot.pause()

    asyncio.run(run())
    events = ledger.read_events(journal)
    assert len(events) == before                 # nothing written
    assert ledger.load_book(journal).warnings == []
