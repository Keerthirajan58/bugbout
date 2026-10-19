"""Measure a judge policy on a suite, in the two ways the arena is used.

**Selection** - several agents patched the same issue; which patch ships? Scored
per task against the hidden oracle (see :mod:`verdict.arena.scoreboard`).

**Gating** - one agent says it is done; may its patch merge? Scored per patch:
the judge *approves* a patch the independent evidence fully supports, *rejects*
one that fails a reproduction or breaks a test the others keep, and leaves it
*unverified* when no independent evidence exists. A gate is worth exactly as
much as its precision on what it approves and its catch rate on what is wrong.

Policies are compared on the development suite and one is frozen before the
held-out suite is scored. Nothing here changes a rule; it only measures them.
"""

from __future__ import annotations

from dataclasses import dataclass

from verdict.arena import scoreboard
from verdict.arena.judge import Judge, JudgePolicy, Verdict
from verdict.arena.matrix import Matrix
from verdict.arena.truth import Resolution


@dataclass(frozen=True, slots=True)
class GateStats:
    approved_correct: int
    approved_wrong: int
    rejected_correct: int
    rejected_wrong: int
    unverified_correct: int
    unverified_wrong: int

    @property
    def correct(self) -> int:
        return self.approved_correct + self.rejected_correct + self.unverified_correct

    @property
    def wrong(self) -> int:
        return self.approved_wrong + self.rejected_wrong + self.unverified_wrong

    @property
    def precision(self) -> float:
        """Of the patches the gate let through, the share that were correct."""
        approved = self.approved_correct + self.approved_wrong
        return self.approved_correct / approved if approved else 0.0

    @property
    def base_rate(self) -> float:
        """The share of patches that were correct: the precision of no gate at all."""
        total = self.correct + self.wrong
        return self.correct / total if total else 0.0

    @property
    def catch_rate(self) -> float:
        """Of the wrong patches, the share the gate rejected."""
        return self.rejected_wrong / self.wrong if self.wrong else 0.0

    @property
    def false_alarm_rate(self) -> float:
        """Of the correct patches, the share the gate rejected."""
        return self.rejected_correct / self.correct if self.correct else 0.0


@dataclass(frozen=True, slots=True)
class PolicyResult:
    policy: str
    board: scoreboard.Scoreboard
    gate: GateStats
    verdicts: tuple[Verdict, ...]


def evaluate(
    policy: JudgePolicy | str,
    task_ids: list[str],
    matrices: dict[str, Matrix],
    resolutions: list[Resolution],
    entrants: set[str] | None = None,
) -> PolicyResult:
    """Judge every task under ``policy``; ``entrants`` narrows the field of candidates."""
    if entrants is not None:
        matrices = {t: m.restricted(entrants) for t, m in matrices.items()}
        resolutions = [r for r in resolutions if r.entrant in entrants]
    judge = Judge(policy)
    verdicts = tuple(judge(matrices[t]) for t in task_ids if t in matrices)
    board = scoreboard.build(
        task_ids,
        resolutions,
        [v.to_dict() for v in verdicts],
        {t: m.candidates for t, m in matrices.items()},
    )
    return PolicyResult(judge.policy.name, board, gate_stats(verdicts, resolutions), verdicts)


def gate_stats(verdicts: tuple[Verdict, ...], resolutions: list[Resolution]) -> GateStats:
    resolved = {(r.task_id, r.entrant): r.resolved for r in resolutions}
    counts = dict.fromkeys(
        (
            "approved_correct",
            "approved_wrong",
            "rejected_correct",
            "rejected_wrong",
            "unverified_correct",
            "unverified_wrong",
        ),
        0,
    )
    for verdict in verdicts:
        for standing in verdict.standings:
            truth = "correct" if resolved.get((verdict.task_id, standing.entrant)) else "wrong"
            if standing.approved:
                outcome = "approved"
            elif standing.breaks or standing.support is not None:
                outcome = "rejected"
            else:
                outcome = "unverified"
            counts[f"{outcome}_{truth}"] += 1
    return GateStats(**counts)
