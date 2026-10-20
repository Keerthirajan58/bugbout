"""Console logging.

Library code only ever calls :func:`get_logger`; how output is rendered is
decided once, by the CLI, through :func:`configure_logging`.
"""

from __future__ import annotations

import logging

from rich.console import Console
from rich.logging import RichHandler

console = Console(stderr=True)


def configure_logging(verbose: bool = False) -> None:
    """Install a Rich handler on the root logger. Safe to call more than once."""
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.addHandler(
        RichHandler(console=console, rich_tracebacks=True, show_path=verbose, show_time=True)
    )
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
