"""Ground truth comes from the maintainers' fix, and decides every score."""

from __future__ import annotations

from pathlib import Path

import pytest

from bugbout.tasks.mining import Miner, MiningRules
from bugbout.tasks.oracle import Oracle, Referee, Resolution
from bugbout.tasks.task import Task
from bugbout.workspace.patch import Patch
from tests.toy import BUGGY, FIXED, ISSUE, StaticIssues, Toy, patch_to


@pytest.fixture
def task(toy: Toy) -> Task:
    rules = MiningRules(source_root="src/toy", test_root="tests")
    [task], _ = Miner(toy.repository, StaticIssues(ISSUE), rules).mine()
    return task


@pytest.fixture
def referee(toy: Toy, tmp_path: Path) -> Referee:
    return Referee(toy.workspace(tmp_path / "workspace"))


def test_the_fix_defines_fail_to_pass_and_pass_to_pass(task: Task, referee: Referee) -> None:
    oracle = referee.oracle(task)
    assert oracle.fail_to_pass == ("tests.test_core::test_add",)
    assert oracle.pass_to_pass == ("tests.test_core::test_mul",)
    assert oracle.unstable == () and oracle.valid


class TestScoring:
    def test_a_correct_patch_resolves(self, task: Task, referee: Referee, tmp_path: Path) -> None:
        result = referee.score(task, referee.oracle(task), patch_to(tmp_path, FIXED), "agent")
        assert result.resolved and result.fixed_bug and not result.broke_something

    def test_a_patch_that_breaks_a_passing_test_does_not(
        self, task: Task, referee: Referee, tmp_path: Path
    ) -> None:
        broken = FIXED.replace("a * b", "a ** b")
        result = referee.score(task, referee.oracle(task), patch_to(tmp_path, broken), "agent")
        assert result.fixed_bug and result.broke_something and not result.resolved
        assert result.pass_to_pass_failed == ("tests.test_core::test_mul",)

    def test_a_wrong_patch_does_not(self, task: Task, referee: Referee, tmp_path: Path) -> None:
        wrong = BUGGY.replace("a - b", "a * b")
        result = referee.score(task, referee.oracle(task), patch_to(tmp_path, wrong), "agent")
        assert not result.resolved and not result.fixed_bug

    def test_an_empty_patch_is_unresolved(self, task: Task, referee: Referee) -> None:
        result = referee.score(task, referee.oracle(task), Patch(""), "agent")
        assert not result.resolved and result.detail == "no fix produced"

    def test_a_patch_that_only_adds_tests_is_no_fix(
        self, task: Task, referee: Referee, tmp_path: Path
    ) -> None:
        (tmp_path / "before").mkdir()
        (tmp_path / "after" / "tests").mkdir(parents=True)
        (tmp_path / "after" / "tests" / "test_new.py").write_text("def test_x():\n    pass\n")
        patch = Patch.between(tmp_path / "before", tmp_path / "after")
        assert referee.score(task, referee.oracle(task), patch, "agent").detail == "no fix produced"

    def test_a_patch_that_does_not_apply_is_unresolved(self, task: Task, referee: Referee) -> None:
        stale = Patch("--- a/src/toy/core.py\n+++ b/src/toy/core.py\n@@ -1 +1 @@\n-nope\n+yes\n")
        result = referee.score(task, referee.oracle(task), stale, "agent")
        assert not result.applied and result.detail.startswith("did not apply")


def test_oracles_and_resolutions_round_trip() -> None:
    oracle = Oracle("t", ("a",), ("b",), ("c",), "note")
    assert Oracle.from_dict(oracle.to_dict()) == oracle
    resolution = Resolution.unresolved("t", "agent", oracle, "no fix produced")
    assert Resolution.from_dict(resolution.to_dict()) == resolution
