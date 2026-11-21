"""Shared modal dialogs: confirm, text entry, and the trade recorder."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static


class ConfirmModal(ModalScreen[bool]):
    """Yes/no gate. Nothing writes to the journal without one of these."""

    BINDINGS = [("escape", "cancel", "Cancel"), ("n", "cancel", "No"), ("y", "confirm", "Yes")]

    def __init__(self, title: str, body: str, danger: bool = False, confirm_label: str = "Confirm") -> None:
        super().__init__()
        self._title = title
        self._body = body
        self._danger = danger
        self._confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Static(self._title, classes="modal-title")
            yield Static(self._body, classes="modal-body")
            with Horizontal(classes="modal-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button(self._confirm_label, id="ok", classes="-primary" if not self._danger else "")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "ok")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class TextModal(ModalScreen[str | None]):
    """Single-field entry."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, title: str, label: str, value: str = "", placeholder: str = "") -> None:
        super().__init__()
        self._title = title
        self._label = label
        self._value = value
        self._placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Static(self._title, classes="modal-title")
            yield Label(self._label)
            yield Input(value=self._value, placeholder=self._placeholder, id="field")
            with Horizontal(classes="modal-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Save", id="ok", classes="-primary")

    def on_mount(self) -> None:
        self.query_one("#field", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok":
            self.dismiss(self.query_one("#field", Input).value.strip() or None)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
