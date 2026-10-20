"""Operating-system sandboxes for the commands agents run and the tests they write."""

from __future__ import annotations

import platform

from bugbout.errors import ConfigError, SandboxUnavailableError
from bugbout.sandbox.base import Confinement, NoSandbox, Sandbox
from bugbout.sandbox.bubblewrap import Bubblewrap
from bugbout.sandbox.seatbelt import Seatbelt

BACKENDS: dict[str, type[Sandbox]] = {
    backend.name: backend for backend in (Seatbelt, Bubblewrap, NoSandbox)
}


def detect() -> Sandbox:
    """This platform's sandbox: seatbelt on macOS, bubblewrap on Linux."""
    native = Seatbelt if platform.system() == "Darwin" else Bubblewrap
    problem = native.problem()
    if problem is None:
        return native()
    raise SandboxUnavailableError(
        f"no sandbox is available: {problem}. To run agents' commands unconfined "
        'instead, set sandbox = "none" under [run] in bugbout.toml'
    )


def get_sandbox(name: str = "auto") -> Sandbox:
    """The sandbox called ``name``, or the platform's own for ``auto``."""
    if name == "auto":
        return detect()
    backend = BACKENDS.get(name)
    if backend is None:
        choices = ", ".join(["auto", *BACKENDS])
        raise ConfigError(f"unknown sandbox {name!r}; choose one of: {choices}")
    if (problem := backend.problem()) is not None:
        raise SandboxUnavailableError(problem)
    return backend()


__all__ = [
    "BACKENDS",
    "Bubblewrap",
    "Confinement",
    "NoSandbox",
    "Sandbox",
    "Seatbelt",
    "detect",
    "get_sandbox",
]
