"""Operating-system sandboxes for the commands agents run and the tests they write."""

from __future__ import annotations

from bugbout.errors import ConfigError, SandboxUnavailableError
from bugbout.sandbox.base import Confinement, NoSandbox, Sandbox
from bugbout.sandbox.bubblewrap import Bubblewrap
from bugbout.sandbox.seatbelt import Seatbelt

BACKENDS: dict[str, type[Sandbox]] = {
    backend.name: backend for backend in (Seatbelt, Bubblewrap, NoSandbox)
}


def detect() -> Sandbox:
    """This platform's sandbox: seatbelt on macOS, bubblewrap on Linux."""
    for backend in (Seatbelt, Bubblewrap):
        if backend.available():
            return backend()
    raise SandboxUnavailableError(
        "No sandbox is available on this machine. On Linux, install bubblewrap "
        '(e.g. `apt install bubblewrap`); or set sandbox = "none" in bugbout.toml '
        "to let agents run commands unconfined."
    )


def get_sandbox(name: str = "auto") -> Sandbox:
    """The sandbox called ``name``, or the platform's own for ``auto``."""
    if name == "auto":
        return detect()
    backend = BACKENDS.get(name)
    if backend is None:
        choices = ", ".join(["auto", *BACKENDS])
        raise ConfigError(f"unknown sandbox {name!r}; choose one of: {choices}")
    if not backend.available():
        raise SandboxUnavailableError(f"the {name} sandbox is not available on this machine")
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
