"""Run the agent pair across a corpus of surviving mutants.

This is the measurement apparatus. Both arms of the ablation call it, and the
*only* difference between them is whether a retrieval block is appended to the
Prosecutor's prompt. Keeping that the single point of divergence is what makes
the resulting delta attributable to retrieval rather than to anything else.
"""

from __future__ import annotations

import json
import threading
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import cosmic_ray.plugins as plugins
from cosmic_ray.mutating import mutate_code

from precedent.agents.context import build_context
from precedent.agents.prosecutor import Accusation, Prosecutor
from precedent.agents.skeptic import Skeptic, SkepticVerdict
from precedent.eval.case import CaseOutcome
from precedent.logging import get_logger
from precedent.mutation.mutant import MutantResult
from precedent.targets.workspace import TargetWorkspace

log = get_logger(__name__)


class RetrievalProvider(Protocol):
    """Supplies the Prosecutor's extra context. Absent in phase 2, real in phase 3."""

    def block_for(self, result: MutantResult) -> tuple[str, list[dict[str, object]]]: ...


@dataclass(frozen=True, slots=True)
class EvalStats:
    """Scoring for one arm."""

    total: int
    caught: int
    by_verdict: dict[str, int]

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
        return f"catch-rate {self.catch_rate:.1%} ({self.caught}/{self.total}) | {breakdown}"


def stats_from(outcomes: list[CaseOutcome]) -> EvalStats:
    return EvalStats(
        total=len(outcomes),
        caught=sum(1 for o in outcomes if o.caught),
        by_verdict=dict(Counter(o.verdict for o in outcomes)),
    )


class EvalHarness:
    """Puts each surviving mutant through Prosecutor then Skeptic."""

    def __init__(
        self,
        prosecutor: Prosecutor,
        skeptic: Skeptic,
        *,
        retrieval: RetrievalProvider | None = None,
    ) -> None:
        self.prosecutor = prosecutor
        self.skeptic = skeptic
        # Phase 2 leaves this None. That is the ablation's control arm.
        self.retrieval = retrieval

    def run_one(self, workspace: TargetWorkspace, result: MutantResult) -> CaseOutcome:
        """Evaluate a single surviving mutant."""
        spec = result.spec
        path = workspace.repo_dir / spec.module_path
        original = path.read_text(encoding="utf-8")
        mutated = mutate_code(original, plugins.get_operator(spec.operator)(), spec.occurrence)
        if mutated is None:
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

        context = build_context(spec, mutated, result.diff)
        retrieval_block: str = ""
        retrieved: list[dict[str, object]] = []
        if self.retrieval is not None:
            retrieval_block, retrieved = self.retrieval.block_for(result)

        accusation = self.prosecutor.accuse(context, retrieval_block=retrieval_block)
        verdict = self.skeptic.adjudicate(workspace, spec, accusation)
        return CaseOutcome.build(
            mutant_id=spec.mutant_id,
            target_slug=spec.target_slug,
            module_path=spec.module_path,
            function=spec.function,
            operator=spec.operator,
            family=spec.family,
            diff=result.diff,
            accusation=accusation,
            verdict=verdict,
            retrieved=retrieved,
        )

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

        shards = [pending[i :: len(workspaces)] for i in range(len(workspaces))]
        with ThreadPoolExecutor(max_workers=len(workspaces)) as pool:
            futures = {
                pool.submit(self._run_shard, workspace, shard, journal, on_result): workspace
                for workspace, shard in zip(workspaces, shards, strict=True)
                if shard
            }
            for future in as_completed(futures):
                outcomes.extend(future.result())

        return _ordered(outcomes, survivors)

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
