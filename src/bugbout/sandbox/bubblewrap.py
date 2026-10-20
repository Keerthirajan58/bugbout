"""Linux, through bubblewrap (``bwrap``).

The whole filesystem is mounted read-only, with fresh ``/dev``, ``/proc`` and
``/tmp``; the given directories are mounted back writable, and a process that
may not use the network gets a network namespace of its own.
"""

from __future__ import annotations

import functools
import shutil
import subprocess
import sys
from typing import override

from bugbout.sandbox.base import Confinement, Sandbox

_PRIVATE_TMP = "/tmp"  # noqa: S108 - a fresh tmpfs inside the sandbox, not the host's /tmp


class Bubblewrap(Sandbox):
    name = "bubblewrap"

    @classmethod
    @override
    def available(cls) -> bool:
        return sys.platform.startswith("linux") and _probe()

    @override
    def prefix(self, confinement: Confinement) -> list[str]:
        argv = [
            shutil.which("bwrap") or "bwrap",
            "--die-with-parent",
            "--unshare-pid",
            "--ro-bind", "/", "/",
            "--dev", "/dev",
            "--proc", "/proc",
            "--tmpfs", _PRIVATE_TMP,
            "--setenv", "TMPDIR", _PRIVATE_TMP,
        ]  # fmt: skip
        for root in confinement.writable:
            path = str(root.resolve())
            argv += ["--bind", path, path]
        if not confinement.network:
            argv.append("--unshare-net")
        return [*argv, "--"]


@functools.cache
def _probe() -> bool:
    """``bwrap`` is installed and allowed to create namespaces here."""
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        return False
    try:
        completed = subprocess.run(  # noqa: S603 - the resolved bwrap binary, literal arguments
            [bwrap, "--ro-bind", "/", "/", "--unshare-net", "true"],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0
