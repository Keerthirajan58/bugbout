"""What a bout found, as plain data: every number in a report, and nothing to render it."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AgentResult:
    """One contestant across every task, scored by the maintainers' hidden tests."""

    name: str
    vendor: str
    description: dict[str, str]
    tasks: int
    resolved: int
    """Fixed the bug - every fail-to-pass test passes - and broke nothing."""

    fixed_but_broke: int
    """Fixed the bug, but a test that passed before now fails."""

    wrong: int
    """Changed the code, but the bug is still there."""

    no_fix: int
    """Left no change to the source, or one that did not apply."""

    failed_runs: int
    """Scored runs that errored or timed out, whatever they left behind."""

    stopped_by_quota: int
    """Runs a usage limit stopped before they produced anything. They are not scored,
    and ``bugbout run --retry-failed`` runs them again."""

    median_seconds: float | None

    @property
    def resolve_rate(self) -> float:
        return self.resolved / self.tasks if self.tasks else 0.0


@dataclass(frozen=True, slots=True)
class TaskResult:
    task_id: str
    reference: int
    reference_kind: str
    title: str
    attempted_by: tuple[str, ...]
    resolved_by: tuple[str, ...]
    chosen: str | None
    """The judge's pick; ``None`` when it abstained."""

    chosen_resolved: bool
    verdict: str


@dataclass(frozen=True, slots=True)
class Selection:
    """Several agents patched the same issue: which patch should ship?"""

    tasks: int
    best_agent: str | None
    best_agent_resolved: int
    any_agent_resolved: int
    """Tasks some agent resolved: the most any way of choosing could achieve."""

    random_pick: float
    """Expected tasks resolved by picking one candidate at random."""

    judge_resolved: int
    judge_picks: int
    judge_abstained: int
    abstained_correctly: int
    """Abstentions on tasks no candidate resolved - the right call."""

    @property
    def wrong_picks(self) -> int:
        return self.judge_picks - self.judge_resolved


@dataclass(frozen=True, slots=True)
class Gate:
    """One agent says it is done: should its patch merge? Every patch, judged alone."""

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
    def approved(self) -> int:
        return self.approved_correct + self.approved_wrong

    @property
    def precision(self) -> float | None:
        """Of the patches the gate let through, the share that were correct."""
        return self.approved_correct / self.approved if self.approved else None

    @property
    def base_rate(self) -> float | None:
        """The share of all patches that were correct: the precision of no gate at all."""
        total = self.correct + self.wrong
        return self.correct / total if total else None

    @property
    def catch_rate(self) -> float | None:
        """Of the wrong patches, the share the gate rejected."""
        return self.rejected_wrong / self.wrong if self.wrong else None


@dataclass(frozen=True, slots=True)
class EvidenceSource:
    """How well one source of tests separates correct patches from wrong ones."""

    source: str
    kind: str
    """``examiner``, ``contestant`` or ``suite`` - the repository's own tests."""

    auc: float | None
    """Chance that a random correct patch scores above a random wrong one: 0.5 is a coin
    flip, 1.0 a perfect separation. ``None`` without both kinds of patch to compare."""

    correct: int
    wrong: int


@dataclass(frozen=True, slots=True)
class Report:
    project: str
    generated: str
    version: str
    policy: str
    mining: dict[str, Any]
    tasks_mined: int
    tasks_scorable: int
    agents: tuple[AgentResult, ...]
    tasks: tuple[TaskResult, ...]
    selection: Selection
    gate: Gate
    evidence: tuple[EvidenceSource, ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["agents"] = [{**asdict(a), "resolve_rate": a.resolve_rate} for a in self.agents]
        data["selection"]["wrong_picks"] = self.selection.wrong_picks
        data["gate"].update(
            precision=self.gate.precision,
            base_rate=self.gate.base_rate,
            catch_rate=self.gate.catch_rate,
        )
        return data
