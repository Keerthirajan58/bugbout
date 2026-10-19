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
from verdict.arena.matrix import BASE, Matrix
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


@dataclass(frozen=True, slots=True)
class SourceSignal:
    """How well one source of evidence separates correct patches from wrong ones."""

    source: str
    auc: float
    """Probability that a random correct patch scores above a random wrong one
    (ties count half): 0.5 is a coin flip, 1.0 a perfect separation."""

    correct: int
    wrong: int


def evidence_signal(
    matrices: dict[str, Matrix], resolutions: list[Resolution]
) -> list[SourceSignal]:
    """The AUC of every exhibit author, and of the existing suite, across a suite.

    A patch's score from an author is the fraction of that author's reproductions
    (tests that fail on the base) it passes; an author is never scored on its own
    patch. For the existing suite, the score is the number of base-passing tests
    the patch breaks, negated. No rule or threshold is involved: this is the raw
    signal every policy is built on.
    """
    resolved = {(r.task_id, r.entrant): r.resolved for r in resolutions}
    scores: dict[str, list[tuple[float, bool]]] = {}
    for task_id, matrix in matrices.items():
        base = matrix.outcomes.get(BASE, {})
        for author, module in matrix.exhibits.items():
            repro = [t for t, o in base.items() if module in t and o == "failed"]
            for candidate in matrix.candidates:
                if repro and candidate != author:
                    passed = sum(matrix.passes(candidate, t) for t in repro) / len(repro)
                    scores.setdefault(author, []).append(
                        (passed, resolved.get((task_id, candidate), False))
                    )
        suite = [t for t, o in base.items() if o == "passed" and matrix.author_of(t) is None]
        for candidate in matrix.candidates:
            broken = sum(not matrix.passes(candidate, t) for t in suite)
            scores.setdefault(EXISTING_SUITE, []).append(
                (-broken, resolved.get((task_id, candidate), False))
            )
    return sorted(
        (_signal(source, pairs) for source, pairs in scores.items()),
        key=lambda s: -s.auc,
    )


EXISTING_SUITE = "the repository's existing suite"


def _signal(source: str, pairs: list[tuple[float, bool]]) -> SourceSignal:
    good = [score for score, ok in pairs if ok]
    bad = [score for score, ok in pairs if not ok]
    if not good or not bad:
        return SourceSignal(source, float("nan"), len(good), len(bad))
    wins = sum((g > b) + 0.5 * (g == b) for g in good for b in bad)
    return SourceSignal(source, wins / (len(good) * len(bad)), len(good), len(bad))


@dataclass(frozen=True, slots=True)
class Blindness:
    """How many wrong patches the issue's own reproduction tests let through."""

    patches: int
    wrong: int
    wrong_passing_every_reproduction: int
    wrong_scored: int
    """Wrong patches on tasks where the reference author's reproduction reproduced the bug."""


def reproduction_blindness(
    matrices: dict[str, Matrix], resolutions: list[Resolution], author: str
) -> Blindness:
    """Of the wrong patches, how many pass every reproduction ``author`` wrote.

    A wrong patch that passes them fixed the symptom the issue describes and
    still failed the maintainer's tests: no test written from the issue alone
    could have caught it.
    """
    wrong = [r for r in resolutions if not r.resolved]
    passing = scored = 0
    for r in wrong:
        matrix = matrices.get(r.task_id)
        if matrix is None or author not in matrix.exhibits or r.entrant not in matrix.outcomes:
            continue
        module = matrix.exhibits[author]
        repro = [t for t, o in matrix.outcomes[BASE].items() if module in t and o == "failed"]
        if repro:
            scored += 1
            passing += all(matrix.passes(r.entrant, t) for t in repro)
    return Blindness(len(resolutions), len(wrong), passing, scored)
