"""Names that are safe to use as file and module names."""

from __future__ import annotations

import re

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def slug(name: str) -> str:
    """``name`` with path separators and anything unusual replaced, e.g. ``codex__gpt-5``."""
    return _UNSAFE.sub("_", name.replace("/", "__")).strip("_") or "_"
