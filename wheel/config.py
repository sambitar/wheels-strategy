"""Config load/save. The TOML file is the single source of truth.

tomllib reads TOML (stdlib in 3.11+) but cannot write it, and the config shape
here is a handful of flat tables of scalars, so a tiny serializer is cheaper
than another dependency.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _resolve_root() -> Path:
    """Find the project directory: config, journal and cache live beside it.

    Order: $WHEELS_HOME, then the source checkout, then the working directory
    (so an installed `wheel` command still finds your config and journal).
    """
    env = os.environ.get("WHEELS_HOME")
    if env:
        return Path(env).expanduser().resolve()

    source = Path(__file__).resolve().parent.parent
    if (source / "config.toml").exists() or (source / "pyproject.toml").exists():
        return source

    return Path.cwd()


ROOT = _resolve_root()

ROOT = _resolve_root()
CONFIG_PATH = ROOT / "config.toml"
STATE_DIR = ROOT / "state"
JOURNAL_PATH = STATE_DIR / "trades.jsonl"
CACHE_DIR = ROOT / ".cache"

SECTION_ORDER = ("account", "scan", "ui", "watchlist")


@dataclass
class Config:
    budget: float = 2000.0
    profile: str = "balanced"
    dte_min: int = 30
    dte_max: int = 60
    min_open_interest: int = 100
    max_spread_pct: float = 15.0
    risk_free_rate: float = 0.04
    top_n: int = 8
    refresh_seconds: int = 0
    symbols: list[str] = field(default_factory=lambda: ["FIG"])

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or CONFIG_PATH
        if not path.exists():
            cfg = cls()
            cfg.save(path)
            return cfg
        try:
            raw = tomllib.loads(path.read_text())
        except (tomllib.TOMLDecodeError, OSError):
            return cls()
        acc = raw.get("account", {})
        scan = raw.get("scan", {})
        ui = raw.get("ui", {})
        watch = raw.get("watchlist", {})
        cfg = cls()
        cfg.budget = float(acc.get("budget", cfg.budget))
        cfg.profile = str(acc.get("profile", cfg.profile))
        cfg.dte_min = int(scan.get("dte_min", cfg.dte_min))
        cfg.dte_max = int(scan.get("dte_max", cfg.dte_max))
        cfg.min_open_interest = int(scan.get("min_open_interest", cfg.min_open_interest))
        cfg.max_spread_pct = float(scan.get("max_spread_pct", cfg.max_spread_pct))
        cfg.risk_free_rate = float(scan.get("risk_free_rate", cfg.risk_free_rate))
        cfg.top_n = int(scan.get("top_n", cfg.top_n))
        cfg.refresh_seconds = int(ui.get("refresh_seconds", cfg.refresh_seconds))
        cfg.symbols = [str(s).upper().strip() for s in watch.get("symbols", cfg.symbols) if str(s).strip()]
        return cfg

    def save(self, path: Path | None = None) -> None:
        path = path or CONFIG_PATH
        lines = [
            "# Wheels configuration. Everything here is editable from the in-app",
            '# Settings screen (","), which writes this file back in place.',
            "",
            "[account]",
            "# Total capital you are willing to have committed to wheel positions.",
            f"budget = {self.budget:g}",
            "# Risk profile: conservative | balanced | aggressive",
            f'profile = "{self.profile}"',
            "",
            "[scan]",
            '# Expiration window in calendar days. 30-60 is "one to two months".',
            f"dte_min = {self.dte_min}",
            f"dte_max = {self.dte_max}",
            "# Liquidity gates. Contracts outside these are excluded, not scored low.",
            f"min_open_interest = {self.min_open_interest}",
            f"max_spread_pct = {self.max_spread_pct:g}",
            "# Assumed risk-free rate for Black-Scholes delta.",
            f"risk_free_rate = {self.risk_free_rate:g}",
            "# How many ranked candidates to return.",
            f"top_n = {self.top_n}",
            "",
            "[ui]",
            "refresh_seconds = 0",
            "",
            "[watchlist]",
            "symbols = [" + ", ".join(f'"{s}"' for s in self.symbols) + "]",
            "",
        ]
        path.write_text("\n".join(lines))

    # -- convenience -------------------------------------------------------
    def add_symbol(self, symbol: str) -> None:
        symbol = symbol.upper().strip()
        if symbol and symbol not in self.symbols:
            self.symbols.append(symbol)

    def remove_symbol(self, symbol: str) -> None:
        self.symbols = [s for s in self.symbols if s != symbol.upper().strip()]

    def as_dict(self) -> dict[str, Any]:
        return {
            "budget": self.budget,
            "profile": self.profile,
            "dte_min": self.dte_min,
            "dte_max": self.dte_max,
            "min_open_interest": self.min_open_interest,
            "max_spread_pct": self.max_spread_pct,
            "risk_free_rate": self.risk_free_rate,
            "top_n": self.top_n,
            "symbols": self.symbols,
        }
