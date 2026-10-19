"""Package a suite's artifacts into the one JSON file the dashboard reads.

The dashboard is static: it never runs a pipeline or calls a model, so what a
visitor sees is exactly what the runs recorded. The full matrix is thousands
of cells per task; the bundle carries the part a reader needs to follow the
judge - every exhibit test on every tree, and per candidate the repository
tests it alone broke - while ``matrices.json`` keeps the rest for checking.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from verdict.arena.analysis import gate_stats
from verdict.arena.examiners.exhibit import Exhibit
from verdict.arena.judge import Judge, Standing, Verdict
from verdict.arena.matrix import BASE, Matrix
from verdict.arena.race import Attempt
from verdict.arena.scoreboard import Scoreboard, TaskRow
from verdict.arena.truth import Oracle, Resolution
from verdict.arena.vendors import vendor_of
from verdict.review.tasks import IssueTask

STATEMENT_LIMIT = 2500


def build_bundle(
    *,
    suite: str,
    tasks: list[IssueTask],
    repos: dict[str, str],
    oracles: dict[str, Oracle],
    attempts: dict[str, dict[str, Attempt]],
    entrant_meta: dict[str, dict[str, Any]],
    exhibits: list[Exhibit],
    examiner_meta: dict[str, Any],
    matrices: dict[str, Matrix],
    resolutions: list[Resolution],
    judge: Judge,
    board: Scoreboard,
) -> dict[str, Any]:
    by_key = {(r.task_id, r.entrant): r for r in resolutions}
    code_of = {(e.task_id, e.author): e.test_code for e in exhibits}
    rows = {row.task_id: row for row in board.rows}
    verdicts = {tid: judge(matrix) for tid, matrix in matrices.items()}
    gate = gate_stats(
        tuple(verdicts[t.task_id] for t in tasks if t.task_id in verdicts), resolutions
    )
    return {
        "suite": suite,
        "policy": judge.policy.name,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "scoreboard": _scoreboard(board),
        "gate": {
            **asdict(gate),
            "precision": round(gate.precision, 3),
            "base_rate": round(gate.base_rate, 3),
            "catch_rate": round(gate.catch_rate, 3),
            "false_alarm_rate": round(gate.false_alarm_rate, 3),
        },
        "entrants": [
            {"name": n, "vendor": vendor_of(n), **m} for n, m in sorted(entrant_meta.items())
        ],
        "examiners": [
            {"name": n, "vendor": vendor_of(n), **m} for n, m in sorted(examiner_meta.items())
        ],
        "tasks": [
            _task(
                task,
                repos[task.target_slug],
                oracles.get(task.task_id),
                attempts.get(task.task_id, {}),
                matrices.get(task.task_id),
                verdicts.get(task.task_id),
                by_key,
                code_of,
                rows.get(task.task_id),
            )
            for task in tasks
        ],
    }


def _scoreboard(board: Scoreboard) -> dict[str, Any]:
    best_name, best_count = board.best_single
    return {
        "tasks": board.tasks,
        "per_entrant": board.per_entrant,
        "best_single": {"entrant": best_name, "resolved": best_count},
        "random_pick": board.random_pick,
        "verdict_resolved": board.verdict_resolved,
        "verdict_picks": board.verdict_picks,
        "wrong_picks": board.wrong_picks,
        "abstained": board.verdict_abstained,
        "abstained_correctly": board.abstained_correctly,
        "best_possible": board.best_possible,
    }


def _task(
    task: IssueTask,
    repo: str,
    oracle: Oracle | None,
    attempts: dict[str, Attempt],
    matrix: Matrix | None,
    verdict: Verdict | None,
    resolutions: dict[tuple[str, str], Resolution],
    code_of: dict[tuple[str, str], str],
    row: TaskRow | None,
) -> dict[str, Any]:
    standings = {s.entrant: s for s in verdict.standings} if verdict else {}
    return {
        "task_id": task.task_id,
        "repo": repo,
        "target": task.target_slug,
        "reference": task.reference,
        "url": f"https://github.com/{repo}/issues/{task.reference}",
        "title": task.title,
        "statement": task.problem_statement[:STATEMENT_LIMIT],
        "oracle": {
            "fail_to_pass": len(oracle.fail_to_pass) if oracle else 0,
            "pass_to_pass": len(oracle.pass_to_pass) if oracle else 0,
        },
        "candidates": [
            _candidate(name, attempt, resolutions.get((task.task_id, name)), standings.get(name))
            for name, attempt in sorted(attempts.items())
        ],
        "evidence": _evidence(
            task, matrix, code_of, set(verdict.valid_exhibits) if verdict else set()
        ),
        "verdict": {
            "chosen": verdict.chosen if verdict else None,
            "reason": verdict.reason if verdict else "not judged",
            "chosen_resolved": bool(row and row.chosen_resolved),
            "resolved_by": row.resolved_by if row else [],
        },
    }


def _candidate(
    name: str, attempt: Attempt, resolution: Resolution | None, standing: Standing | None
) -> dict[str, Any]:
    return {
        "entrant": name,
        "vendor": vendor_of(name),
        "diff": attempt.patch.diff,
        "status": attempt.status,
        "seconds": attempt.record.seconds,
        "summary": attempt.record.summary[:1500],
        "resolved": bool(resolution and resolution.resolved),
        "fixed_bug": bool(resolution and resolution.fixed_bug),
        "broke": list(resolution.pass_to_pass_failed[:10]) if resolution else [],
        "standing": None
        if standing is None
        else {
            "support": standing.support,
            "judges": list(standing.judges),
            "reproductions_passed": standing.reproductions_passed,
            "reproductions_total": standing.reproductions_total,
            "breaks": list(standing.breaks[:10]),
            "breaks_total": len(standing.breaks),
            "lines_changed": standing.lines_changed,
            "invariant_failures": standing.invariant_failures,
            "approved": standing.approved,
        },
    }


def _evidence(
    task: IssueTask,
    matrix: Matrix | None,
    code_of: dict[tuple[str, str], str],
    valid: set[str],
) -> list[dict[str, Any]]:
    if matrix is None:
        return []
    trees = [t for t in matrix.trees if t != BASE]
    evidence = []
    for author, module in sorted(matrix.exhibits.items()):
        tests = sorted(t for t in matrix.outcomes.get(BASE, {}) if module in t)
        evidence.append(
            {
                "author": author,
                "vendor": vendor_of(author),
                "kind": matrix.kinds.get(author, "prompted"),
                "code": code_of.get((task.task_id, author), ""),
                "reproduces": author in valid,
                "tests": [
                    {
                        "id": t.split("::", 1)[-1],
                        "base": matrix.outcomes[BASE].get(t, "absent"),
                        "trees": {
                            tree: matrix.outcomes.get(tree, {}).get(t, "absent") for tree in trees
                        },
                    }
                    for t in tests
                ],
            }
        )
    return evidence
