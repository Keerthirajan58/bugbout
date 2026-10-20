"""Waiting on operating-system processes from tests."""

from __future__ import annotations

import os
import time


def gone(pid: int, wait: float = 10.0) -> bool:
    """Whether process ``pid`` has exited, giving it up to ``wait`` seconds to do so."""
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    return False
