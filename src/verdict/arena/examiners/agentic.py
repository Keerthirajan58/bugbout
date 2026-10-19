"""Agentic examiners: a coding agent that captures the issue as a failing test.

The examiner works in its own clean checkout of the base commit, may explore
and execute anything, and is told to fix nothing. Its brief requires the test
to fail on the unfixed code *for the reason the issue describes* - so, unlike a
one-shot answer, it has checked its own expectations against the real library
before submitting. Any source edits it makes anyway are discarded and noted.

It runs through the same :class:`Race` as the contestants: same isolation, same
journal, same lanes. Only the assignment differs.
"""

from __future__ import annotations

from verdict.arena.agents.base import CodingAgent
from verdict.arena.assignments import Reproduce
from verdict.arena.examiners.base import Examiner, ExamRoom
from verdict.arena.examiners.exhibit import Exhibit
from verdict.arena.race import Attempt, Race
from verdict.review.tasks import IssueTask


class AgentExaminer(Examiner):
    def __init__(
        self, agent: CodingAgent, *, timeout: int = 1800, lane_slots: dict[str, int] | None = None
    ) -> None:
        self.agent = agent
        self.author = f"examiner/{agent.name}"
        self.timeout = timeout
        self.lane_slots = lane_slots or {}

    @property
    def agents(self) -> list[CodingAgent]:
        return [self.agent]

    def describe(self) -> dict[str, object]:
        return {"kind": "agentic", **self.agent.describe()}

    def examine(self, tasks: list[IssueTask], room: ExamRoom) -> list[Exhibit]:
        race = Race(
            room.layout,
            room.suite,
            Reproduce(),
            timeout=self.timeout,
            lane_slots=self.lane_slots,
        )
        attempts = {a.task_id: a for a in race.run(tasks, [self.agent])}
        return [self._exhibit(task, attempts[task.task_id], room) for task in tasks]

    def _exhibit(self, task: IssueTask, attempt: Attempt, room: ExamRoom) -> Exhibit:
        test_root = room.layout.bench(task.target_slug).target.test_root
        created = attempt.patch.only(under=test_root, keep=True).created()
        wanted = f"{test_root}/{Reproduce.test_file}"
        code = created.get(wanted) or next(iter(created.values()), "")
        notes = []
        if attempt.source_files:
            notes.append(f"edited {len(attempt.source_files)} source file(s); edits discarded")
        if code and wanted not in created:
            notes.append(f"test written to {next(iter(created))} instead of {wanted}")
        return Exhibit(
            task_id=task.task_id,
            author=self.author,
            vendor=self.vendor,
            kind="blind",
            test_code=code,
            reasoning=attempt.record.summary[:2000],
            model=self.agent.name,
            error="" if code else f"no reproduction test written ({attempt.status})",
            note="; ".join(notes),
        )
