"""The arena's artifacts, one file per stage.

Stages stay separate because their costs differ by orders of magnitude. An
oracle takes seconds to rebuild; a contestant's patch took a coding agent
minutes of quota and cannot be regenerated identically. Re-scoring or re-judging
must never require re-running a contestant.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from verdict.arena.truth import Oracle, Resolution
from verdict.artifacts import payload_of, read_artifact, write_artifact
from verdict.paths import artifacts_dir

KIND_ORACLES = "arena_oracles"
KIND_ENTRIES = "arena_entries"
KIND_RESOLUTIONS = "arena_resolutions"


def arena_dir() -> Path:
    return artifacts_dir() / "arena"


def save_oracles(oracles: list[Oracle]) -> tuple[Path, str]:
    path = arena_dir() / "oracles.json"
    return path, write_artifact(
        path, kind=KIND_ORACLES, payload={"oracles": [o.to_dict() for o in oracles]}
    )


def load_oracles() -> dict[str, Oracle]:
    payload = payload_of(read_artifact(arena_dir() / "oracles.json", expect_kind=KIND_ORACLES))
    return {o["task_id"]: Oracle.from_dict(o) for o in payload["oracles"]}


def entries_path(entrant: str) -> Path:
    return arena_dir() / "entries" / f"{entrant.replace('/', '__')}.json"


def save_entries(entrant: str, entries: list[dict[str, Any]], meta: dict[str, Any]) -> Path:
    """Every patch one entrant produced, with how it was produced."""
    path = entries_path(entrant)
    write_artifact(
        path, kind=KIND_ENTRIES, payload={"entrant": entrant, "meta": meta, "entries": entries}
    )
    return path


def load_entries(entrant: str) -> dict[str, Any]:
    return payload_of(read_artifact(entries_path(entrant), expect_kind=KIND_ENTRIES))


def list_entrants() -> list[str]:
    folder = arena_dir() / "entries"
    return (
        sorted(p.stem.replace("__", "/") for p in folder.glob("*.json")) if folder.exists() else []
    )


def save_resolutions(resolutions: list[Resolution]) -> tuple[Path, str]:
    path = arena_dir() / "resolutions.json"
    return path, write_artifact(
        path,
        kind=KIND_RESOLUTIONS,
        payload={"resolutions": [r.to_dict() for r in resolutions]},
    )


def load_resolutions() -> list[Resolution]:
    payload = payload_of(
        read_artifact(arena_dir() / "resolutions.json", expect_kind=KIND_RESOLUTIONS)
    )
    return [Resolution.from_dict(r) for r in payload["resolutions"]]
