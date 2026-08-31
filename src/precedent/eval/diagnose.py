"""Diagnose where an evaluation loses cases, and what could recover them.

The ablation says *whether* retrieval helped. This says *why*, and - more
usefully - estimates the headroom left in each failure mode, so effort goes
where the measured ceiling is highest rather than where intuition points.

Every figure here is computed from recorded case outcomes. Nothing is estimated
from intuition about how language models behave.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

# What each failure mode means, and whether better retrieval could plausibly fix
# it. The judgement encoded here is deliberately conservative: a mode counts as
# retrieval-addressable only when the missing ingredient is *knowledge about
# this codebase*, which is precisely what the retrieval layer supplies.
FAILURE_MODES: dict[str, dict[str, Any]] = {
    "no_difference": {
        "means": "the test ran cleanly but did not distinguish the two versions",
        "retrieval_addressable": True,
        "why": (
            "the agent picked the wrong behaviour to probe; knowing how this code "
            "has failed before, and who calls it, redirects the hypothesis"
        ),
    },
    "rejected_invalid_on_original": {
        "means": "the test failed on the unchanged code - it was wrong about the library",
        "retrieval_addressable": True,
        "why": (
            "the agent misunderstood the API; call sites and the surrounding "
            "code chunk show how the function is actually used"
        ),
    },
    "no_candidate": {
        "means": "the agent produced no test at all",
        "retrieval_addressable": True,
        "why": "with no idea what could break, context supplies one",
    },
    "rejected_by_review": {
        "means": "execution differentiated, but the difference was judged not meaningful",
        "retrieval_addressable": False,
        "why": "a judgement about significance, not a knowledge gap",
    },
    "rejected_error": {
        "means": "the test would not run",
        "retrieval_addressable": False,
        "why": "a code-generation failure, unrelated to context",
    },
    "rejected_guard": {
        "means": "the test tried to read source instead of exercising behaviour",
        "retrieval_addressable": False,
        "why": "a prompt-compliance failure, unrelated to context",
    },
}


@dataclass(frozen=True, slots=True)
class ModeOutcome:
    """How one control-arm failure mode fared in the other arm."""

    mode: str
    count: int
    recovered: int
    """How many the second arm caught."""

    @property
    def recovery_rate(self) -> float:
        return self.recovered / self.count if self.count else 0.0


@dataclass(frozen=True, slots=True)
class Diagnosis:
    """Where the cases went, and how much room is left."""

    paired: int
    control_caught: int
    other_caught: int
    modes: list[ModeOutcome]
    retrieval_used: int
    retrieval_cited: int
    cited_and_caught: int
    citation_on_target: int
    """Cited items whose file or symbol matches the diff - retrieval precision."""

    @property
    def addressable_remaining(self) -> int:
        """Misses still open in modes that better context could plausibly fix."""
        return sum(
            m.count - m.recovered
            for m in self.modes
            if FAILURE_MODES.get(m.mode, {}).get("retrieval_addressable")
        )

    @property
    def unaddressable_remaining(self) -> int:
        return sum(
            m.count - m.recovered
            for m in self.modes
            if not FAILURE_MODES.get(m.mode, {}).get("retrieval_addressable")
        )

    @property
    def ceiling(self) -> float:
        """Catch-rate if every retrieval-addressable miss were recovered.

        An upper bound, not a forecast. Its value is in bounding the payoff: if
        the ceiling is barely above the current rate, no amount of retrieval
        work can move the headline number, and the effort belongs elsewhere.
        """
        if not self.paired:
            return 0.0
        return (self.other_caught + self.addressable_remaining) / self.paired


def _on_target(case: dict[str, Any]) -> bool:
    """Whether the cited item concerns the file or symbol the diff touches."""
    citation = case.get("citation")
    if not citation:
        return False
    doc_id = str(citation).split(":")[-1].strip("[] ")
    for item in case.get("retrieved") or []:
        if item.get("doc_id") != doc_id:
            continue
        if item.get("module_path") == case.get("module_path"):
            return True
        function = case.get("function")
        return bool(function and function in (item.get("symbols") or []))
    return False


def diagnose(control: list[dict[str, Any]], other: list[dict[str, Any]]) -> Diagnosis:
    """Compare two arms case by case, restricted to mutants present in both."""
    control_by_id = {c["mutant_id"]: c for c in control}
    shared = [c for c in other if c["mutant_id"] in control_by_id]

    missed = [c for c in shared if not control_by_id[c["mutant_id"]]["caught"]]
    by_mode = Counter(control_by_id[c["mutant_id"]]["verdict"] for c in missed)
    recovered = Counter(control_by_id[c["mutant_id"]]["verdict"] for c in missed if c["caught"])
    modes = [
        ModeOutcome(mode=mode, count=count, recovered=recovered.get(mode, 0))
        for mode, count in by_mode.most_common()
    ]

    cited = [c for c in shared if c.get("citation")]
    return Diagnosis(
        paired=len(shared),
        control_caught=sum(1 for c in shared if control_by_id[c["mutant_id"]]["caught"]),
        other_caught=sum(1 for c in shared if c["caught"]),
        modes=modes,
        retrieval_used=sum(1 for c in shared if c.get("retrieved")),
        retrieval_cited=len(cited),
        cited_and_caught=sum(1 for c in cited if c["caught"]),
        citation_on_target=sum(1 for c in cited if _on_target(c)),
    )
