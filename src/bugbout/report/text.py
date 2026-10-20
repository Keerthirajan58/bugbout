"""Small formatting rules shared by every way of showing a report."""

from __future__ import annotations


def percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def share(part: int, whole: int) -> str:
    return f"{part}/{whole} ({percent(part / whole)})" if whole else "0/0"


def duration(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes}m {rest:02d}s" if minutes else f"{rest}s"


def auc(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"
