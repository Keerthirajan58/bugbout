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

    extra_pytest_args: tuple[str, ...] = field(default_factory=tuple)
    """Test-selection flags this repo needs, restored after ``addopts`` is cleared.

    Clearing ``addopts`` is what stops a repo's coverage gate from deciding a
    mutant's fate, but some projects also use it for legitimate *selection*.
    click marks 31,000 stress tests and deselects them by default with
    ``-m 'not stress'``; stripping that turns a 2.5s suite into one that does
    not finish. Gates are dropped, selection is preserved - and preserved
    explicitly here, per target, rather than by trying to parse a repo's config.
    """

    historical_source_roots: tuple[str, ...] = field(default_factory=tuple)
    """Paths the package lived at earlier in its history.

    Repositories move. marshmallow switched to a ``src/`` layout in 2019, so a
    ``git log -- src/marshmallow`` sees only 484 of the 1,385 commits that ever
    touched the package - 65% of its own bugfix history would be invisible to
    the retrieval layer, silently and with no error."""

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

    @property
    def source_pathspecs(self) -> tuple[str, ...]:
        """Every path to search when mining history, current and historical."""
        return (self.source_root, *self.historical_source_roots)

    def normalize_path(self, path: str) -> str:
        """Rewrite a historical file path to where that file lives today.

        A 2016 commit touching ``marshmallow/utils.py`` concerns the same code
        as today's ``src/marshmallow/utils.py``. Without this, historical
        incidents cannot be matched to the file a diff is in.
        """
        for old_root in self.historical_source_roots:
            if path.startswith(f"{old_root}/"):
                return f"{self.source_root}/{path[len(old_root) + 1 :]}"
        return path


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
    historical_source_roots=("marshmallow",),
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

# --- expansion set -----------------------------------------------------------
# Added so the corpus spans six repositories rather than two. Mutation testing
# needs no API quota, so breadth here costs CPU time only - which is exactly the
# right trade when free-tier LLM throughput is the scarce resource.

CLICK = TargetRepo(
    slug="click",
    owner="pallets",
    name="click",
    ref="8.5.0",
    commit="8b19813f2bfca99f1018a587a8cf54fc959f2e5d",
    spdx_license="BSD-3-Clause",
    source_root="src/click",
    test_root="tests",
    test_dependencies=("pytest",),
    # click deselects 31,000 stress tests by default; without this the suite
    # never finishes.
    extra_pytest_args=("-m", "not stress"),
    selection_rationale=(
        "4,863 statements at 85% coverage with a 2.5s suite, and the most reverts of any "
        "candidate examined (22). CLI argument parsing is dense with boundary conditions - "
        "option counts, nargs, type coercion - where an off-by-one changes behaviour for "
        "one input and nothing else, which is the regression shape this project targets."
    ),
)

JSONSCHEMA = TargetRepo(
    slug="jsonschema",
    owner="python-jsonschema",
    name="jsonschema",
    ref="v4.26.0",
    commit="a7277432b0f7bcd0551f6e589d30457017125df4",
    spdx_license="MIT",
    source_root="jsonschema",
    test_root="jsonschema/tests",
    test_dependencies=(
        "pytest",
        "attrs",
        "referencing",
        "rpds-py",
        "jsonschema-specifications",
        "jsonpath-ng",
    ),
    selection_rationale=(
        "A validator implementing a written specification, so correctness is externally "
        "defined rather than a matter of taste. Validation logic fails silently by "
        "construction - an accepted document that should have been rejected produces no "
        "error anywhere - which makes surviving mutants unusually meaningful."
    ),
)

LOGURU = TargetRepo(
    slug="loguru",
    owner="Delgan",
    name="loguru",
    ref="0.7.3",
    commit="ae3bfd1b85b6b4a3db535f69b975687c79498be4",
    spdx_license="MIT",
    source_root="loguru",
    test_root="tests",
    test_dependencies=(
        "pytest",
        "pytest-mock",
        "freezegun",
        "colorama",
        "pytest-mypy-plugins",
        "mypy",
    ),
    selection_rationale=(
        "Logging is the canonical place where a regression goes unnoticed: output still "
        "appears, just wrong - a dropped field, a mangled level, a filter that stops "
        "filtering. Heavy formatting and filtering logic gives mutation plenty of surface, "
        "and the suite is fast."
    ),
)

ATTRS = TargetRepo(
    slug="attrs",
    owner="python-attrs",
    name="attrs",
    ref="26.1.0",
    commit="7bfc49e9b22d5ba25b6e429524c3d49fee27cb36",
    spdx_license="MIT",
    source_root="src/attr",
    test_root="tests",
    test_dependencies=("pytest", "hypothesis"),
    selection_rationale=(
        "Class-generation metaprogramming, with 24 reverts in its history - the most of any "
        "candidate. Its output is other code, so a mutation changes the classes users get "
        "rather than a value they inspect, and the failure surfaces far from its cause."
    ),
)

TARGETS: dict[str, TargetRepo] = {
    t.slug: t for t in (MARSHMALLOW, ARROW, CLICK, JSONSCHEMA, LOGURU, ATTRS)
}


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
