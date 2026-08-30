"""Deterministic model assignment across a pool of free-tier lanes.

Measured limits forced this. Every free model here is capped at roughly 8,000
tokens per minute, and a Prosecutor call costs about 1,400 - so any single model
sustains about five calls a minute no matter how many workers are pushing. The
full evaluation is ~1,500 calls across both arms, which is a day and a half on
one model and about an hour spread across four.

Spreading work across models would normally wreck the comparison: if a mutant
were answered by a strong model in one arm and a weaker one in the other, the
difference between arms would partly measure the models rather than retrieval.

So assignment is **deterministic in the mutant id**. Mutant ``0183c16f1ccd``
draws the same model in the no-retrieval arm and the with-retrieval arm, always.
Every mutant is therefore compared against itself under identical conditions -
a paired design - and the aggregate delta is an average of per-model deltas
rather than a mixture of different populations.

It also makes the result stronger than a single-model run would be: an
improvement that holds across four independently-trained models is much harder
to dismiss as an artefact of one model's quirks.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field

from precedent.agents.prosecutor import Prosecutor
from precedent.agents.skeptic import Skeptic


@dataclass(frozen=True, slots=True)
class AgentPool:
    """A set of agent pairs, one per model lane."""

    pairs: tuple[tuple[Prosecutor, Skeptic], ...]

    preferred: dict[str, str] = field(default_factory=dict)
    """Mutant id -> model that must serve it, when one is already established.

    The second arm of an ablation should meet each mutant with the same model
    the first arm used. Hashing alone does not achieve that once lanes are
    allowed to fail over: in the first phase 3 run only 41 of 115 mutants drew
    the same model in both arms, and *every one* of the 12 regressions the
    retrieval arm appeared to lose had swapped models. That is a measurement
    artefact, not a property of retrieval.

    Seeding this from the frozen control arm routes each mutant back to its
    original model, so the comparison is paired by construction rather than by
    luck. Fallback still exists - a wedged lane must not lose the case - and
    the model that actually served is still recorded, so pairing is verified
    afterwards rather than assumed."""

    def __post_init__(self) -> None:
        if not self.pairs:
            raise ValueError("AgentPool needs at least one agent pair")

    @classmethod
    def single(cls, prosecutor: Prosecutor, skeptic: Skeptic) -> AgentPool:
        return cls(pairs=((prosecutor, skeptic),))

    def index_for(self, mutant_id: str) -> int:
        """Which lane serves this mutant. Stable across runs, arms and machines.

        A recorded preference wins, so a mutant returns to the model that
        already answered it. Otherwise the id is hashed - not its position, so
        the assignment does not shift when the corpus is filtered or reordered.
        """
        wanted = self.preferred.get(mutant_id)
        if wanted:
            for index, (prosecutor, _) in enumerate(self.pairs):
                if prosecutor.router.providers[0].model == wanted:
                    return index
        digest = hashlib.sha256(mutant_id.encode()).hexdigest()[:8]
        return int(digest, 16) % len(self.pairs)

    def with_preferences(self, preferred: dict[str, str]) -> AgentPool:
        """A pool that routes the given mutants back to their recorded models."""
        return AgentPool(pairs=self.pairs, preferred=preferred)

    def for_mutant(self, mutant_id: str) -> tuple[Prosecutor, Skeptic]:
        return self.pairs[self.index_for(mutant_id)]

    def model_for(self, mutant_id: str) -> str:
        prosecutor, _ = self.for_mutant(mutant_id)
        return prosecutor.router.providers[0].model

    @property
    def models(self) -> tuple[str, ...]:
        return tuple(p.router.providers[0].model for p, _ in self.pairs)

    def distribution(self, mutant_ids: list[str]) -> dict[str, int]:
        """How a corpus splits across lanes, for reporting."""
        return dict(Counter(self.model_for(m) for m in mutant_ids))

    def combined_stats(self) -> str:
        parts = [
            f"{p.router.providers[0].model}: {p.router.stats.summary()}" for p, _ in self.pairs
        ]
        return "\n    ".join(parts)
