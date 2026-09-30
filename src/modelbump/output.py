"""Terminal output: color, progress, and formatting helpers.

Colors are disabled when stdout is not a TTY, when ``NO_COLOR`` is set, or when
``--no-color`` is passed. This keeps report-pipeline output clean in CI.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Iterable
from typing import TextIO

_COLOR_ENABLED: bool | None = None


def _supports_color(stream: TextIO) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("TERM") == "dumb":
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def init_color(enabled: bool | None = None) -> None:
    global _COLOR_ENABLED
    if enabled is None:
        _COLOR_ENABLED = _supports_color(sys.stdout)
    else:
        _COLOR_ENABLED = enabled


def color_enabled() -> bool:
    global _COLOR_ENABLED
    if _COLOR_ENABLED is None:
        init_color()
    return bool(_COLOR_ENABLED)


class C:
    """ANSI color codes (empty strings when color is off)."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"


def paint(text: str, *codes: str) -> str:
    if not color_enabled() or not codes:
        return text
    return "".join(codes) + text + C.RESET


def bold(text: str) -> str:
    return paint(text, C.BOLD)


def dim(text: str) -> str:
    return paint(text, C.DIM)


def ok(text: str) -> str:
    return paint(text, C.BRIGHT_GREEN)


def warn(text: str) -> str:
    return paint(text, C.BRIGHT_YELLOW)


def err(text: str) -> str:
    return paint(text, C.BRIGHT_RED)


def info(text: str) -> str:
    return paint(text, C.BRIGHT_CYAN)


def accent(text: str) -> str:
    return paint(text, C.BRIGHT_MAGENTA)


def verdict_color(passed: bool) -> str:
    return C.BRIGHT_GREEN if passed else C.BRIGHT_RED


def badge(passed: bool) -> str:
    return paint(" PASS " if passed else " FAIL ", verdict_color(passed), C.BOLD)


def rule(width: int = 68, char: str = "─") -> str:
    return dim(char * width)


def sparkline(values: Iterable[float]) -> str:
    """A tiny unicode sparkline, useful for latency distributions."""
    bars = "▁▂▃▄▅▆▇█"
    vals = list(values)
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return bars[0] * len(vals)
    return "".join(bars[min(7, int((v - lo) / (hi - lo) * 7.99))] for v in vals)


# ---------------------------------------------------------------------------
# Progress bar
# ---------------------------------------------------------------------------

class ProgressBar:
    """A minimal, dependency-free progress bar for the TTY runner."""

    def __init__(self, total: int, label: str = "running", stream: TextIO | None = None) -> None:
        self.total = max(1, total)
        self.done = 0
        self.label = label
        self.stream = stream or sys.stderr
        self.enabled = bool(getattr(self.stream, "isatty", lambda: False)())
        self.started = time.time()
        self._last_draw = 0.0
        self._notes: dict[str, int] = {}

    def advance(self, note: str | None = None) -> None:
        self.done += 1
        if note:
            self._notes[note] = self._notes.get(note, 0) + 1
        now = time.time()
        if self.enabled and now - self._last_draw > 0.08:
            self._last_draw = now
            self.draw()

    def draw(self, final: bool = False) -> None:
        if not self.enabled:
            return
        width = 28
        frac = self.done / self.total
        filled = int(width * frac)
        bar = "█" * filled + "░" * (width - filled)
        elapsed = time.time() - self.started
        rate = self.done / elapsed if elapsed > 0 else 0
        line = (
            f"\r{paint(self.label, C.BRIGHT_CYAN)} {paint(bar, C.BRIGHT_BLUE)} "
            f"{self.done}/{self.total} {dim(f'{rate:5.1f}/s {elapsed:5.1f}s')}"
        )
        self.stream.write(line)
        self.stream.flush()
        if final:
            self.stream.write("\n")
            self.stream.flush()

    def close(self) -> None:
        if self.enabled:
            self.draw(final=True)

    def summary(self) -> str:
        if not self._notes:
            return ""
        parts = [f"{k}={v}" for k, v in sorted(self._notes.items())]
        return " ".join(parts)


def table(headers: list[str], rows: list[list[str]], aligns: list[str] | None = None) -> str:
    """Render a plain-text table with box-drawing separators."""
    if not rows:
        return dim("(no rows)")
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    aligns = aligns or ["left"] * len(headers)

    def fmt(cells: list[str]) -> str:
        out = []
        for i, cell in enumerate(cells):
            cell = str(cell)
            if aligns[i] == "right":
                out.append(cell.rjust(widths[i]))
            else:
                out.append(cell.ljust(widths[i]))
        return " │ ".join(out)

    lines = [fmt(headers), "─┼─".join("─" * w for w in widths)]
    lines.extend(fmt([str(c) for c in row]) for row in rows)
    return "\n".join(lines)
