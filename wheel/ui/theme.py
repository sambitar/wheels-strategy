"""Palette and stylesheet. Dark, restrained, financial.

Numbers get colour only when colour carries meaning (P/L, risk, urgency).
Everything else is greyscale so the eye lands on what matters.
"""

from __future__ import annotations

# one accent, green/amber/red reserved for meaning
GREEN = "#57b894"
RED = "#e05561"
AMBER = "#d7a94b"
ACCENT = "#5aa9e6"
TEXT = "#d4d8de"
DIM = "#6b7280"
BG = "#12151a"
PANEL = "#1a1e26"
RAISED = "#232b37"

APP_CSS = """
Screen { background: #12151a; color: #d4d8de; }

#topbar { height: 1; background: #1a1e26; padding: 0 1; }
#statusbar { height: 1; background: #1a1e26; padding: 0 1; color: #6b7280; }
#title { height: 1; background: #1a1e26; color: #5aa9e6; text-style: bold; padding: 0 1; }
#hint { height: 1; background: #1a1e26; color: #6b7280; padding: 0 1; }
#progress { height: 1; background: #1a1e26; color: #d7a94b; padding: 0 1; }

#body { height: 1fr; }
#sidebar-note { height: 1; color: #6b7280; padding: 0 1; }

DataTable { background: #12151a; height: 1fr; }
DataTable > .datatable--header { background: #1a1e26; color: #6b7280; text-style: bold; }
DataTable > .datatable--cursor { background: #232b37; color: #ffffff; }
DataTable > .datatable--hover { background: #1a1e26; }
DataTable:focus { border: none; }

#detail { background: #12151a; padding: 1 2; height: 1fr; }
#detail-scroll { height: 1fr; scrollbar-size: 1 1; }

.modal { background: #1a1e26; border: round #5aa9e6; padding: 1 2; width: 76; height: auto; max-height: 22; }
.modal-wide { background: #1a1e26; border: round #5aa9e6; padding: 1 2; width: 96; height: auto; max-height: 30; }
.modal-title { color: #5aa9e6; text-style: bold; height: 1; }
.modal-body { height: auto; }
.modal-buttons { height: 3; align: right middle; }
.modal-buttons Button { margin-left: 2; min-width: 12; }

Button { background: #232b37; color: #d4d8de; border: none; min-width: 10; }
Button:hover { background: #2c3644; }
Button.-primary { background: #2b4a63; color: #cfe8ff; }
Button.-primary:hover { background: #35647f; }
Input { background: #12151a; border: tall #2c3644; color: #d4d8de; }
Input:focus { border: tall #5aa9e6; }
Label { height: auto; }

TabbedContent { height: 1fr; }
TabPane { padding: 0; }
Tabs { background: #1a1e26; }
Tab { background: #1a1e26; color: #6b7280; padding: 0 2; }
Tab.-active { color: #5aa9e6; text-style: bold; }

Footer { background: #1a1e26; }
FooterKey { background: #1a1e26; color: #6b7280; }
FooterKey:hover { background: #232b37; }
"""
