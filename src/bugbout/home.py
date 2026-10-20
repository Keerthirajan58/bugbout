"""Where bugbout keeps heavy, disposable state: environments and checkouts.

Results live with the project (``.bugbout/`` beside ``bugbout.toml``); the
expensive working state lives here, in a cache directory, so it never lands in
anyone's repository. Coding agents run shell commands a model wrote, so the
default path deliberately avoids spaces. Override with ``BUGBOUT_HOME``.
"""

from __future__ import annotations

import os
from pathlib import Path

HOME_ENV = "BUGBOUT_HOME"


def bugbout_home() -> Path:
    override = os.environ.get(HOME_ENV)
    if override:
        return Path(override).expanduser().resolve()
    cache = os.environ.get("XDG_CACHE_HOME")
    return (Path(cache) if cache else Path.home() / ".cache") / "bugbout"
