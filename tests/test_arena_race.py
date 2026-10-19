"""The race and the agentic examiner, driven by a fake agent - no CLI, no model."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from verdict.arena.agents.base import CodingAgent, RunRecord
from verdict.arena.assignments import REPRODUCTION_TEST, Fix
from verdict.arena.examiners import AgentExaminer, ExamRoom
from verdict.arena.layout import ArenaLayout
from verdict.arena.race import Race
from verdict.review.tasks import IssueTask
from verdict.targets.registry import TargetRepo

TARGET = TargetRepo(
    slug="toy",
    owner="o",
    name="toy",
    ref="r",
    commit="c",
    spdx_license="MIT",
    source_root="src/toy",
    test_root="tests",
)


def _task(task_id: str) -> IssueTask:
    return IssueTask(
        task_id=task_id,
        target_slug="toy",
        base_commit="b",
        fix_commit="f",
        reference=1,
        reference_kind="issue",
        title="toy bug",
        problem_statement="the toy is broken",
        source_files=("src/toy/core.py",),
        test_files=("tests/test_core.py",),
        oracle_tests=("test_it",),
    )


@dataclass
class _Bench:
    target: TargetRepo = TARGET
    python: Path = Path("/usr/bin/python3")


class _Layout(ArenaLayout):
    """A layout whose base tree is a tiny fixture rather than a git archive."""

    def __init__(self, home: Path) -> None:
        super().__init__(home)
        base = home / "fixture"
        (base / "src" / "toy").mkdir(parents=True)
        (base / "src" / "toy" / "core.py").write_text("x = 1\n")
        (base / "tests").mkdir()
        self._base = base

    def bench(self, slug: str) -> Any:
        return _Bench()

    def pristine(self, task: IssueTask) -> Path:
        return self._base


@dataclass
class _Agent(CodingAgent):
    """Edits the source and/or writes a test, as told; counts its runs."""

    name: str = "fake/agent"
    lane: str = "fake"
    edit_source: bool = True
    write_test: str | None = "tests/test_issue_regression.py"
    fail: bool = False
    runs: list[str] = field(default_factory=list)

    def command(self, prompt: str, workdir: Path, logdir: Path) -> tuple[list[str], dict[str, str]]:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        return {"harness": "fake"}

    def run(self, prompt: str, workdir: Path, logdir: Path, timeout: int) -> RunRecord:
        self.runs.append(prompt)
        if self.fail:
            return RunRecord(self.name, 1, False, 0.1, error="exit code 1")
        if self.edit_source:
            (workdir / "src" / "toy" / "core.py").write_text("x = 2\n")
        if self.write_test:
            (workdir / self.write_test).write_text("def test_it():\n    assert True\n")
        return RunRecord(self.name, 0, False, 0.1, summary="done")


def test_an_attempt_is_measured_from_disk_and_journaled(tmp_path: Path) -> None:
    agent = _Agent()
    layout = _Layout(tmp_path)
    attempt = Race(layout, "dev", Fix()).attempt(_task("t1"), agent)
    assert attempt.status == "ok"
    assert attempt.source_files == ("src/toy/core.py",)
    assert attempt.test_files == ("tests/test_issue_regression.py",)
    assert "toy bug" in agent.runs[0] and "test_issue_regression.py" in agent.runs[0]
    # Journaled: a second race over the same suite does not run the agent again.
    assert Race(layout, "dev", Fix()).attempt(_task("t1"), agent) == attempt
    assert len(agent.runs) == 1


def test_only_failures_that_produced_nothing_are_retried(tmp_path: Path) -> None:
    layout = _Layout(tmp_path)
    failing = _Agent(fail=True)
    Race(layout, "dev", Fix()).attempt(_task("t1"), failing)
    failing.fail = False
    Race(layout, "dev", Fix()).attempt(_task("t1"), failing)
    assert len(failing.runs) == 1  # not retried unless asked
    retried = Race(layout, "dev", Fix(), retry_failed=True).attempt(_task("t1"), failing)
    assert len(failing.runs) == 2 and retried.status == "ok"


def test_one_crashing_attempt_does_not_end_the_race(tmp_path: Path) -> None:
    class Crashing(_Agent):
        def run(self, prompt: str, workdir: Path, logdir: Path, timeout: int) -> RunRecord:
            raise RuntimeError("boom")

    attempts = Race(_Layout(tmp_path), "dev", Fix()).run(
        [_task("t1"), _task("t2")], [Crashing(name="fake/crash"), _Agent()]
    )
    statuses = sorted((a.agent, a.status) for a in attempts)
    assert statuses == [
        ("fake/agent", "ok"),
        ("fake/agent", "ok"),
        ("fake/crash", "crashed"),
        ("fake/crash", "crashed"),
    ]


def test_an_examiners_source_edits_are_discarded_and_noted(tmp_path: Path) -> None:
    layout = _Layout(tmp_path)
    agent = _Agent(edit_source=True, write_test=f"tests/{REPRODUCTION_TEST}")
    room = ExamRoom(layout, "dev", source_context=lambda task, budget: "")
    [exhibit] = AgentExaminer(agent).examine([_task("t1")], room)
    assert exhibit.kind == "agentic"
    assert exhibit.author == "examiner/fake/agent"
    assert "def test_it" in exhibit.test_code
    assert "edits discarded" in exhibit.note
