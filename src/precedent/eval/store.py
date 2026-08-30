"""Persist evaluation runs, and compute the ablation between two arms."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from precedent.artifacts import payload_of, read_artifact, write_artifact
from precedent.eval.case import CaseOutcome
from precedent.eval.harness import EvalStats, stats_from
from precedent.paths import artifacts_dir
from precedent.targets.registry import TargetRepo

KIND = "eval_run"

# The two arms of the ablation. `no_retrieval` is the phase 2 control; the only
# difference in `with_retrieval` is that the Prosecutor's prompt carries mined
# repository history.
ARM_NO_RETRIEVAL = "no_retrieval"
ARM_WITH_RETRIEVAL = "with_retrieval"


def eval_path(target_slug: str, arm: str) -> Path:
    return artifacts_dir() / "eval" / f"{target_slug}.{arm}.json"


def journal_path(target_slug: str, arm: str) -> Path:
    """Where in-progress results are appended, so a run can resume."""
    return artifacts_dir() / "eval" / "journal" / f"{target_slug}.{arm}.jsonl"


def save_eval(
    target: TargetRepo,
    arm: str,
    outcomes: list[CaseOutcome],
    *,
    mutation_run_hash: str,
    model: str,
    freeze: bool = False,
    allow_refreeze: bool = False,
) -> tuple[Path, str]:
    """Write an evaluation artifact. Returns its path and content hash.

    ``mutation_run_hash`` ties this evaluation to the exact corpus it scored.
    Comparing two arms that ran against different survivor sets would produce a
    meaningless delta, so the link is recorded rather than assumed.
    """
    stats = stats_from(outcomes)
    payload: dict[str, Any] = {
        "arm": arm,
        "target": {
            "slug": target.slug,
            "full_name": target.full_name,
            "ref": target.ref,
            "commit": target.commit,
        },
        "corpus": {"mutation_run_hash": mutation_run_hash, "size": len(outcomes)},
        "model": model,
        "stats": {
            "total": stats.total,
            "caught": stats.caught,
            "catch_rate": round(stats.catch_rate, 6),
            "by_verdict": stats.by_verdict,
        },
        "cases": [o.to_dict() for o in outcomes],
    }
    destination = eval_path(target.slug, arm)
    digest = write_artifact(
        destination, kind=KIND, payload=payload, freeze=freeze, allow_refreeze=allow_refreeze
    )
    return destination, digest


def load_eval(target_slug: str, arm: str) -> dict[str, Any]:
    return payload_of(read_artifact(eval_path(target_slug, arm), expect_kind=KIND))


def load_cases(target_slug: str, arm: str) -> list[CaseOutcome]:
    return [CaseOutcome.from_dict(c) for c in load_eval(target_slug, arm)["cases"]]


def stats_of(target_slug: str, arm: str) -> EvalStats:
    stats = load_eval(target_slug, arm)["stats"]
    return EvalStats(total=stats["total"], caught=stats["caught"], by_verdict=stats["by_verdict"])
