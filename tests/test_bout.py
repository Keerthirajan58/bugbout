"""A whole bout on a real repository, with scripted agents: mined, raced, judged and scored."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from bugbout.agents.base import CodingAgent
from bugbout.agents.command import CommandAgent
from bugbout.arena.briefs import ASSIGNMENTS
from bugbout.bout import Bout
from bugbout.errors import AgentNotAvailableError, ArtifactError, ConfigError
from bugbout.project import Examiner, Project
from bugbout.sandbox import NoSandbox
from tests.toy import BUGGY, FIXED, ISSUE, REGRESSION, StaticIssues, Toy, script_agent

WRONG = BUGGY.replace("a - b", "a * b")
WRONG_TEST = "from toy.core import add\n\n\ndef test_add():\n    assert add(2, 3) == 6\n"


@pytest.fixture
def project(toy: Toy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    monkeypatch.setenv("BUGBOUT_HOME", str(tmp_path / "home"))
    (toy.repo.path / "bugbout.toml").write_text(
        '[project]\nsource = "src/toy"\ngithub = "toy/toy"\n'
        f"interpreter = {str(sys.executable)!r}\n"
        '[run]\nsandbox = "none"\ntimeout = 120\n'
    )
    return Project.load(toy.repo.path)


def _bout(project: Project, tally: Path, *contestants: CodingAgent) -> Bout:
    fixer = script_agent(
        "fixer",
        {"src/toy/core.py": FIXED, "tests/test_issue_regression.py": REGRESSION},
        tally=tally / "fixer",
    )
    wrong = script_agent(
        "wrong",
        {"src/toy/core.py": WRONG, "tests/test_issue_regression.py": WRONG_TEST},
        tally=tally / "wrong",
    )
    examiner = script_agent(
        "examiner", {"tests/test_issue_reproduction.py": REGRESSION}, tally=tally / "examiner"
    )
    return Bout(
        project,
        contestants=[fixer, wrong, *contestants],
        examiners=[Examiner(examiner, ASSIGNMENTS["reproduce"])],
        sandbox=NoSandbox(),
        issues=StaticIssues(ISSUE),
    )


def _runs(tally: Path) -> int:
    return len(tally.read_text().splitlines()) if tally.exists() else 0


def test_a_bout_finds_the_agent_that_fixed_the_bug(project: Project, tmp_path: Path) -> None:
    report = _bout(project, tmp_path).run()

    assert (report.tasks_mined, report.tasks_scorable) == (1, 1)
    results = {a.name: a for a in report.agents}
    assert (results["fixer"].resolved, results["wrong"].resolved) == (1, 0)
    assert results["wrong"].wrong == 1

    [task] = report.tasks
    assert task.resolved_by == ("fixer",) and task.chosen == "fixer" and task.chosen_resolved
    assert (report.selection.judge_resolved, report.selection.best_agent_resolved) == (1, 1)
    assert (report.gate.approved_correct, report.gate.rejected_wrong) == (1, 1)
    examiner = next(e for e in report.evidence if e.source == "reproduce/examiner")
    assert examiner.auc == 1.0


def test_a_second_run_reuses_every_result(project: Project, tmp_path: Path) -> None:
    first = _bout(project, tmp_path).run()
    second = _bout(project, tmp_path).run()
    assert [_runs(tmp_path / name) for name in ("fixer", "wrong", "examiner")] == [1, 1, 1]
    assert second.agents == first.agents and second.tasks == first.tasks


def test_an_agent_added_later_is_the_only_one_that_runs(project: Project, tmp_path: Path) -> None:
    _bout(project, tmp_path).run()
    late = script_agent("late", {"src/toy/core.py": FIXED}, tally=tmp_path / "late")
    report = _bout(project, tmp_path, late).run()
    assert [_runs(tmp_path / name) for name in ("fixer", "wrong", "late")] == [1, 1, 1]
    assert {a.name: a.resolved for a in report.agents}["late"] == 1


def test_the_results_live_beside_the_config(project: Project, tmp_path: Path) -> None:
    _bout(project, tmp_path).run()
    results = project.results_dir
    assert {p.name for p in results.iterdir()} >= {"tasks.json", "oracles.json", "verdicts.json"}
    assert (results / "attempts" / "fix").is_dir() and (results / "attempts" / "reproduce").is_dir()


def test_there_is_no_report_before_a_run(project: Project, tmp_path: Path) -> None:
    with pytest.raises(ArtifactError, match="no results yet"):
        _bout(project, tmp_path).report()


class TestCheck:
    def test_a_bout_needs_contestants(self, project: Project) -> None:
        with pytest.raises(ConfigError, match="no contestants"):
            Bout(project, contestants=[], examiners=[], sandbox=NoSandbox()).check()

    def test_a_missing_agent_is_found_before_anything_runs(self, project: Project) -> None:
        ghost = CommandAgent(["no-such-agent-binary"], name="ghost")
        with pytest.raises(AgentNotAvailableError, match="ghost"):
            Bout(project, contestants=[ghost], examiners=[], sandbox=NoSandbox()).check()
