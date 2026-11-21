"""Small formatting helpers shared by every view."""

from __future__ import annotations

from rich.text import Text

from .theme import AMBER, DIM, GREEN, RED


def money(x: float | None, decimals: int = 0, sign: bool = False) -> str:
    if x is None:
        return "—"
    prefix = "+" if sign and x > 0 else ""
    return f"{prefix}${x:,.{decimals}f}"


def pct(x: float | None, decimals: int = 1, sign: bool = True) -> str:
    if x is None:
        return "—"
    prefix = "+" if sign and x > 0 else ""
    return f"{prefix}{x:.{decimals}f}%"


def pl_text(value: float | None, decimals: int = 0) -> Text:
    """P/L is the one place colour always carries meaning."""
    if value is None:
        return Text("—", style=DIM)
    if value > 0:
        return Text(money(value, decimals, sign=True), style=GREEN)
    if value < 0:
        return Text(money(value, decimals, sign=True), style=RED)
    return Text(money(0, decimals), style=DIM)


def urgency_text(text: str, urgency: str) -> Text:
    style = DIM
    if urgency == "high":
        style = RED
    elif urgency == "normal":
        style = AMBER
    return Text(text, style=style)


def short_flags(flags: list[str]) -> str:
    """Terminal-width friendly flag codes. The detail pane spells them out."""
    codes = []
    for f in flags:
        if f.startswith("earnings"):
            codes.append("earn")
        elif f.startswith("ex-div"):
            codes.append("exdiv")
        elif f.startswith("falling knife"):
            codes.append("knife")
        elif f.startswith("near 52-week low"):
            codes.append("52lo")
        elif f.startswith("near 52-week high"):
            codes.append("52hi")
        elif f.startswith("thin cushion"):
            codes.append("thin")
        elif f.startswith("IV much richer"):
            codes.append("ivrich")
        else:
            codes.append(f.split(" (")[0][:6])
    return " ".join(codes)


def score_bar(score: float, width: int = 8) -> Text:
    """Compact visual rank. Filled blocks, dim remainder."""
    filled = max(0, min(width, round(score / 100 * width * 2.2)))
    bar = "█" * filled + "░" * (width - filled)
    style = GREEN if score >= 45 else AMBER if score >= 25 else DIM
    return Text(bar, style=style)


def dte_text(dte: int | None) -> Text:
    if dte is None:
        return Text("—", style=DIM)
    if dte < 0:
        return Text("exp", style=RED)
    if dte <= 7:
        return Text(str(dte), style=AMBER)
    return Text(str(dte), style=DIM)


def truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"
