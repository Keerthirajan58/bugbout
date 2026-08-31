"""The record of one mutant put through the agent pair."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from precedent.agents.prosecutor import Accusation
from precedent.agents.skeptic import SkepticVerdict


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    """Everything that happened for one surviving mutant, in one arm.

    Deliberately verbose. The dashboard has to reconstruct the whole debate -
    what was retrieved, what the Prosecutor argued, what the Skeptic executed,
    and why the verdict landed where it did - from this record alone, without
    re-running anything.
    """

    mutant_id: str
    target_slug: str
    module_path: str
    function: str | None
    operator: str
    family: str
    diff: str

    verdict: str
    caught: bool
    reason: str

    hypothesis: str = ""
    reasoning: str = ""
    test_name: str = ""
    test_code: str = ""
    confident: bool = False

    provider: str = ""
    model: str = ""
    cached: bool = False
    prosecutor_latency: float = 0.0
    review_reason: str | None = None

    repairs: int = 0
    """How many repair attempts were spent before this verdict."""

    citation: str | None = None
    """Phase 3 only: which retrieved item motivated the hypothesis."""

    used_evidence: str = ""
    """Phase 3 only: the Prosecutor's own account of what the evidence told it."""

    retrieved: list[dict[str, Any]] = field(default_factory=list)
    """Phase 3 only: the context supplied to the Prosecutor."""

    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CaseOutcome:
        known = set(cls.__slots__)
        return cls(**{k: v for k, v in data.items() if k in known})

    @classmethod
    def build(
        cls,
        *,
        mutant_id: str,
        target_slug: str,
        module_path: str,
        function: str | None,
        operator: str,
        family: str,
        diff: str,
        accusation: Accusation,
        verdict: SkepticVerdict,
        retrieved: list[dict[str, Any]] | None = None,
        repairs: int = 0,
    ) -> CaseOutcome:
        return cls(
            mutant_id=mutant_id,
            target_slug=target_slug,
            module_path=module_path,
            function=function,
            operator=operator,
            family=family,
            diff=diff,
            verdict=verdict.verdict,
            caught=verdict.caught,
            reason=verdict.reason,
            hypothesis=accusation.hypothesis,
            reasoning=accusation.reasoning,
            test_name=accusation.test_name,
            test_code=accusation.test_code,
            confident=accusation.confident,
            provider=accusation.provider,
            model=accusation.model,
            cached=accusation.cached,
            prosecutor_latency=accusation.latency_seconds,
            review_reason=verdict.review_reason,
            repairs=repairs,
            citation=accusation.citation,
            used_evidence=accusation.used_evidence,
            retrieved=retrieved or [],
            error=accusation.error,
        )
