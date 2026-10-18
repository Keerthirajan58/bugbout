"""Curated, pre-cached examples for a live demo.

Everything here is read from committed run artifacts. No API key, no network,
no pipeline run - so a demo cannot fail because a free tier is rate limited,
which is exactly the failure this project spent a day fighting.

Cases are chosen to show the system's *range* rather than only its wins. A
demo that shows five catches proves the agent can catch things; one that also
shows a rejected cheat and a test that was wrong about the library proves the
Skeptic is doing work, which is the more interesting claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from verdict.artifacts import ArtifactError, read_artifact
from verdict.eval.store import ARM_NO_RETRIEVAL, ARM_WITH_RETRIEVAL, eval_path


@dataclass(frozen=True, slots=True)
class DemoCase:
    """One curated example, with the reason it was selected."""

    slot: str
    headline: str
    why_it_matters: str
    case: dict[str, Any]
    arm: str

    @property
    def mutant_id(self) -> str:
        return str(self.case["mutant_id"])

    @property
    def location(self) -> str:
        return f"{self.case['module_path']}:{self.case.get('function') or '?'}"


# Slot definitions, in the order a demo should walk them. Each names a claim the
# project makes, and picks a case that either supports it or shows its limit.
_SLOTS: tuple[tuple[str, str, str], ...] = (
    (
        "grounded-catch",
        "A regression caught with the repository's own history in hand",
        "The Prosecutor cites a specific past defect or call site, and execution confirms the catch.",
    ),
    (
        "unaided-catch",
        "A regression caught with no retrieval at all",
        "The control arm still catches half the corpus; retrieval has to beat this, not the empty set.",
    ),
    (
        "retrieval-only",
        "Caught only when retrieval was available",
        "The concrete form of the ablation claim: this mutant was missed without context.",
    ),
    (
        "rejected-cheat",
        "A test that tried to read source instead of running code",
        "The scoring rule invites this exploit. It is refused before execution and scored as a miss.",
    ),
    (
        "wrong-about-library",
        "A confident test that was simply wrong",
        "It failed on the UNCHANGED code, so it was never evidence. This is where false positives die.",
    ),
    (
        "no-difference",
        "A test that ran cleanly and found nothing",
        "Execution, not argument, decides. A plausible hypothesis that does not discriminate is not a catch.",
    ),
    (
        "review-rejected",
        "Execution differentiated, but the Skeptic judged it meaningless",
        "The last filter: a real behavioural difference that no user could observe is still not a bug.",
    ),
)


def _load(slug: str, arm: str) -> list[dict[str, Any]]:
    try:
        payload = read_artifact(eval_path(slug, arm), expect_kind="eval_run")["payload"]
    except ArtifactError:
        return []
    cases: list[dict[str, Any]] = payload["cases"]
    return cases


def _first(cases: list[dict[str, Any]], predicate: Any) -> dict[str, Any] | None:
    return next((c for c in cases if predicate(c)), None)


def curate(slug: str) -> list[DemoCase]:
    """Pick one real case per slot from the committed artifacts."""
    control = _load(slug, ARM_NO_RETRIEVAL)
    retrieval = _load(slug, ARM_WITH_RETRIEVAL)
    control_by_id = {c["mutant_id"]: c for c in control}

    picked: list[DemoCase] = []
    used: set[str] = set()

    def take(slot: str, headline: str, why: str, case: dict[str, Any] | None, arm: str) -> None:
        if case is None or case["mutant_id"] in used:
            return
        used.add(case["mutant_id"])
        picked.append(DemoCase(slot, headline, why, case, arm))

    finders: dict[str, Any] = {
        "grounded-catch": lambda: (
            _first(retrieval, lambda c: c["caught"] and c.get("citation")),
            ARM_WITH_RETRIEVAL,
        ),
        "unaided-catch": lambda: (
            _first(control, lambda c: c["caught"] and len(c.get("diff", "")) < 900),
            ARM_NO_RETRIEVAL,
        ),
        "retrieval-only": lambda: (
            _first(
                retrieval,
                lambda c: (
                    c["caught"] and not control_by_id.get(c["mutant_id"], {}).get("caught", True)
                ),
            ),
            ARM_WITH_RETRIEVAL,
        ),
        "rejected-cheat": lambda: (
            _first(control + retrieval, lambda c: c["verdict"] == "rejected_guard"),
            ARM_NO_RETRIEVAL,
        ),
        "wrong-about-library": lambda: (
            _first(control, lambda c: c["verdict"] == "rejected_invalid_on_original"),
            ARM_NO_RETRIEVAL,
        ),
        "no-difference": lambda: (
            _first(control, lambda c: c["verdict"] == "no_difference"),
            ARM_NO_RETRIEVAL,
        ),
        "review-rejected": lambda: (
            _first(control + retrieval, lambda c: c["verdict"] == "rejected_by_review"),
            ARM_NO_RETRIEVAL,
        ),
    }

    for slot, headline, why in _SLOTS:
        case, arm = finders[slot]()
        take(slot, headline, why, case, arm)
    return picked
