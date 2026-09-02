"""Persist each stage of the real-diff study as its own artifact.

The stages are deliberately separate files rather than one growing record.
Generating the agent diffs is the expensive, non-reproducible step - it involves
a coding agent working for minutes per task - so it must be possible to re-score
or re-audit without regenerating anything.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from precedent.artifacts import payload_of, read_artifact, write_artifact
from precedent.paths import artifacts_dir
from precedent.review.bench import AgentDiff, GroundTruth
from precedent.review.tasks import IssueTask

KIND_TASKS = "review_tasks"
KIND_DIFFS = "review_diffs"
KIND_TRUTH = "review_ground_truth"
KIND_AUDIT = "review_audit"


def review_dir() -> Path:
    return artifacts_dir() / "review"


def _path(name: str) -> Path:
    return review_dir() / f"{name}.json"


def save_tasks(tasks: list[IssueTask], *, mining: dict[str, Any]) -> tuple[Path, str]:
    path = _path("tasks")
    digest = write_artifact(
        path, kind=KIND_TASKS, payload={"mining": mining, "tasks": [t.to_dict() for t in tasks]}
    )
    return path, digest


def load_tasks() -> list[IssueTask]:
    payload = payload_of(read_artifact(_path("tasks"), expect_kind=KIND_TASKS))
    return [IssueTask.from_dict(t) for t in payload["tasks"]]


def save_diffs(diffs: list[AgentDiff]) -> tuple[Path, str]:
    path = _path("diffs")
    digest = write_artifact(path, kind=KIND_DIFFS, payload={"diffs": [d.to_dict() for d in diffs]})
    return path, digest


def load_diffs() -> list[AgentDiff]:
    payload = payload_of(read_artifact(_path("diffs"), expect_kind=KIND_DIFFS))
    return [AgentDiff.from_dict(d) for d in payload["diffs"]]


def save_ground_truth(truths: list[GroundTruth]) -> tuple[Path, str]:
    path = _path("ground_truth")
    digest = write_artifact(
        path, kind=KIND_TRUTH, payload={"ground_truth": [g.to_dict() for g in truths]}
    )
    return path, digest


def load_ground_truth() -> list[GroundTruth]:
    payload = payload_of(read_artifact(_path("ground_truth"), expect_kind=KIND_TRUTH))
    return [GroundTruth.from_dict(g) for g in payload["ground_truth"]]


def save_audit(payload: dict[str, Any], *, freeze: bool = False) -> tuple[Path, str]:
    path = _path("audit")
    return path, write_artifact(path, kind=KIND_AUDIT, payload=payload, freeze=freeze)


def load_audit() -> dict[str, Any]:
    return payload_of(read_artifact(_path("audit"), expect_kind=KIND_AUDIT))
