"""Package the arena's artifacts into the one JSON file the dashboard reads.

The dashboard is static: it never runs a pipeline or calls a model, so what a
visitor sees is exactly what the runs recorded. Everything is derived from the
committed artifacts; nothing is summarised by hand.

The full test matrix is thousands of cells per task. The dashboard shows the
part a reader needs to follow the judge's reasoning - every exhibit test on
every tree, and per candidate the repository tests it alone broke - and the
complete matrix stays in ``matrices.json`` for anyone who wants to check.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from verdict.arena.judge import Verdict, judge
from verdict.arena.matrix import BASE, Matrix, family_of
from verdict.arena.scoreboard import Scoreboard
from verdict.arena.truth import Oracle, Resolution
from verdict.review.tasks import IssueTask

STATEMENT_LIMIT = 2500


def build_bundle(
    tasks: list[IssueTask],
    repos: dict[str, str],
    oracles: dict[str, Oracle],
    entries: dict[str, dict[str, dict[str, Any]]],
    entrant_meta: dict[str, dict[str, Any]],
    exhibits: list[dict[str, Any]],
    examiner_meta: dict[str, Any],
    matrices: dict[str, Matrix],
    resolutions: list[Resolution],
    board: Scoreboard,
) -> dict[str, Any]:
    by_key = {(r.task_id, r.entrant): r for r in resolutions}
    blind_authors = {e["author"] for e in exhibits}
    rows = {row.task_id: row for row in board.rows}

    out_tasks = []
    for task in tasks:
        matrix = matrices.get(task.task_id)
        verdict: Verdict | None = judge(matrix) if matrix else None
        standings = {s.entrant: s for s in verdict.standings} if verdict else {}
        candidates = []
        for entrant, entry in sorted(entries.get(task.task_id, {}).items()):
            resolution = by_key.get((task.task_id, entrant))
            standing = standings.get(entrant)
            candidates.append(
                {
                    "entrant": entrant,
                    "family": family_of(entrant),
                    "diff": entry.get("patch", {}).get("diff", ""),
                    "status": entry.get("status", ""),
                    "seconds": entry.get("record", {}).get("seconds"),
                    "summary": entry.get("record", {}).get("summary", "")[:1500],
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
                    },
                }
            )

        evidence = []
        if matrix:
            trees = [t for t in matrix.trees if t != BASE]
            for author, module in sorted(matrix.exhibits.items()):
                code = next(
                    (
                        e["test_code"]
                        for e in exhibits
                        if e["task_id"] == task.task_id and e["author"] == author
                    ),
                    "",
                )
                tests = sorted(t for t in matrix.outcomes.get(BASE, {}) if module in t)
                evidence.append(
                    {
                        "author": author,
                        "family": family_of(author),
                        "kind": "blind" if author in blind_authors else "self",
                        "code": code,
                        "reproduces": verdict is not None and author in verdict.valid_exhibits,
                        "tests": [
                            {
                                "id": t.split("::", 1)[-1],
                                "base": matrix.outcomes[BASE].get(t, "absent"),
                                "trees": {
                                    tree: matrix.outcomes.get(tree, {}).get(t, "absent")
                                    for tree in trees
                                },
                            }
                            for t in tests
                        ],
                    }
                )

        row = rows.get(task.task_id)
        oracle = oracles.get(task.task_id)
        out_tasks.append(
            {
                "task_id": task.task_id,
                "repo": repos[task.target_slug],
                "target": task.target_slug,
                "reference": task.reference,
                "reference_kind": task.reference_kind,
                "url": f"https://github.com/{repos[task.target_slug]}/issues/{task.reference}",
                "title": task.title,
                "statement": task.problem_statement[:STATEMENT_LIMIT],
                "oracle": {
                    "fail_to_pass": len(oracle.fail_to_pass) if oracle else 0,
                    "pass_to_pass": len(oracle.pass_to_pass) if oracle else 0,
                },
                "candidates": candidates,
                "evidence": evidence,
                "verdict": {
                    "chosen": verdict.chosen if verdict else None,
                    "reason": verdict.reason if verdict else "not judged",
                    "chosen_resolved": bool(row and row.chosen_resolved),
                    "resolved_by": row.resolved_by if row else [],
                },
            }
        )

    best_name, best_count = board.best_single
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "scoreboard": {
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
        },
        "entrants": [
            {"name": name, "family": family_of(name), **meta}
            for name, meta in sorted(entrant_meta.items())
        ],
        "examiners": [{"name": name, **meta} for name, meta in sorted(examiner_meta.items())],
        "tasks": out_tasks,
    }
