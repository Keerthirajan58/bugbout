"""Turn the arena's artifacts into the numbers the write-up reports.

A pure function of what the runs recorded. The baselines are the questions a
skeptical reader would ask first:

* **Each agent alone** - how often does one agent's patch resolve the issue?
* **Random pick** - given every agent's patch, choose one blindly. This is the
  expected value, computed exactly rather than sampled.
* **Best possible** - some agent resolved it. No selector can beat this.
* **Verdict** - the judge's pick, scored by the hidden oracle it never saw.
  An abstention is counted as unresolved: refusing to choose is safe, but it
  does not ship a fix.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from verdict.arena.truth import Resolution


@dataclass
class TaskRow:
    task_id: str
    candidates: list[str]
    resolved_by: list[str]
    chosen: str | None
    chosen_resolved: bool
    reason: str


@dataclass
class Scoreboard:
    tasks: int
    per_entrant: dict[str, dict[str, int]]
    best_possible: int
    random_pick: float
    verdict_resolved: int
    verdict_picks: int
    verdict_abstained: int
    abstained_correctly: int
    """Abstentions on tasks where no candidate was correct - the right call."""

    wrong_picks: int
    rows: list[TaskRow] = field(default_factory=list)

    @property
    def best_single(self) -> tuple[str, int]:
        name, stats = max(self.per_entrant.items(), key=lambda kv: kv[1]["resolved"])
        return name, stats["resolved"]

    @property
    def precision(self) -> float:
        return self.verdict_resolved / self.verdict_picks if self.verdict_picks else 0.0


def build(
    task_ids: list[str],
    resolutions: list[Resolution],
    verdicts: list[dict[str, Any]],
    candidates_by_task: dict[str, list[str]],
) -> Scoreboard:
    by_key = {(r.task_id, r.entrant): r for r in resolutions}
    entrants = sorted({r.entrant for r in resolutions})
    per_entrant = {
        e: {
            "resolved": sum(1 for t in task_ids if (r := by_key.get((t, e))) and r.resolved),
            "fixed_bug": sum(1 for t in task_ids if (r := by_key.get((t, e))) and r.fixed_bug),
            "broke_tests": sum(
                1 for t in task_ids if (r := by_key.get((t, e))) and r.broke_something
            ),
        }
        for e in entrants
    }
    chosen_by_task = {v["task_id"]: v for v in verdicts}

    rows: list[TaskRow] = []
    best = resolved = picks = abstained = right_abstain = wrong = 0
    expected = 0.0
    for task_id in task_ids:
        candidates = candidates_by_task.get(task_id, [])
        good = [c for c in candidates if (r := by_key.get((task_id, c))) and r.resolved]
        best += bool(good)
        expected += len(good) / len(candidates) if candidates else 0.0
        verdict = chosen_by_task.get(task_id, {})
        chosen = verdict.get("chosen")
        ok = bool(chosen and chosen in good)
        if chosen:
            picks += 1
            resolved += ok
            wrong += not ok
        else:
            abstained += 1
            right_abstain += not good
        rows.append(TaskRow(task_id, candidates, good, chosen, ok, verdict.get("reason", "")))

    return Scoreboard(
        tasks=len(task_ids),
        per_entrant=per_entrant,
        best_possible=best,
        random_pick=round(expected, 2),
        verdict_resolved=resolved,
        verdict_picks=picks,
        verdict_abstained=abstained,
        abstained_correctly=right_abstain,
        wrong_picks=wrong,
        rows=rows,
    )
