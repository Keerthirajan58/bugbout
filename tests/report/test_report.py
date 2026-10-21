"""Reports are computed from stored results alone, and read the same in every format."""

from __future__ import annotations

import dataclasses
import json

from rich.console import Console

from bugbout.agents.base import AgentRun
from bugbout.arena.judge import Standing, Verdict
from bugbout.arena.matrix import BASE, Matrix
from bugbout.arena.race import Attempt
from bugbout.report import Results, build_report, render_markdown, render_terminal
from bugbout.tasks.oracle import Oracle, Resolution
from bugbout.tasks.task import Task
from bugbout.workspace.patch import Patch

ORACLE = Oracle("t", ("tests.test_core::test_add",), ())


def _task(task_id: str) -> Task:
    return Task(
        task_id, "b", "f", 7, "issue", "add | subtracts", "...", ("a.py",), ("t.py",), ("x",)
    )


def _attempt(task_id: str, agent: str) -> Attempt:
    run = AgentRun(agent, 0, False, 90.0)
    return Attempt(task_id, agent, "fix", "ok", run, Patch("--- a/a.py\n+++ b/a.py\n"), "acme")


def _resolution(task_id: str, agent: str, resolved: bool) -> Resolution:
    return Resolution(task_id, agent, True, resolved, int(resolved), 1, (), 0)


def _standing(entrant: str, approved: bool) -> Standing:
    return Standing(entrant, 1.0 if approved else 0.0, ("x",), 1, 1, (), 0, 3, approved=approved)


def _results() -> Results:
    """Two tasks: on t1 only `a` is right and the judge picks it; t2 nobody is right."""
    repro = "tests.test_bugbout__x::test_bug"
    matrix = Matrix(
        "t1",
        [BASE, "a", "b"],
        {"x": "test_bugbout__x"},
        kinds={"x": "examiner"},
        outcomes={BASE: {repro: "failed"}, "a": {repro: "passed"}, "b": {repro: "failed"}},
    )
    return Results(
        project="toy/toy",
        policy="majority",
        mining={"examined": 9, "fix_shaped": 4, "rejected": {"no test shipped with the fix": 2}},
        tasks_mined=3,
        tasks=[_task("t1"), _task("t2")],
        attempts=[_attempt(t, agent) for t in ("t1", "t2") for agent in ("a", "b")],
        resolutions=[
            _resolution("t1", "a", True),
            _resolution("t1", "b", False),
            _resolution("t2", "a", False),
            _resolution("t2", "b", False),
        ],
        verdicts=[
            Verdict(
                "t1", "majority", "a", "supported", (_standing("a", True), _standing("b", False))
            ),
            Verdict("t2", "majority", None, "nothing reaches 50%"),
        ],
        matrices={"t1": matrix},
        agents={"a": {"vendor": "acme"}, "b": {"vendor": "other"}},
    )


def test_the_numbers_are_computed_from_the_results() -> None:
    report = build_report(_results())
    a, b = report.agents
    assert (a.name, a.resolved, a.tasks, a.median_seconds) == ("a", 1, 2, 90.0)
    assert (b.resolved, b.wrong) == (0, 2)
    selection = report.selection
    assert (selection.best_agent, selection.best_agent_resolved) == ("a", 1)
    assert (selection.judge_resolved, selection.judge_abstained, selection.abstained_correctly) == (
        1,
        1,
        1,
    )
    assert selection.random_pick == 0.5 and selection.wrong_picks == 0
    assert (report.gate.approved_correct, report.gate.rejected_wrong, report.gate.precision) == (
        1,
        1,
        1.0,
    )
    [examiner, suite] = report.evidence
    assert (examiner.source, examiner.auc) == ("x", 1.0)
    assert suite.kind == "suite"


def test_markdown_carries_every_section() -> None:
    text = render_markdown(build_report(_results()))
    assert text.startswith("# bugbout: toy/toy")
    assert "| a | 1/2 (50%) |" in text
    assert "Best single agent: a, 1/2 (50%)" in text
    assert "| Approved | 1 | 0 |" in text
    assert "add \\| subtracts" in text  # a pipe in a title cannot break the table
    assert "- no test shipped with the fix: 2" in text


def test_the_json_form_is_plain_data() -> None:
    data = json.loads(json.dumps(build_report(_results()).to_dict()))
    assert data["agents"][0]["resolve_rate"] == 0.5
    assert data["gate"]["precision"] == 1.0 and data["selection"]["wrong_picks"] == 0


def test_the_terminal_shows_the_headline() -> None:
    console = Console(record=True, width=120)
    render_terminal(build_report(_results()), console)
    text = console.export_text()
    assert "toy/toy" in text and "1/2 (50%)" in text and "Judge (majority)" in text


def test_an_empty_bout_still_reports() -> None:
    results = _results()
    empty = Results(results.project, "majority", {}, 0, [], [], [], [], {}, {})
    report = build_report(empty)
    assert report.agents == () and "No contestant has run yet." in render_markdown(report)


def test_with_nothing_resolved_or_approved_the_text_says_so() -> None:
    results = _results()
    nobody = dataclasses.replace(
        results,
        resolutions=[dataclasses.replace(r, resolved=False) for r in results.resolutions],
        verdicts=[
            Verdict("t1", "majority", None, "nothing reaches 50%", (_standing("a", False),)),
        ],
    )
    text = render_markdown(build_report(nobody))
    assert "Best single agent: none resolved a bug" in text
    assert "Nothing was approved; 1 of 1 wrong patches were rejected." in text


def test_a_run_stopped_by_a_usage_limit_is_not_scored() -> None:
    results = _results()
    stopped = dataclasses.replace(_attempt("t2", "b"), status="quota", patch=Patch(""))
    attempts = [a for a in results.attempts if (a.task_id, a.agent) != ("t2", "b")]
    report = build_report(dataclasses.replace(results, attempts=[*attempts, stopped]))
    b = next(agent for agent in report.agents if agent.name == "b")
    assert (b.tasks, b.stopped_by_quota) == (1, 1)
    assert "- b: 1 run(s) hit a usage limit and are not scored." in render_markdown(report)
