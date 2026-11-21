"""The Textual app: screen registry and the shared keyboard model."""

from __future__ import annotations

from textual.app import App

from .config import Config
from .ui import detail, screens
from .ui.theme import APP_CSS

SCREENS = {
    "book": screens.BookScreen,
    "scan": screens.ScanScreen,
    "calls": screens.CallsScreen,
    "plan": screens.PlanScreen,
    "journal": screens.JournalScreen,
    "settings": screens.SettingsScreen,
}


class WheelsApp(App):
    """Full-screen views, one key each. No arguments, no menus.

    Navigation lives on the screens themselves (b/s/c/l/,) so each view is
    self-describing in its hint line. Screens are installed once and swapped,
    never stacked, so moving around does not grow memory.
    """

    CSS = APP_CSS
    TITLE = "Wheels"
    SUB_TITLE = "wheel strategy helper"

    def __init__(self) -> None:
        super().__init__()
        self.cfg = Config.load()
        self._screens_installed = False

    def on_mount(self) -> None:
        for name, cls in SCREENS.items():
            screen = cls(id=name)
            self.install_screen(screen, name=name)
        self._screens_installed = True
        self.push_screen("book")

    def switch(self, name: str) -> None:
        if not self._screens_installed:
            return
        current = self.screen.id if self.screen else None
        if current == name:
            return
        try:
            self.switch_screen(name)
        except Exception:  # noqa: BLE001 - never let navigation kill the app
            pass

    def refresh_current(self) -> None:
        screen = self.screen
        build = getattr(screen, "build", None)
        if callable(build):
            build()

    def action_help(self) -> None:
        self.push_screen(detail.HelpModal())


def run() -> None:
    WheelsApp().run()
