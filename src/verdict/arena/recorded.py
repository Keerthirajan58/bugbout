"""Bring the earlier study's Claude Code patches into the arena.

They were captured as ``diff -ruN`` output with absolute paths into this
project's own (space-containing) directory. Each is replayed onto a fresh base
tree, re-measured in the portable format, and verified by round trip before it
is admitted - a converted patch that does not reproduce the recorded tree
exactly is an error, not an entry.

They are labelled as recorded wherever they appear: the prompt that produced
them was not the arena's standard one, and they cannot be re-run.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from verdict.arena.agents.base import RunRecord
from verdict.arena.layout import ArenaLayout
from verdict.arena.patch import Patch
from verdict.arena.race import Attempt
from verdict.review.bench import AgentDiff, TaskBench, apply_hunks, parse_patch
from verdict.review.tasks import IssueTask

RECORDED_ENTRANT = "claude-code"
RECORDED_META = {
    "harness": "claude-code",
    "source": "recorded by the earlier real-diff study (2026-09-01)",
    "note": "not re-run: the prompt was not the arena's standard prompt",
}


class RecordedImporter:
    def __init__(self, layout: ArenaLayout) -> None:
        self.layout = layout

    def attempt(self, task: IssueTask, diff: AgentDiff, recorded: TaskBench) -> Attempt:
        patch = self.convert(task, diff, recorded)
        test_root = self.layout.bench(task.target_slug).target.test_root
        return Attempt(
            task_id=task.task_id,
            agent=RECORDED_ENTRANT,
            assignment="fix",
            status="recorded",
            record=RunRecord(RECORDED_ENTRANT, None, False, 0.0),
            patch=patch,
            source_files=patch.only(under=test_root, keep=False).files,
            test_files=patch.only(under=test_root, keep=True).files,
        )

    def convert(self, task: IssueTask, diff: AgentDiff, recorded: TaskBench) -> Patch:
        """Re-measure one recorded diff as a portable :class:`Patch`, verified."""
        if diff.empty:
            return Patch("")
        work = self.layout.scratch / task.task_id
        base, after, check = (work / f"convert-{n}" for n in ("base", "after", "check"))
        try:
            for tree in (base, after, check):
                self.layout.checkout(task, tree)
            self._replay(diff, recorded.pristine(task), base, after)
            patch = Patch.between(base, after)
            if set(patch.files) != set(diff.changed_files):
                raise ValueError(
                    f"{task.task_id}: converted {patch.files}, recorded {diff.changed_files}"
                )
            patch.apply(check)
            if not Patch.between(check, after).empty:
                raise ValueError(f"{task.task_id}: converted patch does not round-trip")
            return patch
        finally:
            for tree in (base, after, check):
                shutil.rmtree(tree, ignore_errors=True)

    @staticmethod
    def _replay(diff: AgentDiff, recorded_prefix: Path, base: Path, after: Path) -> None:
        for rel, hunks in parse_patch(diff.patch, str(recorded_prefix)).items():
            if rel not in diff.changed_files:
                continue  # test-run residue the recorded file list already excluded
            source = (
                (base / rel).read_text().splitlines(keepends=True) if (base / rel).exists() else []
            )
            (after / rel).parent.mkdir(parents=True, exist_ok=True)
            (after / rel).write_text("".join(apply_hunks(source, hunks)), encoding="utf-8")
