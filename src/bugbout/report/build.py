"""Turn a bout's stored results into a :class:`Report`: a pure function of what was recorded."""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from bugbout.__about__ import __version__
from bugbout.arena.judge import Verdict
from bugbout.arena.matrix import BASE, Matrix
from bugbout.arena.race import Attempt
from bugbout.report.model import (
    AgentResult,
    EvidenceSource,
    Gate,
    Report,
    Selection,
    TaskResult,
)
from bugbout.tasks.oracle import Resolution
from bugbout.tasks.task import Task

SUITE = "the repository's own tests"


@dataclass(frozen=True, slots=True)
class Results:
    """Everything a report is computed from."""

    project: str
    policy: str
    mining: dict[str, Any]
    tasks_mined: int
    tasks: Sequence[Task]
    """The scorable tasks: those whose ground truth proves something."""

    attempts: Sequence[Attempt]
    """The contestants' attempts."""

    resolutions: Sequence[Resolution]
    verdicts: Sequence[Verdict]
    matrices: dict[str, Matrix]
    agents: dict[str, dict[str, str]]
    """Each contestant's description, by name."""


def build_report(results: Results) -> Report:
    resolved = {(r.task_id, r.entrant): r for r in results.resolutions}
    return Report(
        project=results.project,
        generated=datetime.now(UTC).isoformat(timespec="seconds"),
        version=__version__,
        policy=results.policy,
        mining=results.mining,
        tasks_mined=results.tasks_mined,
        tasks_scorable=len(results.tasks),
        agents=tuple(_agents(results, resolved)),
        tasks=tuple(_tasks(results, resolved)),
        selection=_selection(results, resolved),
        gate=_gate(results.verdicts, resolved),
        evidence=tuple(_evidence(results.matrices, resolved)),
    )


def _agents(results: Results, resolved: dict[tuple[str, str], Resolution]) -> list[AgentResult]:
    rows = []
    for name, description in results.agents.items():
        attempts = [a for a in results.attempts if a.agent == name]
        outcomes = [resolved.get((a.task_id, name)) for a in attempts]
        seconds = [a.run.seconds for a in attempts if a.status != "crashed"]
        rows.append(
            AgentResult(
                name=name,
                vendor=description.get("vendor", ""),
                description=description,
                tasks=len(attempts),
                resolved=sum(1 for r in outcomes if r and r.resolved),
                fixed_but_broke=sum(1 for r in outcomes if r and r.fixed_bug and r.broke_something),
                wrong=sum(1 for r in outcomes if r and r.applied and not r.fixed_bug),
                no_fix=sum(1 for r in outcomes if r is None or not r.applied),
                failed_runs=sum(1 for a in attempts if a.status != "ok"),
                median_seconds=round(statistics.median(seconds), 1) if seconds else None,
            )
        )
    return sorted(rows, key=lambda row: (-row.resolved, row.name))


def _tasks(results: Results, resolved: dict[tuple[str, str], Resolution]) -> list[TaskResult]:
    verdicts = {v.task_id: v for v in results.verdicts}
    rows = []
    for task in results.tasks:
        attempted = sorted(a.agent for a in results.attempts if a.task_id == task.task_id)
        good = tuple(a for a in attempted if (r := resolved.get((task.task_id, a))) and r.resolved)
        verdict = verdicts.get(task.task_id)
        chosen = verdict.chosen if verdict else None
        rows.append(
            TaskResult(
                task_id=task.task_id,
                reference=task.reference,
                reference_kind=task.reference_kind,
                title=task.title,
                attempted_by=tuple(attempted),
                resolved_by=good,
                chosen=chosen,
                chosen_resolved=chosen in good,
                verdict=verdict.reason if verdict else "not judged",
            )
        )
    return rows


def _selection(results: Results, resolved: dict[tuple[str, str], Resolution]) -> Selection:
    verdicts = {v.task_id: v for v in results.verdicts}
    by_agent: dict[str, int] = dict.fromkeys(results.agents, 0)
    any_agent = picks = judged_right = abstained = abstained_right = 0
    expected = 0.0
    for task in results.tasks:
        matrix = results.matrices.get(task.task_id)
        candidates = matrix.candidates if matrix else []
        good = {
            a.agent
            for a in results.attempts
            if a.task_id == task.task_id
            and (r := resolved.get((task.task_id, a.agent)))
            and r.resolved
        }
        for agent in good & set(by_agent):
            by_agent[agent] += 1
        any_agent += bool(good)
        expected += len(good & set(candidates)) / len(candidates) if candidates else 0.0
        chosen = verdicts[task.task_id].chosen if task.task_id in verdicts else None
        if chosen is None:
            abstained += 1
            abstained_right += not good
        else:
            picks += 1
            judged_right += chosen in good
    best = max(by_agent.items(), key=lambda item: item[1], default=(None, 0))
    if best[1] == 0:
        best = (None, 0)  # nobody resolved anything: there is no best agent to name
    return Selection(
        tasks=len(results.tasks),
        best_agent=best[0],
        best_agent_resolved=best[1],
        any_agent_resolved=any_agent,
        random_pick=round(expected, 2),
        judge_resolved=judged_right,
        judge_picks=picks,
        judge_abstained=abstained,
        abstained_correctly=abstained_right,
    )


def _gate(verdicts: Sequence[Verdict], resolved: dict[tuple[str, str], Resolution]) -> Gate:
    counts = dict.fromkeys(
        (
            f"{gate}_{truth}"
            for gate in ("approved", "rejected", "unverified")
            for truth in ("correct", "wrong")
        ),
        0,
    )
    for verdict in verdicts:
        for standing in verdict.standings:
            outcome = resolved.get((verdict.task_id, standing.entrant))
            truth = "correct" if outcome and outcome.resolved else "wrong"
            counts[f"{standing.gate}_{truth}"] += 1
    return Gate(**counts)


def _evidence(
    matrices: dict[str, Matrix], resolved: dict[tuple[str, str], Resolution]
) -> list[EvidenceSource]:
    """The raw signal every policy is built on, source by source.

    A patch's score from an author is the share of that author's reproductions
    (tests failing on the base) it passes; nobody is scored on their own patch.
    From the repository's own tests, the score is how few base-passing tests the
    patch breaks.
    """
    scores: dict[str, list[tuple[float, bool]]] = {}
    kinds: dict[str, str] = {SUITE: "suite"}
    for task_id, matrix in matrices.items():
        base = matrix.outcomes.get(BASE, {})
        reproductions: dict[str, list[str]] = {}
        suite = []
        for test, outcome in base.items():
            author = matrix.author_of(test)
            if author is None and outcome == "passed":
                suite.append(test)
            elif author is not None and outcome == "failed":
                reproductions.setdefault(author, []).append(test)
        for candidate in matrix.candidates:
            correct = bool((r := resolved.get((task_id, candidate))) and r.resolved)
            for author, tests in reproductions.items():
                if author != candidate:
                    kinds[author] = matrix.kinds.get(author, "examiner")
                    share = sum(matrix.passes(candidate, t) for t in tests) / len(tests)
                    scores.setdefault(author, []).append((share, correct))
            broken = sum(not matrix.passes(candidate, t) for t in suite)
            scores.setdefault(SUITE, []).append((-broken, correct))
    sources = [_source(name, kinds[name], pairs) for name, pairs in scores.items()]
    return sorted(sources, key=lambda s: (s.auc is None, -(s.auc or 0.0), s.source))


def _source(name: str, kind: str, pairs: list[tuple[float, bool]]) -> EvidenceSource:
    good = [score for score, ok in pairs if ok]
    bad = [score for score, ok in pairs if not ok]
    if not good or not bad:
        return EvidenceSource(name, kind, None, len(good), len(bad))
    wins = sum((g > b) + 0.5 * (g == b) for g in good for b in bad)
    return EvidenceSource(name, kind, round(wins / (len(good) * len(bad)), 3), len(good), len(bad))
