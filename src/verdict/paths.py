"""Canonical filesystem locations.

Every path Verdict writes to is derived here so that a run can be relocated
(CI, a different checkout) by setting ``VERDICT_DATA_DIR`` alone.
"""

from __future__ import annotations

import os
from pathlib import Path

_ENV_DATA_DIR = "VERDICT_DATA_DIR"
_ENV_HOME = "VERDICT_HOME"


def project_root() -> Path:
    """Repository root, resolved from this file's location.

    ``parents[2]`` is ``<root>/src/verdict/paths.py`` -> ``<root>``.
    """
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    """Root of all generated data. Override with ``VERDICT_DATA_DIR``."""
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


def verdict_home() -> Path:
    """Where the arena runs coding agents: environments, checkouts, transcripts.

    Deliberately outside the repository. This project's directory name contains
    spaces, and a coding agent runs shell commands a model wrote - where an
    unquoted path does not fail loudly, it just quietly means something else.
    Override with ``VERDICT_HOME``.
    """
    override = os.environ.get(_ENV_HOME)
    return Path(override).expanduser().resolve() if override else Path.home() / ".verdict"


def ensure_dirs() -> None:
    """Create the standard data directories if they do not yet exist."""
    for path in (data_dir(), workspaces_dir(), artifacts_dir(), cache_dir()):
        path.mkdir(parents=True, exist_ok=True)
