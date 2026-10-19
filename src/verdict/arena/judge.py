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

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from verdict.arena.examiners.exhibit import BLIND, Kind
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

    evidence: frozenset[Kind] = frozenset({"agentic", "prompted", "self"})
    """Which kinds of exhibit count. Tests of other kinds are ignored entirely,
    never mistaken for the repository's own."""

    independence: Literal["vendor", "author"] = "vendor"
    """Who may not vouch for a patch: anyone from its vendor, or only its author.

    ``vendor`` treats two models from one lab as one opinion. ``author`` only
    stops a contestant's own test from vouching for its own fix."""

    invariants_block: bool = False
    """Whether a patch that fails an independent blind examiner's invariant - a test
    that passes on the base, written as behaviour the issue does not ask to change -
    may not be approved."""

    tie_break: Literal["smaller_change", "most_evidence"] = "smaller_change"
    """Among equally supported candidates: the smaller patch, or the one that
    survived more independent reproductions."""


_EXECUTED: frozenset[Kind] = frozenset({"agentic", "self"})
"""Evidence whose author ran it against the real code before submitting it."""

POLICIES: dict[str, JudgePolicy] = {
    # Committed before any result existed; its dev score is the pre-registered one.
    "v1": JudgePolicy("v1"),
    # --- candidates, declared before agentic evidence was judged -----------------
    # One is frozen for the held-out suite by the procedure in SELECTION below.
    "unanimous": JudgePolicy("unanimous", support_threshold=1.0, tie_break="most_evidence"),
    "agentic": JudgePolicy("agentic", evidence=_EXECUTED),
    "agentic-open": JudgePolicy(
        "agentic-open", evidence=_EXECUTED, blind_vouches_for_all=True, tie_break="most_evidence"
    ),
    "agentic-open-unanimous": JudgePolicy(
        "agentic-open-unanimous",
        evidence=_EXECUTED,
        blind_vouches_for_all=True,
        support_threshold=1.0,
        tie_break="most_evidence",
    ),
    "all-open-unanimous": JudgePolicy(
        "all-open-unanimous",
        blind_vouches_for_all=True,
        support_threshold=1.0,
        tie_break="most_evidence",
    ),
}

# --- added after the first round of dev results (2026-10-18, 19:4x) ------------
# The first candidates showed that issue-derived reproductions do not separate
# partial fixes from complete ones. These variants measure contestants' tests
# as cross-checks of each other, excluding only a patch's own author.
POLICIES |= {
    "cross": JudgePolicy("cross", independence="author", tie_break="most_evidence"),
    "cross-executed": JudgePolicy(
        "cross-executed", evidence=_EXECUTED, independence="author", tie_break="most_evidence"
    ),
    "cross-executed-unanimous": JudgePolicy(
        "cross-executed-unanimous",
        evidence=_EXECUTED,
        independence="author",
        support_threshold=1.0,
        tie_break="most_evidence",
    ),
}

# --- added with the Specify examiner, before its evidence was judged -------------
# Specify asks for the maintainer's general rule plus invariants, aimed at the
# partial fixes that issue-derived reproductions let through.
POLICIES |= {
    "spec": JudgePolicy(
        "spec",
        evidence=_EXECUTED,
        independence="author",
        support_threshold=1.0,
        tie_break="most_evidence",
    ),
    "spec-strict": JudgePolicy(
        "spec-strict",
        evidence=_EXECUTED,
        independence="author",
        support_threshold=1.0,
        invariants_block=True,
        tie_break="most_evidence",
    ),
    "all-strict": JudgePolicy(
        "all-strict",
        independence="author",
        support_threshold=1.0,
        invariants_block=True,
        tie_break="most_evidence",
    ),
}

SELECTION = """\
How one candidate policy is frozen for the held-out suite (declared in advance):
measured on the dev suite with the held-out suite's field of contestants
(codex/gpt-6.1-sol and codex/gpt-5.6-luna only), keep the policies whose gate
approves at least half of the correct patches, and freeze the one with the
highest gate precision; ties go to the most resolved selections."""

FROZEN = "cross-executed"
"""The policy the procedure above froze (2026-10-18), before any held-out
result was computed. On dev, test-like field: 'cross' and 'cross-executed'
tied on every measure - 95.7% of correct patches approved, precision 0.579,
13 of 22 resolved selections. 'cross-executed' counts only evidence whose
author executed it, which the held-out run supplies in full; the two differ
only when one-shot prompted exhibits exist."""


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
    invariant_failures: int = 0
    """Independent blind examiners' invariants this patch fails, quorum or not."""

    approved: bool = False
    """Supported at or above the policy's threshold, and breaking nothing:
    the patch this policy would let through a merge gate."""


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
            elif self._counts(matrix, author):
                if outcome == "failed":
                    reproductions.setdefault(author, set()).add(test)
                elif outcome == "passed":
                    preserved.setdefault(author, set()).add(test)
        counted = {a for a in matrix.exhibits if self._counts(matrix, a)}
        return _Evidence(
            {a: frozenset(t) for a, t in reproductions.items()},
            {a: frozenset(t) for a, t in preserved.items()},
            frozenset(suite),
            tuple(sorted(counted - set(reproductions))),
        )

    def _counts(self, matrix: Matrix, author: str) -> bool:
        return matrix.kinds.get(author, "prompted") in self.policy.evidence

    # --- rule 2 -------------------------------------------------------------------

    def _judges_for(self, matrix: Matrix, evidence: _Evidence, candidate: str) -> tuple[str, ...]:
        return tuple(
            a for a in sorted(evidence.reproductions) if self._independent(matrix, a, candidate)
        )

    def _independent(self, matrix: Matrix, author: str, candidate: str) -> bool:
        if author == candidate:
            return False
        if self.policy.independence == "author":
            return True
        return vendor_of(author) != vendor_of(candidate) or (
            self.policy.blind_vouches_for_all and matrix.kinds.get(author) in BLIND
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
        support = round(sum(fractions) / len(fractions), 3) if fractions else None
        breaks = self._breaks(matrix, evidence, candidate)
        invariant_failures = sum(
            1
            for a in judges
            if matrix.kinds.get(a) in BLIND
            for t in evidence.preserved.get(a, ())
            if not matrix.passes(candidate, t)
        )
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

    # --- rule 4 and the decision ----------------------------------------------------

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
        if (best.support or 0.0) < self.policy.support_threshold:
            return None, (
                f"no candidate passes even half of the independent reproductions "
                f"(best: {best.entrant} at {best.support:.0%})"
            )
        return best.entrant, (
            f"passes {best.reproductions_passed}/{best.reproductions_total} reproductions "
            f"written by {len(best.judges)} independent author(s) and breaks no repository test"
        )
