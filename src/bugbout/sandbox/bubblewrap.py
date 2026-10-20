"""Linux, through bubblewrap (``bwrap``).

The whole filesystem is mounted read-only, with fresh ``/dev``, ``/proc`` and
``/tmp``; the given directories are mounted back writable, and a process that
may not use the network gets a network namespace of its own.
"""

from __future__ import annotations

import functools
import platform
import shutil
import subprocess
from typing import override

from bugbout.sandbox.base import Confinement, Sandbox

_PRIVATE_TMP = "/tmp"  # noqa: S108 - a fresh tmpfs inside the sandbox, not the host's /tmp


class Bubblewrap(Sandbox):
    name = "bubblewrap"

    @classmethod
    @override
    def problem(cls) -> str | None:
        if platform.system() != "Linux":
            return "bubblewrap only runs on Linux"
        if shutil.which("bwrap") is None:
            return "bubblewrap is not installed (e.g. `apt install bubblewrap`)"
        return _probe()

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
def _probe() -> str | None:
    """Why ``bwrap`` cannot create a sandbox here, or ``None`` if it can."""
    bwrap = shutil.which("bwrap") or "bwrap"
    try:
        completed = subprocess.run(  # noqa: S603 - the resolved bwrap binary, literal arguments
            [bwrap, "--ro-bind", "/", "/", "--unshare-net", "true"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"bubblewrap could not run: {exc}"
    if completed.returncode == 0:
        return None
    return explain(completed.stderr.strip())


def explain(error: str) -> str:
    """bubblewrap's own error, with the usual cause when it is a permission problem."""
    reason = f"bubblewrap cannot create a sandbox: {error or 'unknown error'}"
    if "ermission denied" in error or "not permitted" in error:
        reason += (
            "; on Ubuntu 24.04 and later, AppArmor restricts the user namespaces it needs "
            "(see `sysctl kernel.apparmor_restrict_unprivileged_userns`)"
        )
    return reason
