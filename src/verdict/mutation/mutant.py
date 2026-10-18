"""The mutant record.

A mutant is stored as a *recipe*, not as a blob of mutated source: the module,
the operator, and which occurrence of that operator to hit. Given the pinned
commit, that recipe regenerates byte-identical mutated code every time.

This matters for phase 2. The Prosecutor is handed a diff and the Skeptic must
execute against pre- and post-diff trees, possibly days later on a different
machine. Storing a recipe rather than a snapshot means those trees can always be
reconstructed exactly, and the artifacts stay small enough to commit.
"""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import asdict, dataclass
from typing import Any, Literal

Status = Literal["killed", "killed_import", "survived", "timeout", "incompetent", "no_mutation"]
"""Outcome of scoring one mutant.

``killed``        a test failed - the suite caught the change
``killed_import`` the mutated module could not even be imported, so the suite
                  failed to collect. Still detection, and the loudest kind: a
                  developer making this change sees the suite explode. Tracked
                  separately from ``killed`` because it is detected trivially
                  rather than by a test asserting the right behaviour.
``survived``      every test still passed - the change went unnoticed
``timeout``       the suite hung, e.g. on a mutated loop bound. Also detection.
``incompetent``   the mutated source is not usable at all. With AST-level
                  operators this should be ~0; a non-zero count means something
                  in the harness needs investigating, so it is never silently
                  folded into another bucket.
``no_mutation``   the operator matched at enumeration but produced no change
"""


@dataclass(frozen=True, slots=True)
class MutantSpec:
    """A reproducible instruction for producing one mutated source tree."""

    mutant_id: str
    target_slug: str
    target_commit: str
    module_path: str
    """Repo-relative, so the record is portable across machines."""

    operator: str
    family: str
    occurrence: int
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    function: str | None
    """Enclosing def/class name. Phase 3 scopes retrieval to this."""

    @staticmethod
    def make_id(
        target_slug: str, target_commit: str, module_path: str, operator: str, occurrence: int
    ) -> str:
        """A content-addressed identifier, stable across runs and machines.

        cosmic-ray assigns ``uuid4()`` job ids, which would give the same mutant
        a different name on every run and make the two ablation arms impossible
        to join. Hashing the recipe instead means mutant ``a1b2c3d4e5f6`` refers
        to the same code change forever.
        """
        payload = f"{target_slug}:{target_commit}:{module_path}:{operator}:{occurrence}"
        return hashlib.sha256(payload.encode()).hexdigest()[:12]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MutantSpec:
        return cls(**{k: v for k, v in data.items() if k in cls.__slots__})


@dataclass(frozen=True, slots=True)
class MutantResult:
    """The outcome of executing a target's suite against one mutant."""

    spec: MutantSpec
    status: Status
    duration_seconds: float
    tests_passed: int
    tests_failed: int
    killing_test: str | None
    """The first test that failed, when one did. Useful evidence for reporting."""

    diff: str
    """Unified diff of the mutation. Retained for survivors, which become the
    corpus of 'changes that pass the whole suite and are still wrong'."""

    @property
    def survived(self) -> bool:
        return self.status == "survived"

    @property
    def counts_toward_score(self) -> bool:
        """Whether this mutant belongs in the kill-rate denominator.

        Mutants that could not be applied, or that broke the code so badly the
        suite could not even run, measure nothing about test quality and are
        excluded. Including them would let unrunnable code inflate the score.
        """
        return self.status in ("killed", "survived", "timeout")

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.spec.to_dict(),
            "status": self.status,
            "duration_seconds": round(self.duration_seconds, 4),
            "tests_passed": self.tests_passed,
            "tests_failed": self.tests_failed,
            "killing_test": self.killing_test,
            "diff": self.diff,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MutantResult:
        return cls(
            spec=MutantSpec.from_dict(data),
            status=data["status"],
            duration_seconds=data["duration_seconds"],
            tests_passed=data["tests_passed"],
            tests_failed=data["tests_failed"],
            killing_test=data.get("killing_test"),
            diff=data.get("diff", ""),
        )


def make_unified_diff(original: str, mutated: str, module_path: str, context: int = 3) -> str:
    """Render a mutation as a unified diff.

    This is the exact artifact the Prosecutor is given in phase 2, so it is
    generated in the standard format a code reviewer would expect rather than in
    a bespoke shape.
    """
    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            mutated.splitlines(keepends=True),
            fromfile=f"a/{module_path}",
            tofile=f"b/{module_path}",
            n=context,
        )
    )
