"""Agents race on tasks in isolated checkouts, and every finished attempt is journaled."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from bugbout.agents.base import CodingAgent, Invocation, Session
from bugbout.agents.command import CommandAgent
from bugbout.arena.briefs import ASSIGNMENTS, FIX
from bugbout.arena.race import Attempt, Race
from bugbout.sandbox import NoSandbox
from bugbout.tasks.task import Task
from tests.toy import FIXED, REGRESSION, Toy, script_agent

FIX_FILES = {"src/toy/core.py": FIXED, "tests/test_issue_regression.py": REGRESSION}


def _race(toy: Toy, tmp_path: Path, **options: bool) -> Race:
    workspace = toy.workspace(tmp_path / "workspace")
    return Race(
        workspace,
        FIX,
        tmp_path / "journal",
        sandbox=NoSandbox(),
        repo="toy/toy",
        retry_failed=options.get("retry_failed", False),
    )


def _runs(tally: Path) -> int:
    return len(tally.read_text().splitlines()) if tally.exists() else 0


def _python(name: str, code: str) -> CommandAgent:
    return CommandAgent([sys.executable, "-c", code], name=name, sandboxed=False)


class TestAnAttempt:
    def test_the_patch_is_measured_from_the_checkout(
        self, toy: Toy, toy_task: Task, tmp_path: Path
    ) -> None:
        [attempt] = _race(toy, tmp_path).run([toy_task], [script_agent("fixer", FIX_FILES)])
        assert (attempt.status, attempt.agent, attempt.assignment) == ("ok", "fixer", "fix")
        assert set(attempt.patch.files) == set(FIX_FILES)

    def test_an_agent_that_changes_nothing_produced_nothing(
        self, toy: Toy, toy_task: Task, tmp_path: Path
    ) -> None:
        [attempt] = _race(toy, tmp_path).run([toy_task], [script_agent("idle", {})])
        assert attempt.status == "ok" and not attempt.produced

    def test_a_failing_agent_that_hit_its_quota_says_so(
        self, toy: Toy, toy_task: Task, tmp_path: Path
    ) -> None:
        quota = "import sys; print('Error: usage limit reached', file=sys.stderr); sys.exit(1)"
        [attempt] = _race(toy, tmp_path).run([toy_task], [_python("limited", quota)])
        assert attempt.status == "quota" and attempt.retryable

    def test_the_prompt_carries_the_issue_the_runner_and_the_test_file(
        self, toy: Toy, toy_task: Task, tmp_path: Path
    ) -> None:
        echo = _python("echo", "import sys; print(sys.stdin.read())")
        _race(toy, tmp_path).run([toy_task], [echo])
        folder = tmp_path / "workspace" / "runs" / "fix" / toy_task.task_id / "echo"
        prompt = (folder / "logs" / "stdout.log").read_text()
        assert toy_task.title in prompt and toy_task.problem_statement in prompt
        assert f"{folder / 'bin' / 'run-tests'} tests/test_issue_regression.py" in prompt


class TestTheJournal:
    def test_a_finished_attempt_is_never_run_again(
        self, toy: Toy, toy_task: Task, tmp_path: Path
    ) -> None:
        tally = tmp_path / "tally"
        agent = script_agent("fixer", FIX_FILES, tally=tally)
        [first] = _race(toy, tmp_path).run([toy_task], [agent])
        [second] = _race(toy, tmp_path).run([toy_task], [agent])
        assert _runs(tally) == 1 and second == first

    @pytest.mark.parametrize(("retry_failed", "runs"), [(False, 1), (True, 2)])
    def test_a_failure_that_left_nothing_is_retried_only_on_request(
        self, toy: Toy, toy_task: Task, tmp_path: Path, retry_failed: bool, runs: int
    ) -> None:
        tally = tmp_path / "tally"
        agent = script_agent("flaky", {}, exit_code=1, tally=tally)
        _race(toy, tmp_path).run([toy_task], [agent])
        _race(toy, tmp_path, retry_failed=retry_failed).run([toy_task], [agent])
        assert _runs(tally) == runs

    def test_an_attempt_round_trips(self, toy: Toy, toy_task: Task, tmp_path: Path) -> None:
        [attempt] = _race(toy, tmp_path).run([toy_task], [script_agent("fixer", FIX_FILES)])
        assert Attempt.from_dict(attempt.to_dict()) == attempt


class Broken(CodingAgent):
    harness = "broken"
    executable = "broken"

    def invocation(self, session: Session) -> Invocation:
        raise RuntimeError("adapter bug")


def test_a_crash_is_reported_but_not_journaled(toy: Toy, toy_task: Task, tmp_path: Path) -> None:
    race = _race(toy, tmp_path)
    [crashed, fixed] = race.run([toy_task], [Broken("m"), script_agent("fixer", FIX_FILES)])
    assert crashed.status == "crashed" and "adapter bug" in crashed.run.error
    assert fixed.status == "ok"
    assert race.journaled(toy_task, Broken("m")) is None


def test_every_agent_attempts_every_task_across_lanes(
    toy: Toy, toy_task: Task, tmp_path: Path
) -> None:
    agents = [
        script_agent("a", FIX_FILES, quota="one"),
        script_agent("b", {}, quota="one"),
        script_agent("c", FIX_FILES, quota="two"),
    ]
    attempts = _race(toy, tmp_path).run([toy_task], agents)
    assert [a.agent for a in attempts] == ["a", "b", "c"]


def test_every_assignment_names_its_test_file() -> None:
    for assignment in ASSIGNMENTS.values():
        assert assignment.test_file.startswith("test_issue_")
