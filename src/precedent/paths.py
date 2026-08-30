"""Canonical filesystem locations.

Every path Precedent writes to is derived here so that a run can be relocated
(CI, a different checkout) by setting ``PRECEDENT_DATA_DIR`` alone.
"""

from __future__ import annotations

import os
from pathlib import Path

_ENV_DATA_DIR = "PRECEDENT_DATA_DIR"


def project_root() -> Path:
    """Repository root, resolved from this file's location.

    ``parents[2]`` is ``<root>/src/precedent/paths.py`` -> ``<root>``.
    """
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    """Root of all generated data. Override with ``PRECEDENT_DATA_DIR``."""
    override = os.environ.get(_ENV_DATA_DIR)
    return Path(override).expanduser().resolve() if override else project_root() / "data"


def workspaces_dir() -> Path:
    """Checked-out target repositories and their isolated virtualenvs."""
    return data_dir() / "workspaces"


def artifacts_dir() -> Path:
    """Durable, checked-in-worthy run outputs (mutation runs, eval baselines)."""
    return data_dir() / "artifacts"


def cache_dir() -> Path:
    """Disposable caches (LLM responses, embeddings). Safe to delete."""
    return data_dir() / "cache"


def ensure_dirs() -> None:
    """Create the standard data directories if they do not yet exist."""
    for path in (data_dir(), workspaces_dir(), artifacts_dir(), cache_dir()):
        path.mkdir(parents=True, exist_ok=True)
