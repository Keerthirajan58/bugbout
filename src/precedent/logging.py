"""Console logging.

One configuration function, called once from the CLI entrypoint, so that library
code can use :func:`logging.getLogger` without knowing how output is rendered.
"""

from __future__ import annotations

import logging

from rich.console import Console
from rich.logging import RichHandler

console = Console(stderr=True)


def configure_logging(verbose: bool = False) -> None:
    """Install a Rich handler on the root logger.

    Idempotent: repeated calls replace handlers rather than stacking them, which
    otherwise duplicates every line when the CLI is invoked in-process by tests.
    """
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    root.addHandler(
        RichHandler(
            console=console,
            rich_tracebacks=True,
            show_path=verbose,
            omit_repeated_times=False,
        )
    )
    # These are chatty at DEBUG and never carry information we act on.
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a module logger."""
    return logging.getLogger(name)
