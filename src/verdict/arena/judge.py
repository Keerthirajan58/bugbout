"""The judge: picks a patch from the matrix, or refuses to pick one.

Every rule is a statement about executed tests, so every verdict can be
explained by pointing at cells in the matrix:

1. **A reproduction must reproduce.** An exhibit's test counts as evidence
   about the bug only if it FAILS on the untouched base. A test that already
   passes there says nothing about whether a patch fixed anything.
2. **No vendor grades its own models.** A patch is supported only by the
   reproductions of authors from other model families. Its own test, and those
   of its stablemates, are ignored when judging it.
3. **Breaking something needs a quorum.** A repository test that passed on the
   base and fails on a patch counts against it only if most of the *other*
   candidates keep it passing. When nearly every candidate fails the same test,
   the likelier story is that the issue asked for that behaviour to change.
4. **Abstain rather than guess.** If no candidate is supported by at least
   half of the independent evidence, there is no verdict - recommending a patch
   nothing vouches for would be the opinion this project exists to avoid.

Among candidates that survive rule 3, the one with the most independent support
wins; ties go to the patch that violates fewer preserved behaviours, then to
the smaller change.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from verdict.arena.matrix import BASE, Matrix, family_of

SUPPORT_THRESHOLD = 0.5


@dataclass(frozen=True, slots=True)
class Standing:
    """How one candidate fared, with the evidence behind each number."""

    entrant: str
    support: float | None
    """Mean fraction of independent reproductions that pass; ``None`` if none exist."""

    judges: tuple[str, ...]
    reproductions_passed: int
    reproductions_total: int
    breaks: tuple[str, ...]
    """Repository tests broken by this patch alone (rule 3)."""

    preserved_violations: int
    lines_changed: int


@dataclass(frozen=True, slots=True)
class Verdict:
    task_id: str
    chosen: str | None
    reason: str
    standings: tuple[Standing, ...] = field(default_factory=tuple)
    valid_exhibits: tuple[str, ...] = ()
    invalid_exhibits: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def judge(matrix: Matrix, evidence: Callable[[str], bool] | None = None) -> Verdict:
    """Judge one task. ``evidence`` optionally limits which exhibit authors count.

    The filter exists for analysis - measuring which *kind* of evidence carries
    the signal - and leaves the rules themselves untouched. Tests by an excluded
    author are ignored entirely, never mistaken for the repository's own.
    """
    base = matrix.outcomes.get(BASE, {})
    candidates = [t for t in matrix.trees if t != BASE and matrix.outcomes.get(t)]
    if not base:
        return Verdict(matrix.task_id, None, "the base tree produced no test results")
    if not candidates:
        return Verdict(matrix.task_id, None, "no candidate patch produced test results")

    # Rule 1: split each author's tests by what they do on the untouched base.
    reproductions: dict[str, set[str]] = {}
    preserved: dict[str, set[str]] = {}
    suite: set[str] = set()
    for test, outcome in base.items():
        author = matrix.author_of(test)
        if author is not None and evidence is not None and not evidence(author):
            continue
        if author is None:
            if outcome == "passed":
                suite.add(test)
        elif outcome == "failed":
            reproductions.setdefault(author, set()).add(test)
        elif outcome == "passed":
            preserved.setdefault(author, set()).add(test)
    valid = tuple(sorted(reproductions))
    counted = {a for a in matrix.exhibits if evidence is None or evidence(a)}
    invalid = tuple(sorted(counted - set(reproductions)))

    def passes(tree: str, test: str) -> bool:
        return matrix.outcomes[tree].get(test) == "passed"

    def quorum_keeps(test: str, excluding: str) -> bool:
        others = [c for c in candidates if c != excluding]
        return bool(others) and sum(passes(c, test) for c in others) * 2 > len(others)

    standings = []
    for candidate in candidates:
        family = family_of(candidate)
        judges = tuple(a for a in valid if family_of(a) != family)  # rule 2
        fractions = [
            sum(passes(candidate, t) for t in reproductions[a]) / len(reproductions[a])
            for a in judges
        ]
        broken = tuple(
            sorted(
                t
                for t in suite
                if not passes(candidate, t) and (quorum_keeps(t, candidate) or len(candidates) == 1)
            )
        )
        violations = sum(
            1
            for a in judges
            for t in preserved.get(a, ())
            if not passes(candidate, t) and quorum_keeps(t, candidate)
        )
        standings.append(
            Standing(
                entrant=candidate,
                support=round(sum(fractions) / len(fractions), 3) if fractions else None,
                judges=judges,
                reproductions_passed=sum(
                    passes(candidate, t) for a in judges for t in reproductions[a]
                ),
                reproductions_total=sum(len(reproductions[a]) for a in judges),
                breaks=broken,
                preserved_violations=violations,
                lines_changed=matrix.lines_changed.get(candidate, 0),
            )
        )

    eligible = [s for s in standings if not s.breaks and s.support is not None]
    eligible.sort(key=lambda s: (-(s.support or 0.0), s.preserved_violations, s.lines_changed))
    ordered = tuple(sorted(standings, key=lambda s: (bool(s.breaks), -(s.support or -1.0))))

    if not eligible:
        reason = (
            "every candidate breaks a repository test the others keep passing"
            if all(s.breaks for s in standings)
            else "no independent reproduction exists for any surviving candidate"
        )
        return Verdict(matrix.task_id, None, reason, ordered, valid, invalid)
    best = eligible[0]
    if (best.support or 0.0) < SUPPORT_THRESHOLD:
        return Verdict(
            matrix.task_id,
            None,
            f"no candidate passes even half of the independent reproductions "
            f"(best: {best.entrant} at {best.support:.0%})",
            ordered,
            valid,
            invalid,
        )
    return Verdict(
        matrix.task_id,
        best.entrant,
        f"passes {best.reproductions_passed}/{best.reproductions_total} reproductions written by "
        f"{len(best.judges)} independent author(s) and breaks no repository test",
        ordered,
        valid,
        invalid,
    )
