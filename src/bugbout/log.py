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
    """Show bugbout's own messages through Rich; other libraries' logging is left alone.

    Safe to call more than once.
    """
    logger = logging.getLogger("bugbout")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.addHandler(RichHandler(console=console, show_path=verbose, show_time=True))
    logger.propagate = False


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
