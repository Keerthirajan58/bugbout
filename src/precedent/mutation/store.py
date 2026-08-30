"""Persist and reload mutation runs."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from precedent.artifacts import payload_of, read_artifact, write_artifact
from precedent.mutation.mutant import MutantResult
from precedent.mutation.runner import RunStats, stats_from
from precedent.paths import artifacts_dir
from precedent.targets.registry import TargetRepo

KIND = "mutation_run"


def operator_set_hash(operators: tuple[str, ...]) -> str:
    """Fingerprint of the operator selection.

    Recorded with every run so that two runs can be proven to have used the same
    mutant-generation policy. Comparing kill-rates produced by different operator
    sets would be meaningless, and this makes that mismatch detectable rather
    than invisible.
    """
    return hashlib.sha256("|".join(sorted(operators)).encode()).hexdigest()[:12]


def run_path(target_slug: str) -> Path:
    return artifacts_dir() / "mutation" / f"{target_slug}.json"


def save_run(
    target: TargetRepo,
    results: list[MutantResult],
    operators: tuple[str, ...],
    *,
    path: Path | None = None,
    freeze: bool = False,
    allow_refreeze: bool = False,
) -> tuple[Path, str]:
    """Write a mutation run artifact. Returns its path and content hash."""
    destination = path or run_path(target.slug)
    stats = stats_from(results)
    payload: dict[str, Any] = {
        "target": {
            "slug": target.slug,
            "full_name": target.full_name,
            "ref": target.ref,
            "commit": target.commit,
        },
        "operators": {
            "count": len(operators),
            "hash": operator_set_hash(operators),
            "names": sorted(operators),
        },
        "stats": {
            "total": stats.total,
            "scored": stats.scored,
            "killed": stats.killed,
            "killed_import": stats.killed_import,
            "survived": stats.survived,
            "timeout": stats.timeout,
            "incompetent": stats.incompetent,
            "no_mutation": stats.no_mutation,
            "kill_rate": round(stats.kill_rate, 6),
            "test_kill_rate": round(stats.test_kill_rate, 6),
        },
        "results": [r.to_dict() for r in results],
    }
    digest = write_artifact(
        destination,
        kind=KIND,
        payload=payload,
        freeze=freeze,
        allow_refreeze=allow_refreeze,
    )
    return destination, digest


def load_run(target_slug: str, *, path: Path | None = None) -> dict[str, Any]:
    """Read a mutation run artifact, verifying integrity if it is frozen."""
    return payload_of(read_artifact(path or run_path(target_slug), expect_kind=KIND))


def load_results(target_slug: str, *, path: Path | None = None) -> list[MutantResult]:
    payload = load_run(target_slug, path=path)
    return [MutantResult.from_dict(item) for item in payload["results"]]


def load_survivors(target_slug: str, *, path: Path | None = None) -> list[MutantResult]:
    """The surviving mutants: changes that pass the entire suite and are wrong.

    This is the corpus every later phase is evaluated against.
    """
    return [r for r in load_results(target_slug, path=path) if r.survived]


def stats_of(target_slug: str, *, path: Path | None = None) -> RunStats:
    stats = load_run(target_slug, path=path)["stats"]
    return RunStats(
        total=stats["total"],
        killed=stats["killed"],
        killed_import=stats.get("killed_import", 0),
        survived=stats["survived"],
        timeout=stats["timeout"],
        incompetent=stats["incompetent"],
        no_mutation=stats["no_mutation"],
    )
