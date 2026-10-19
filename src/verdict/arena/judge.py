"""The judge: picks a patch from the matrix, or refuses to pick one.

Every rule is a statement about executed tests, so every verdict can be
explained by pointing at cells in the matrix:

1. **A reproduction must reproduce.** An exhibit's test counts as evidence
   about the bug only if it FAILS on the untouched base.
2. **No vendor vouches for its own models.** A patch is supported only by the
   reproductions of authors from other vendors.
3. **Breaking something needs a quorum.** A repository test that passed on the
   base and fails on a patch counts against it only if most of the *other*
   candidates keep it passing; when nearly every candidate fails it, the likelier
   story is that the issue asked for that behaviour to change.
4. **Abstain rather than guess.** If no candidate is supported by at least half
   of the independent evidence, there is no verdict.

Among candidates that survive rule 3, the one with the most independent support
wins; ties go to the patch that violates fewer preserved behaviours, then to
the smaller change.

The rules are data - a :class:`JudgePolicy` - so a variant is a new policy, not
an edit. ``v1`` is the policy as committed before any result existed; its
score is the pre-registered one and it is never changed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from verdict.arena.matrix import BASE, Matrix
from verdict.arena.vendors import vendor_of


@dataclass(frozen=True, slots=True)
class JudgePolicy:
    name: str
    support_threshold: float = 0.5
    blind_vouches_for_all: bool = False
    """Whether a blind exhibit may support a patch from its own vendor.

    Rule 2 exists because a contestant's test encodes its own reading of the
    issue. A blind examiner never saw any patch - but it may still share its
    vendor's misreading, which is why v1 excludes it too.
    """

    include: Callable[[str], bool] | None = None
    """Analysis only: which exhibit authors count. Excluded authors' tests are
    ignored entirely, never mistaken for the repository's own."""


POLICIES: dict[str, JudgePolicy] = {
    "v1": JudgePolicy("v1"),
}


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
    """Repository tests this patch breaks while the quorum keeps them (rule 3)."""

    preserved_violations: int
    lines_changed: int


@dataclass(frozen=True, slots=True)
class Verdict:
    task_id: str
    policy: str
    chosen: str | None
    reason: str
    standings: tuple[Standing, ...] = field(default_factory=tuple)
    valid_exhibits: tuple[str, ...] = ()
    invalid_exhibits: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class _Evidence:
    """A matrix's tests, sorted by what they can say (rule 1)."""

    reproductions: dict[str, frozenset[str]]
    """author -> their tests that fail on the base: evidence about the bug."""

    preserved: dict[str, frozenset[str]]
    """author -> their tests that pass on the base: behaviour to keep."""

    suite: frozenset[str]
    """The repository's own tests that pass on the base."""

    invalid: tuple[str, ...]
    """Authors whose exhibit reproduced nothing."""


class Judge:
    def __init__(self, policy: JudgePolicy | str = "v1") -> None:
        self.policy = POLICIES[policy] if isinstance(policy, str) else policy

    def __call__(self, matrix: Matrix) -> Verdict:
        if not matrix.outcomes.get(BASE):
            return Verdict(
                matrix.task_id, self.policy.name, None, "the base tree produced no results"
            )
        candidates = matrix.candidates
        if not candidates:
            return Verdict(matrix.task_id, self.policy.name, None, "no candidate produced results")

        evidence = self._evidence(matrix)
        standings = [self._standing(matrix, evidence, c) for c in candidates]
        chosen, reason = self._decide(standings)
        ordered = tuple(sorted(standings, key=lambda s: (bool(s.breaks), -(s.support or -1.0))))
        return Verdict(
            matrix.task_id,
            self.policy.name,
            chosen,
            reason,
            ordered,
            tuple(sorted(evidence.reproductions)),
            evidence.invalid,
        )

    # --- rule 1 -------------------------------------------------------------------

    def _evidence(self, matrix: Matrix) -> _Evidence:
        reproductions: dict[str, set[str]] = {}
        preserved: dict[str, set[str]] = {}
        suite: set[str] = set()
        for test, outcome in matrix.outcomes[BASE].items():
            author = matrix.author_of(test)
            if author is None:
                if outcome == "passed":
                    suite.add(test)
            elif self._counts(author):
                if outcome == "failed":
                    reproductions.setdefault(author, set()).add(test)
                elif outcome == "passed":
                    preserved.setdefault(author, set()).add(test)
        counted = {a for a in matrix.exhibits if self._counts(a)}
        return _Evidence(
            {a: frozenset(t) for a, t in reproductions.items()},
            {a: frozenset(t) for a, t in preserved.items()},
            frozenset(suite),
            tuple(sorted(counted - set(reproductions))),
        )

    def _counts(self, author: str) -> bool:
        return self.policy.include is None or self.policy.include(author)

    # --- rule 2 -------------------------------------------------------------------

    def _judges_for(self, matrix: Matrix, evidence: _Evidence, candidate: str) -> tuple[str, ...]:
        own = vendor_of(candidate)
        return tuple(
            author
            for author in sorted(evidence.reproductions)
            if vendor_of(author) != own
            or (self.policy.blind_vouches_for_all and matrix.kinds.get(author) == "blind")
        )

    # --- rule 3 -------------------------------------------------------------------

    @staticmethod
    def _quorum_keeps(matrix: Matrix, test: str, candidate: str) -> bool:
        others = [c for c in matrix.candidates if c != candidate]
        return bool(others) and sum(matrix.passes(c, test) for c in others) * 2 > len(others)

    def _breaks(self, matrix: Matrix, evidence: _Evidence, candidate: str) -> tuple[str, ...]:
        alone = len(matrix.candidates) == 1
        return tuple(
            sorted(
                t
                for t in evidence.suite
                if not matrix.passes(candidate, t)
                and (alone or self._quorum_keeps(matrix, t, candidate))
            )
        )

    # --- one candidate ------------------------------------------------------------

    def _standing(self, matrix: Matrix, evidence: _Evidence, candidate: str) -> Standing:
        judges = self._judges_for(matrix, evidence, candidate)
        fractions = [
            sum(matrix.passes(candidate, t) for t in evidence.reproductions[a])
            / len(evidence.reproductions[a])
            for a in judges
        ]
        violations = sum(
            1
            for a in judges
            for t in evidence.preserved.get(a, ())
            if not matrix.passes(candidate, t) and self._quorum_keeps(matrix, t, candidate)
        )
        return Standing(
            entrant=candidate,
            support=round(sum(fractions) / len(fractions), 3) if fractions else None,
            judges=judges,
            reproductions_passed=sum(
                matrix.passes(candidate, t) for a in judges for t in evidence.reproductions[a]
            ),
            reproductions_total=sum(len(evidence.reproductions[a]) for a in judges),
            breaks=self._breaks(matrix, evidence, candidate),
            preserved_violations=violations,
            lines_changed=matrix.lines_changed.get(candidate, 0),
        )

    # --- rule 4 and the decision ----------------------------------------------------

    def _decide(self, standings: list[Standing]) -> tuple[str | None, str]:
        eligible = sorted(
            (s for s in standings if not s.breaks and s.support is not None),
            key=lambda s: (-(s.support or 0.0), s.preserved_violations, s.lines_changed),
        )
        if not eligible:
            if all(s.breaks for s in standings):
                return None, "every candidate breaks a repository test the others keep passing"
            return None, "no independent reproduction exists for any surviving candidate"
        best = eligible[0]
        if (best.support or 0.0) < self.policy.support_threshold:
            return None, (
                f"no candidate passes even half of the independent reproductions "
                f"(best: {best.entrant} at {best.support:.0%})"
            )
        return best.entrant, (
            f"passes {best.reproductions_passed}/{best.reproductions_total} reproductions "
            f"written by {len(best.judges)} independent author(s) and breaks no repository test"
        )
