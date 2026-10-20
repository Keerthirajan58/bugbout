"""macOS seatbelt, through ``sandbox-exec``.

The profile is the whole policy, so it is kept small and readable: everything is
allowed except writing outside the given directories and, unless permitted,
using the network.
"""

from __future__ import annotations

import functools
import subprocess
import sys
from pathlib import Path
from typing import override

from bugbout.sandbox.base import Confinement, Sandbox

SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")
_TEMPORARY = (Path("/private/tmp"), Path("/private/var/folders"))
_DEVICES = '    (literal "/dev/null")\n    (literal "/dev/tty")\n    (regex #"^/dev/fd/")'
_OFFLINE = "(deny network*)\n(allow network* (local unix-socket))\n"


class Seatbelt(Sandbox):
    name = "seatbelt"

    @classmethod
    @override
    def available(cls) -> bool:
        return sys.platform == "darwin" and _probe()

    @override
    def prefix(self, confinement: Confinement) -> list[str]:
        return [str(SANDBOX_EXEC), "-p", profile(confinement)]


def profile(confinement: Confinement) -> str:
    roots = [*confinement.writable, *_TEMPORARY]
    subpaths = "\n".join(f'    (subpath "{_escape(root)}")' for root in roots)
    return (
        "(version 1)\n"
        "(allow default)\n"
        "(deny file-write*)\n"
        f"(allow file-write*\n{subpaths}\n{_DEVICES})\n"
        f"{'' if confinement.network else _OFFLINE}"
    )


def _escape(path: Path) -> str:
    return str(path.resolve()).replace("\\", "\\\\").replace('"', '\\"')


@functools.cache
def _probe() -> bool:
    """``sandbox-exec`` is present and usable - it is not, inside another sandbox."""
    if not SANDBOX_EXEC.exists():
        return False
    try:
        completed = subprocess.run(  # noqa: S603 - a fixed system binary and literal profile
            [str(SANDBOX_EXEC), "-p", "(version 1)(allow default)", "/usr/bin/true"],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0
