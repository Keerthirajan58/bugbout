"""The repositories Precedent audits.

Every target is pinned to an immutable commit SHA rather than a tag or branch.
The ablation number is a claim about a specific tree; if the checkout could move
underneath us, the "with retrieval" and "without retrieval" arms would no longer
be measuring the same thing.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class TargetRepo:
    """A pinned, reproducible checkout of a repository under audit."""

    slug: str
    """Short identifier used in paths, artifacts and CLI arguments."""

    owner: str
    name: str

    ref: str
    """Human-readable release tag the pin corresponds to."""

    commit: str
    """Immutable commit SHA. This, not ``ref``, is what gets checked out."""

    spdx_license: str

    source_root: str
    """Package directory relative to the repo root, e.g. ``src/marshmallow``."""

    test_root: str
    """Test directory relative to the repo root."""

    test_dependencies: tuple[str, ...] = field(default_factory=tuple)
    """Packages the suite imports that installing the project alone won't provide."""

    selection_rationale: str = ""
    """Why this repo earns its place, recorded so the choice stays auditable."""

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def clone_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.name}.git"

    @property
    def short_commit(self) -> str:
        return self.commit[:12]


MARSHMALLOW = TargetRepo(
    slug="marshmallow",
    owner="marshmallow-code",
    name="marshmallow",
    ref="4.3.1",
    commit="c7b559a1fa3aba57ca6dba0ab336841c5038a782",
    spdx_license="MIT",
    source_root="src/marshmallow",
    test_root="tests",
    test_dependencies=("pytest", "simplejson"),
    selection_rationale=(
        "1,819 statements at 98% coverage with a 1.4s suite - the fastest full-suite "
        "re-run of any candidate, which is what makes per-mutant execution affordable. "
        "Serialization and validation logic fails quietly (None handling, partial "
        "loads, field coercion), so surviving mutants tend to be genuine silent "
        "regressions rather than crashes. 21 reverts in history to mine."
    ),
)

ARROW = TargetRepo(
    slug="arrow",
    owner="arrow-py",
    name="arrow",
    ref="1.4.0",
    commit="2c1f5185ad66bf3b1cf8c0812f4b4fe65ac46656",
    spdx_license="Apache-2.0",
    source_root="arrow",
    test_root="tests",
    test_dependencies=(
        "pytest",
        "pytest-mock",
        "python-dateutil",
        "dateparser",
        "pytz",
        "simplejson",
    ),
    selection_rationale=(
        "2,315 statements at 99.9% coverage - the highest of any candidate, meaning a "
        "surviving mutant is almost always a real gap in the test suite rather than "
        "merely uncovered code. Date, timezone and DST arithmetic is the canonical "
        "domain for changes that pass their tests and are still wrong, which is "
        "exactly the failure mode Precedent claims to catch."
    ),
)

TARGETS: dict[str, TargetRepo] = {t.slug: t for t in (MARSHMALLOW, ARROW)}


class UnknownTargetError(KeyError):
    """Raised when a slug does not match any registered target."""

    def __init__(self, slug: str) -> None:
        known = ", ".join(sorted(TARGETS))
        super().__init__(f"Unknown target {slug!r}. Registered targets: {known}")


def get_target(slug: str) -> TargetRepo:
    """Look up a target by slug, with a message that lists the valid ones."""
    try:
        return TARGETS[slug]
    except KeyError:
        raise UnknownTargetError(slug) from None


def list_targets() -> list[TargetRepo]:
    """All registered targets, in a stable order."""
    return [TARGETS[slug] for slug in sorted(TARGETS)]
