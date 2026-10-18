"""Bring the earlier study's Claude Code patches into the arena.

They were captured as ``diff -ruN`` output with absolute paths into this
project's own (space-containing) directory. Each is replayed onto a fresh base
tree, re-measured in the portable format, and verified by round trip before it
is admitted - a converted patch that does not reproduce the recorded tree
exactly is an error, not an entry.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from verdict.arena.patch import Patch
from verdict.review.bench import AgentDiff, TaskBench, _apply_hunks, _parse_patch
from verdict.review.tasks import IssueTask

RECORDED_ENTRANT = "claude-code"


def convert(
    task: IssueTask, diff: AgentDiff, recorded: TaskBench, arena: TaskBench, scratch: Path
) -> Patch:
    """Re-measure one recorded diff as a portable :class:`Patch`."""
    if diff.empty:
        return Patch("")
    base, after, check = (
        scratch / task.task_id / f"convert-{n}" for n in ("base", "after", "check")
    )
    for tree in (base, after, check):
        shutil.rmtree(tree, ignore_errors=True)
        arena.archive(task.base_commit, tree)
    for rel, hunks in _parse_patch(diff.patch, str(recorded.pristine(task))).items():
        if rel not in diff.changed_files:
            continue  # test-run residue the recorded file list already excluded
        source = (base / rel).read_text().splitlines(keepends=True) if (base / rel).exists() else []
        (after / rel).parent.mkdir(parents=True, exist_ok=True)
        (after / rel).write_text("".join(_apply_hunks(source, hunks)), encoding="utf-8")

    patch = Patch.between(base, after)
    if set(patch.files) != set(diff.changed_files):
        raise ValueError(f"{task.task_id}: converted {patch.files}, recorded {diff.changed_files}")
    patch.apply(check)
    if not Patch.between(check, after).empty:
        raise ValueError(f"{task.task_id}: converted patch does not round-trip")
    for tree in (base, after, check):
        shutil.rmtree(tree, ignore_errors=True)
    return patch


def entry(task: IssueTask, patch: Patch, test_root: str) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "entrant": RECORDED_ENTRANT,
        "status": "recorded",
        "record": {},
        "patch": patch.to_dict(),
        "fix_files": list(patch.only(under=test_root, keep=False).files),
        "test_files": list(patch.only(under=test_root, keep=True).files),
    }
