"""The judge: picks a patch from the matrix, or refuses to pick one.

Every rule is a statement about executed tests, so every verdict can be
explained by pointing at cells in the matrix:

1. **A reproduction must reproduce.** An exhibit's test is evidence about the
   bug only if it FAILS on the untouched base.
2. **Nobody vouches for themselves.** A patch is supported only by other
   authors' reproductions - under a stricter policy, only by other vendors'.
3. **Breaking something needs a quorum.** A repository test that passed on the
   base and fails on a patch counts against it only if most of the *other*
   candidates keep it passing; when nearly every candidate fails it, the likelier
   story is that the issue asked for that behaviour to change.
4. **Abstain rather than guess.** If no candidate reaches the policy's share of
   the independent reproductions, there is no verdict.

The rules are data - a :class:`JudgePolicy` - so a variant is a new policy, not
an edit.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from bugbout.arena.exhibits import KINDS, Kind
from bugbout.arena.matrix import BASE, Matrix
from bugbout.errors import ConfigError


@dataclass(frozen=True, slots=True)
class JudgePolicy:
    name: str
    support_threshold: float = 0.5
    """The share of independent reproductions a patch must pass to be approved."""

    evidence: frozenset[Kind] = KINDS
    """Which authors' exhibits count; others are ignored entirely."""

    independence: Literal["author", "vendor"] = "author"
    """Who may not vouch for a patch: only its own author, or anyone from its vendor."""

    invariants_block: bool = False
    """Whether failing an examiner's invariant - a test that passes on the base,
    describing behaviour the issue does not ask to change - blocks approval."""

    tie_break: Literal["most_evidence", "smaller_change"] = "most_evidence"
    """Among equally supported candidates: the one that survived more independent
    reproductions, or the smaller patch."""


POLICIES: dict[str, JudgePolicy] = {
    policy.name: policy
    for policy in (
        JudgePolicy("majority"),
        JudgePolicy("unanimous", support_threshold=1.0),
        JudgePolicy("strict", support_threshold=1.0, invariants_block=True),
        JudgePolicy("cross-vendor", independence="vendor"),
    )
}
DEFAULT_POLICY = "majority"


def get_policy(name: str) -> JudgePolicy:
    if name not in POLICIES:
        raise ConfigError(f"unknown judge policy {name!r}; choose one of: {', '.join(POLICIES)}")
    return POLICIES[name]


@dataclass(frozen=True, slots=True)
class Standing:
    """How one candidate fared, with the evidence behind each number."""

    entrant: str
    support: float | None
    """Mean share of each independent author's reproductions it passes; ``None`` if none exist."""

    judges: tuple[str, ...]
    reproductions_passed: int
    reproductions_total: int
    breaks: tuple[str, ...]
    """Repository tests this patch breaks while the quorum keeps them (rule 3)."""

    preserved_violations: int
    lines_changed: int
    invariant_failures: int = 0
    approved: bool = False
    """Supported at or above the threshold and breaking nothing: what a merge gate
    under this policy would let through."""

    @property
    def gate(self) -> Literal["approved", "rejected", "unverified"]:
        """What a merge gate would do with this patch on its own."""
        if self.approved:
            return "approved"
        if self.breaks or self.support is not None:
            return "rejected"
        return "unverified"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Standing:
        payload = {k: v for k, v in data.items() if k in set(cls.__slots__)}
        payload["judges"] = tuple(payload["judges"])
        payload["breaks"] = tuple(payload["breaks"])
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class Verdict:
    task_id: str
    policy: str
    chosen: str | None
    reason: str
    standings: tuple[Standing, ...] = field(default_factory=tuple)
    valid_exhibits: tuple[str, ...] = ()
    invalid_exhibits: tuple[str, ...] = ()
    """Authors whose tests reproduced nothing, so they could not count as evidence."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Verdict:
        return cls(
            task_id=data["task_id"],
            policy=data["policy"],
            chosen=data["chosen"],
            reason=data["reason"],
            standings=tuple(Standing.from_dict(s) for s in data.get("standings", ())),
            valid_exhibits=tuple(data.get("valid_exhibits", ())),
            invalid_exhibits=tuple(data.get("invalid_exhibits", ())),
        )


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


class Judge:
    def __init__(self, policy: JudgePolicy | str = DEFAULT_POLICY) -> None:
        self.policy = get_policy(policy) if isinstance(policy, str) else policy

    def __call__(self, matrix: Matrix) -> Verdict:
        if not matrix.outcomes.get(BASE):
            return self._abstain(matrix, "the base tree produced no results")
        candidates = matrix.candidates
        if not candidates:
            return self._abstain(matrix, "no candidate fix produced results")
        evidence = self._evidence(matrix)
        standings = [self._standing(matrix, evidence, c) for c in candidates]
        chosen, reason = self._decide(standings)
        ordered = sorted(standings, key=lambda s: (bool(s.breaks), -(s.support or -1.0)))
        return Verdict(
            matrix.task_id,
            self.policy.name,
            chosen,
            reason,
            tuple(ordered),
            tuple(sorted(evidence.reproductions)),
            evidence.invalid,
        )

    def _abstain(self, matrix: Matrix, reason: str) -> Verdict:
        return Verdict(matrix.task_id, self.policy.name, None, reason)

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
            elif matrix.kinds.get(author) in self.policy.evidence:
                if outcome == "failed":
                    reproductions.setdefault(author, set()).add(test)
                elif outcome == "passed":
                    preserved.setdefault(author, set()).add(test)
        counted = {a for a in matrix.exhibits if matrix.kinds.get(a) in self.policy.evidence}
        return _Evidence(
            {a: frozenset(t) for a, t in reproductions.items()},
            {a: frozenset(t) for a, t in preserved.items()},
            frozenset(suite),
            tuple(sorted(counted - set(reproductions))),
        )

    # --- rule 2 -------------------------------------------------------------------

    def _independent(self, matrix: Matrix, author: str, candidate: str) -> bool:
        if author == candidate:
            return False
        if self.policy.independence == "author":
            return True
        vendor = matrix.vendors.get(author)
        return vendor is None or vendor != matrix.vendors.get(candidate)

    # --- rule 3 -------------------------------------------------------------------

    @staticmethod
    def _quorum_keeps(matrix: Matrix, test: str, candidate: str) -> bool:
        others = [c for c in matrix.candidates if c != candidate]
        return bool(others) and sum(matrix.passes(c, test) for c in others) * 2 > len(others)

    def _breaks(self, matrix: Matrix, evidence: _Evidence, candidate: str) -> tuple[str, ...]:
        alone = len(matrix.candidates) == 1
        return tuple(
            sorted(
                test
                for test in evidence.suite
                if not matrix.passes(candidate, test)
                and (alone or self._quorum_keeps(matrix, test, candidate))
            )
        )

    # --- one candidate ------------------------------------------------------------

    def _standing(self, matrix: Matrix, evidence: _Evidence, candidate: str) -> Standing:
        judges = tuple(
            a for a in sorted(evidence.reproductions) if self._independent(matrix, a, candidate)
        )
        fractions = [
            sum(matrix.passes(candidate, t) for t in evidence.reproductions[a])
            / len(evidence.reproductions[a])
            for a in judges
        ]
        support = round(sum(fractions) / len(fractions), 3) if fractions else None
        violations = sum(
            1
            for a in judges
            for t in evidence.preserved.get(a, ())
            if not matrix.passes(candidate, t) and self._quorum_keeps(matrix, t, candidate)
        )
        invariant_failures = sum(
            1
            for a in judges
            if matrix.kinds.get(a) == "examiner"
            for t in evidence.preserved.get(a, ())
            if not matrix.passes(candidate, t)
        )
        breaks = self._breaks(matrix, evidence, candidate)
        return Standing(
            entrant=candidate,
            support=support,
            judges=judges,
            reproductions_passed=sum(
                matrix.passes(candidate, t) for a in judges for t in evidence.reproductions[a]
            ),
            reproductions_total=sum(len(evidence.reproductions[a]) for a in judges),
            breaks=breaks,
            preserved_violations=violations,
            lines_changed=matrix.lines_changed.get(candidate, 0),
            invariant_failures=invariant_failures,
            approved=(
                not breaks
                and support is not None
                and support >= self.policy.support_threshold
                and not (self.policy.invariants_block and invariant_failures)
            ),
        )

    # --- rule 4 and the decision --------------------------------------------------

    def _tie_break(self, standing: Standing) -> int:
        if self.policy.tie_break == "most_evidence":
            return -standing.reproductions_total
        return standing.lines_changed

    def _decide(self, standings: list[Standing]) -> tuple[str | None, str]:
        eligible = sorted(
            (s for s in standings if not s.breaks and s.support is not None),
            key=lambda s: (-(s.support or 0.0), s.preserved_violations, self._tie_break(s)),
        )
        if not eligible:
            if all(s.breaks for s in standings):
                return None, "every candidate breaks a repository test the others keep passing"
            return None, "no independent reproduction exists for any surviving candidate"
        best = eligible[0]
        if not best.approved:
            return None, (
                f"no candidate reaches {self.policy.support_threshold:.0%} of the independent "
                f"reproductions (best: {best.entrant} at {best.support or 0.0:.0%})"
            )
        return best.entrant, (
            f"passes {best.reproductions_passed}/{best.reproductions_total} reproductions "
            f"written by {len(best.judges)} independent author(s) and breaks no repository test"
        )
