"""Run the agent pair across a corpus of surviving mutants.

This is the measurement apparatus. Both arms of the ablation call it, and the
*only* difference between them is whether a retrieval block is appended to the
Prosecutor's prompt. Keeping that the single point of divergence is what makes
the resulting delta attributable to retrieval rather than to anything else.
"""

from __future__ import annotations

import json
import threading
from collections import Counter, defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from precedent.agents.change import CodeChange
from precedent.agents.context import build_context
from precedent.agents.prosecutor import Accusation, Prosecutor
from precedent.agents.skeptic import Skeptic, SkepticVerdict
from precedent.eval.case import CaseOutcome
from precedent.eval.pool import AgentPool
from precedent.logging import get_logger
from precedent.mutation.mutant import MutantResult
from precedent.targets.workspace import TargetWorkspace

log = get_logger(__name__)


class RetrievalProvider(Protocol):
    """Supplies the Prosecutor's extra context. Absent in phase 2, real in phase 3."""

    def block_for(self, change: CodeChange) -> tuple[str, list[dict[str, object]]]: ...


@dataclass(frozen=True, slots=True)
class Deliberation:
    """One full pass of the agent pair over a change, whatever produced it."""

    accusation: Accusation
    verdict: SkepticVerdict
    retrieved: list[dict[str, object]]
    repairs: int
    gate_failures: int


@dataclass(frozen=True, slots=True)
class EvalStats:
    """Scoring for one arm."""

    total: int
    caught: int
    by_verdict: dict[str, int]

    @property
    def execution_confirmed(self) -> int:
        """Cases where running the test proved a behavioural difference.

        Reported alongside the headline figure because it separates the two
        things the Skeptic does: execution establishes that behaviour differs,
        and review judges whether that difference matters. Publishing only the
        stricter number would hide how much the reviewer is discarding.
        """
        return self.caught + self.by_verdict.get("rejected_by_review", 0)

    @property
    def execution_rate(self) -> float:
        return self.execution_confirmed / self.total if self.total else 0.0

    @property
    def catch_rate(self) -> float:
        """Fraction of surviving mutants the agent pair proved a regression in.

        The denominator is every mutant attempted, including ones where the
        Prosecutor declined or the model failed. Excluding those would let a
        flaky provider inflate the score.
        """
        return self.caught / self.total if self.total else 0.0

    def summary(self) -> str:
        breakdown = ", ".join(f"{k}={v}" for k, v in Counter(self.by_verdict).most_common())
        return (
            f"catch-rate {self.catch_rate:.1%} ({self.caught}/{self.total}) | "
            f"execution-confirmed {self.execution_rate:.1%} "
            f"({self.execution_confirmed}/{self.total}) | {breakdown}"
        )


def stats_from(outcomes: list[CaseOutcome]) -> EvalStats:
    return EvalStats(
        total=len(outcomes),
        caught=sum(1 for o in outcomes if o.caught),
        by_verdict=dict(Counter(o.verdict for o in outcomes)),
    )


class EvalHarness:
    """Puts each surviving mutant through Prosecutor then Skeptic."""

    # Verdicts where the test never ran correctly against the unchanged code.
    # These say nothing about the diff, so one repair attempt is allowed.
    REPAIRABLE = ("rejected_invalid_on_original", "rejected_error", "rejected_guard")

    # The grounding gate proper: the candidate ran, and failed, against code that
    # was never changed. Distinct from the other two repairable verdicts, where
    # the test never executed at all and so says nothing either way.
    GATE_REJECTION = "rejected_invalid_on_original"

    def __init__(
        self,
        prosecutor: Prosecutor,
        skeptic: Skeptic,
        *,
        retrieval: RetrievalProvider | None = None,
        max_repairs: int = 1,
        pool: AgentPool | None = None,
    ) -> None:
        self.prosecutor = prosecutor
        self.skeptic = skeptic
        # A pool spreads work across free-tier model lanes while keeping the
        # per-mutant assignment identical in both arms. Without one, every
        # mutant uses the same pair.
        self.pool = pool or AgentPool.single(prosecutor, skeptic)
        # Phase 2 leaves this None. That is the ablation's control arm.
        self.retrieval = retrieval
        # Bounded deliberately. An unbounded loop would let a model grind until
        # something incidentally differs, which is a much weaker claim than
        # catching the regression. Both arms get the same allowance.
        self.max_repairs = max_repairs

    def run_one(self, workspace: TargetWorkspace, result: MutantResult) -> CaseOutcome:
        """Evaluate a single surviving mutant."""
        spec = result.spec
        change = CodeChange.from_mutant(workspace, spec, result.diff)
        if change is None:
            return CaseOutcome.build(
                mutant_id=spec.mutant_id,
                target_slug=spec.target_slug,
                module_path=spec.module_path,
                function=spec.function,
                operator=spec.operator,
                family=spec.family,
                diff=result.diff,
                accusation=Accusation.failed("the change could not be reconstructed"),
                verdict=SkepticVerdict(
                    verdict="rejected_error",
                    reason="the change could not be reconstructed from its recipe",
                ),
            )

        # Deterministic in the mutant id, so this mutant draws the same model in
        # both arms and is compared against itself.
        prosecutor, skeptic = self.pool.for_mutant(spec.mutant_id)
        outcome = self.deliberate(workspace, change, prosecutor, skeptic)

        return CaseOutcome.build(
            mutant_id=spec.mutant_id,
            target_slug=spec.target_slug,
            module_path=spec.module_path,
            function=spec.function,
            operator=spec.operator,
            family=spec.family,
            diff=result.diff,
            accusation=outcome.accusation,
            verdict=outcome.verdict,
            retrieved=outcome.retrieved,
            repairs=outcome.repairs,
            gate_failures=outcome.gate_failures,
        )

    def deliberate(
        self,
        workspace: TargetWorkspace,
        change: CodeChange,
        prosecutor: Prosecutor,
        skeptic: Skeptic,
    ) -> Deliberation:
        """Prosecute one change, adjudicate it, and repair once if the gate rejects.

        Public and change-shaped so the real-diff study runs the identical
        procedure. Nothing here knows what produced the change.
        """
        context = build_context(change)
        retrieval_block: str = ""
        retrieved: list[dict[str, object]] = []
        if self.retrieval is not None:
            retrieval_block, retrieved = self.retrieval.block_for(change)

        accusation = prosecutor.accuse(context, retrieval_block=retrieval_block)
        verdict = skeptic.adjudicate(workspace, change, accusation)

        # The grounding gate: a candidate that fails against the unmutated code
        # is wrong about the library, not about the change. Counted per attempt
        # rather than only at the end, so a mutant repaired on the second try
        # still records that its first candidate was a false positive.
        gate_failures = 1 if verdict.verdict == self.GATE_REJECTION else 0

        repairs = 0
        while (
            repairs < self.max_repairs and verdict.verdict in self.REPAIRABLE and accusation.usable
        ):
            repairs += 1
            failure = (
                verdict.original_result.tail(25)
                if verdict.original_result is not None
                else verdict.reason
            )
            revised = prosecutor.revise(
                context, accusation, failure, retrieval_block=retrieval_block
            )
            if not revised.usable:
                break
            revised_verdict = skeptic.adjudicate(workspace, change, revised)
            if revised_verdict.verdict == self.GATE_REJECTION:
                gate_failures += 1
            # Keep the repair only if it actually improved matters; a revision
            # that lands in the same bucket should not overwrite the record of
            # the first attempt.
            if revised_verdict.verdict not in self.REPAIRABLE:
                accusation, verdict = revised, revised_verdict
                break
            accusation, verdict = revised, revised_verdict

        return Deliberation(accusation, verdict, retrieved, repairs, gate_failures)

    def run(
        self,
        workspaces: list[TargetWorkspace],
        survivors: list[MutantResult],
        *,
        journal: Path | None = None,
        on_result: Callable[[CaseOutcome], None] | None = None,
    ) -> list[CaseOutcome]:
        """Evaluate a corpus, optionally journalling progress for resumption.

        The journal matters on a free tier. A run of several hundred mutants can
        take hours and may be interrupted by a daily quota; without it, resuming
        would mean re-executing every sandbox run already completed.
        """
        done: dict[str, CaseOutcome] = {}
        if journal and journal.exists():
            done = _read_journal(journal)
            if done:
                log.info("Resuming: %d cases already recorded in %s", len(done), journal.name)

        pending = [s for s in survivors if s.spec.mutant_id not in done]
        outcomes: list[CaseOutcome] = list(done.values())
        if not pending:
            return _ordered(outcomes, survivors)

        if journal:
            journal.parent.mkdir(parents=True, exist_ok=True)

        shards = self._shard(pending, len(workspaces))
        with ThreadPoolExecutor(max_workers=len(workspaces)) as pool:
            futures = {
                pool.submit(self._run_shard, workspace, shard, journal, on_result): workspace
                for workspace, shard in zip(workspaces, shards, strict=True)
                if shard
            }
            for future in as_completed(futures):
                outcomes.extend(future.result())

        return _ordered(outcomes, survivors)

    def _shard(self, pending: list[MutantResult], workers: int) -> list[list[MutantResult]]:
        """Split work across workers, aligning each worker to one model lane.

        Round-robin sharding wastes most of the available throughput. Lanes are
        paced (~11s between calls) to stay under the token-per-minute limit, so
        a worker whose next mutant belongs to a busy lane simply sleeps - it
        cannot skip ahead to other work. Measured on the real run, that left
        73% of lane capacity idle.

        Grouping by lane removes the cross-blocking: each worker only ever waits
        on its own lane's pacing, which it would have to wait for anyway. When
        there are more workers than lanes, several workers share a lane and
        overlap their sandbox execution while the lane paces the API calls.
        """
        lanes = len(self.pool.pairs)
        if lanes <= 1 or workers < lanes:
            return [pending[i::workers] for i in range(workers)]

        by_lane: dict[int, list[MutantResult]] = defaultdict(list)
        for result in pending:
            by_lane[self.pool.index_for(result.spec.mutant_id)].append(result)

        shards: list[list[MutantResult]] = [[] for _ in range(workers)]
        for lane, items in by_lane.items():
            owners = [w for w in range(workers) if w % lanes == lane] or [lane]
            for index, item in enumerate(items):
                shards[owners[index % len(owners)]].append(item)
        return shards

    def _run_shard(
        self,
        workspace: TargetWorkspace,
        shard: list[MutantResult],
        journal: Path | None,
        on_result: Callable[[CaseOutcome], None] | None,
    ) -> list[CaseOutcome]:
        workspace.ensure_clean()
        produced: list[CaseOutcome] = []
        for result in shard:
            outcome = self.run_one(workspace, result)
            produced.append(outcome)
            if journal:
                _append_journal(journal, outcome)
            if on_result is not None:
                on_result(outcome)
        return produced


def _ordered(outcomes: list[CaseOutcome], survivors: list[MutantResult]) -> list[CaseOutcome]:
    """Restore corpus order, which sharding and completion order both destroy."""
    position = {s.spec.mutant_id: i for i, s in enumerate(survivors)}
    return sorted(outcomes, key=lambda o: position.get(o.mutant_id, len(position)))


def _read_journal(path: Path) -> dict[str, CaseOutcome]:
    recovered: dict[str, CaseOutcome] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            outcome = CaseOutcome.from_dict(json.loads(line))
        except (json.JSONDecodeError, TypeError):
            # A partially-written final line after an interrupted run. Skip it;
            # that one case is simply re-evaluated.
            continue
        recovered[outcome.mutant_id] = outcome
    return recovered


# Shards run on separate threads but share one journal file. A JSON line here
# carries a diff and a full test body, so it comfortably exceeds the size at
# which concurrent appends are atomic - without this lock, two workers finishing
# together could interleave and corrupt both records.
_JOURNAL_LOCK = threading.Lock()


def _append_journal(path: Path, outcome: CaseOutcome) -> None:
    """Append one result. Line-delimited JSON, so a kill loses at most one case."""
    line = json.dumps(outcome.to_dict(), default=str) + "\n"
    with _JOURNAL_LOCK, path.open("a", encoding="utf-8") as handle:
        handle.write(line)
