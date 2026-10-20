"""Cross-examination on a real checkout: every exhibit against every fix, by execution."""

from __future__ import annotations

from pathlib import Path

import pytest

from bugbout.agents.base import AgentRun
from bugbout.arena.exhibits import Exhibit, Kind, exhibit_of
from bugbout.arena.judge import Judge
from bugbout.arena.matrix import BASE, CrossExaminer, Matrix
from bugbout.arena.race import Attempt
from bugbout.tasks.task import Task
from bugbout.workspace.patch import Patch
from tests.toy import BUGGY, FIXED, REGRESSION, VACUOUS, Toy, patch_to

WRONG = BUGGY.replace("a - b", "a * b")


def _exhibit(author: str, code: str, kind: Kind = "examiner") -> Exhibit:
    return Exhibit("t", author, "google" if kind == "examiner" else "openai", kind, code)


@pytest.fixture
def matrix(toy: Toy, toy_task: Task, tmp_path: Path) -> Matrix:
    fixes = {
        "right": patch_to(tmp_path / "right", FIXED),
        "wrong": patch_to(tmp_path / "wrong", WRONG),
    }
    exhibits = [_exhibit("reproduce/x", REGRESSION), _exhibit("specify/y", VACUOUS)]
    examiner = CrossExaminer(toy.workspace(tmp_path / "workspace"))
    return examiner.build(toy_task, fixes, exhibits, {"right": "acme", "wrong": "acme"})


def test_every_tree_runs_every_exhibit_and_the_suite(matrix: Matrix) -> None:
    repro = "tests.test_bugbout__reproduce_x::test_add"
    assert matrix.trees == [BASE, "right", "wrong"]
    assert [matrix.outcomes[t][repro] for t in matrix.trees] == ["failed", "passed", "failed"]
    assert all(matrix.passes(t, "tests.test_core::test_mul") for t in matrix.trees)
    assert matrix.author_of(repro) == "reproduce/x"


def test_the_judge_picks_the_fix_the_reproduction_supports(matrix: Matrix) -> None:
    verdict = Judge()(matrix)
    assert verdict.chosen == "right"
    assert verdict.invalid_exhibits == ("specify/y",)  # it passes on the base: no reproduction


def test_a_fix_that_does_not_apply_is_no_candidate(
    toy: Toy, toy_task: Task, tmp_path: Path
) -> None:
    stale = Patch("--- a/src/toy/core.py\n+++ b/src/toy/core.py\n@@ -1 +1 @@\n-nope\n+yes\n")
    matrix = CrossExaminer(toy.workspace(tmp_path / "ws")).build(
        toy_task, {"stale": stale}, [_exhibit("reproduce/x", REGRESSION)], {}
    )
    assert matrix.candidates == [] and matrix.notes["stale"].startswith("fix did not apply")


def test_without_the_suite_only_exhibits_run(toy: Toy, toy_task: Task, tmp_path: Path) -> None:
    examiner = CrossExaminer(toy.workspace(tmp_path / "ws"), run_suite=False)
    matrix = examiner.build(toy_task, {}, [_exhibit("reproduce/x", REGRESSION)], {})
    assert set(matrix.outcomes[BASE]) == {"tests.test_bugbout__reproduce_x::test_add"}


class TestExhibits:
    def _attempt(self, assignment: str, files: dict[str, str], tmp_path: Path) -> Attempt:
        (tmp_path / "before").mkdir()
        for relative, text in files.items():
            (tmp_path / "after" / relative).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / "after" / relative).write_text(text)
        patch = Patch.between(tmp_path / "before", tmp_path / "after")
        run = AgentRun("codex/gpt-5", 0, False, 1.0)
        return Attempt("t", "codex/gpt-5", assignment, "ok", run, patch, "openai")

    def test_a_contestant_s_regression_test_is_its_exhibit(self, tmp_path: Path) -> None:
        files = {"src/toy/core.py": FIXED, "tests/test_issue_regression.py": REGRESSION}
        exhibit = exhibit_of(self._attempt("fix", files, tmp_path), "tests")
        assert (exhibit.author, exhibit.kind, exhibit.vendor) == (
            "codex/gpt-5",
            "contestant",
            "openai",
        )
        assert exhibit.test_code == REGRESSION and exhibit.note == ""

    def test_an_examiner_that_edits_the_source_is_noted(self, tmp_path: Path) -> None:
        files = {"src/toy/core.py": FIXED, "tests/test_other.py": REGRESSION}
        exhibit = exhibit_of(self._attempt("reproduce", files, tmp_path), "tests")
        assert exhibit.author == "reproduce/codex/gpt-5" and exhibit.kind == "examiner"
        assert "instead of tests/test_issue_reproduction.py" in exhibit.note
        assert "edited the source" in exhibit.note

    def test_no_test_means_an_unusable_exhibit(self, tmp_path: Path) -> None:
        exhibit = exhibit_of(self._attempt("specify", {"notes.py": "x = 1\n"}, tmp_path), "tests")
        assert not exhibit.usable and exhibit.error == "no test written (ok)"

    def test_an_exhibit_round_trips(self) -> None:
        exhibit = _exhibit("reproduce/x", REGRESSION)
        assert Exhibit.from_dict(exhibit.to_dict()) == exhibit
