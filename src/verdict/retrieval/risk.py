"""Macro retrieval: deciding where to spend effort.

You cannot adversarially probe an entire repository - marshmallow alone yields
920 mutants, and auditing every function on every diff would be too slow to
demo. Something has to decide where scrutiny is worth paying for.

This scores how historically fragile a piece of code is, from evidence the
repository supplies about itself: how often it has been fixed, how recently,
whether a change to it was ever reverted, and how much other code depends on it.
A function that has been patched five times and is called from thirty places is
a different proposition from one that has never been touched.

In the evaluation this ranks and reports rather than gates - every mutant is
still audited so that both ablation arms see an identical corpus. Gating is what
the CLI does when auditing a real pull request, where the budget is real.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from verdict.retrieval.documents import Document

# A defect from ten years ago says less about today's code than one from last
# year. Two years is the point at which an incident counts for half.
HALF_LIFE_SECONDS = 2 * 365 * 24 * 3600


@dataclass(frozen=True, slots=True)
class RiskScore:
    """How fragile a symbol or file looks, and the evidence for saying so."""

    key: str
    score: float
    incidents: int
    reverts: int
    recent_incidents: int
    call_count: int

    def explain(self) -> str:
        parts = [f"{self.incidents} past fix(es)"]
        if self.reverts:
            parts.append(f"{self.reverts} revert(s)")
        if self.recent_incidents:
            parts.append(f"{self.recent_incidents} in the last two years")
        if self.call_count:
            parts.append(f"called from {self.call_count} place(s)")
        return ", ".join(parts)


def _recency_weight(timestamp: int, now: float) -> float:
    """Exponential decay, so old incidents fade rather than vanish."""
    age = max(0.0, now - timestamp)
    return math.pow(0.5, age / HALF_LIFE_SECONDS)


def score_symbols(documents: list[Document], *, now: float | None = None) -> dict[str, RiskScore]:
    """Risk per symbol, aggregated from mined history and call sites."""
    now = now if now is not None else time.time()
    incidents: dict[str, int] = {}
    reverts: dict[str, int] = {}
    recent: dict[str, int] = {}
    weighted: dict[str, float] = {}
    calls: dict[str, int] = {}

    for document in documents:
        if document.kind == "call_site":
            callee = str(document.metadata.get("callee", ""))
            if callee:
                calls[callee] = int(document.metadata.get("call_count", 0))
            continue
        if document.kind not in ("incident", "revert"):
            continue
        timestamp = int(document.metadata.get("timestamp", 0) or 0)
        weight = _recency_weight(timestamp, now) if timestamp else 0.2
        for symbol in document.symbols:
            incidents[symbol] = incidents.get(symbol, 0) + 1
            weighted[symbol] = weighted.get(symbol, 0.0) + weight
            if weight > 0.5:
                recent[symbol] = recent.get(symbol, 0) + 1
            if document.kind == "revert":
                # A revert is the strongest signal available: someone shipped a
                # change here and had to take it back.
                reverts[symbol] = reverts.get(symbol, 0) + 1
                weighted[symbol] = weighted.get(symbol, 0.0) + weight

    scores: dict[str, RiskScore] = {}
    for symbol in set(incidents) | set(calls):
        history = weighted.get(symbol, 0.0)
        # Dependents are damped logarithmically: being called from 30 places is
        # riskier than from 3, but not ten times riskier.
        fan_in = math.log1p(calls.get(symbol, 0))
        scores[symbol] = RiskScore(
            key=symbol,
            score=round(history + 0.35 * fan_in, 4),
            incidents=incidents.get(symbol, 0),
            reverts=reverts.get(symbol, 0),
            recent_incidents=recent.get(symbol, 0),
            call_count=calls.get(symbol, 0),
        )
    return scores


def rank(scores: dict[str, RiskScore], limit: int = 20) -> list[RiskScore]:
    """Highest-risk symbols first, ties broken by name for determinism."""
    return sorted(scores.values(), key=lambda s: (-s.score, s.key))[:limit]
