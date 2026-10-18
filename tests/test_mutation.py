"""Tests for the mutation harness.

The classification tests are the important ones. Misclassifying a mutant does
not raise, does not fail a run, and does not look wrong in the output - it just
shifts the kill-rate. An early version of this harness scored 96 mutants as
"invalid" when the suite had in fact detected every one of them by failing to
import, which understated the kill-rate by 1.8 points. Those cases are pinned
here so the mistake cannot come back silently.
"""

from __future__ import annotations

import cosmic_ray.plugins as plugins
import pytest

from verdict.mutation.mutant import MutantResult, MutantSpec, make_unified_diff
from verdict.mutation.operators import (
    DEFAULT_OPERATORS,
    EXCLUDED_WITH_REASON,
    OPERATOR_FAMILIES,
    family_of,
)
from verdict.mutation.runner import MutationRunner, RunStats, stats_from
from verdict.targets.workspace import SuiteResult


def _spec(module_path: str = "src/pkg/mod.py", operator: str = "core/AddNot") -> MutantSpec:
    return MutantSpec(
        mutant_id="deadbeef0000",
        target_slug="demo",
        target_commit="0" * 40,
        module_path=module_path,
        operator=operator,
        family=family_of(operator),
        occurrence=0,
        start_line=1,
        start_col=0,
        end_line=1,
        end_col=4,
        function="fn",
    )


def _run(
    exit_code: int, *, stdout: str = "", stderr: str = "", timed_out: bool = False
) -> SuiteResult:
    return SuiteResult(
        exit_code=exit_code,
        passed=0,
        failed=0,
        errors=0,
        skipped=0,
        duration_seconds=0.1,
        timed_out=timed_out,
        stdout=stdout,
        stderr=stderr,
    )


class TestOperatorPolicy:
    def test_every_selected_operator_exists_in_cosmic_ray(self) -> None:
        """Catches typos and operator renames on a cosmic-ray upgrade, which
        would otherwise silently shrink the mutant population."""
        known = set(plugins.operator_names())
        assert not [op for op in DEFAULT_OPERATORS if op not in known]

    def test_selection_is_a_strict_subset(self) -> None:
        assert 0 < len(DEFAULT_OPERATORS) < len(list(plugins.operator_names()))

    def test_no_duplicates(self) -> None:
        assert len(DEFAULT_OPERATORS) == len(set(DEFAULT_OPERATORS))

    def test_every_operator_belongs_to_exactly_one_family(self) -> None:
        for operator in DEFAULT_OPERATORS:
            memberships = [f for f, members in OPERATOR_FAMILIES.items() if operator in members]
            assert len(memberships) == 1, f"{operator} is in {memberships}"

    def test_families_cover_the_whole_selection(self) -> None:
        flattened = {op for members in OPERATOR_FAMILIES.values() for op in members}
        assert flattened == set(DEFAULT_OPERATORS)

    def test_nonsense_cross_family_operators_are_excluded(self) -> None:
        """`a + b -> a >> b` raises TypeError on most operands. Including these
        would be a free kill that overstates how good a test suite is."""
        for operator in (
            "core/ReplaceBinaryOperator_Add_RShift",
            "core/ReplaceBinaryOperator_Mul_BitOr",
        ):
            assert operator not in DEFAULT_OPERATORS

    def test_ordering_identity_confusions_are_excluded(self) -> None:
        assert "core/ReplaceComparisonOperator_Lt_Is" not in DEFAULT_OPERATORS

    def test_boundary_operators_are_included(self) -> None:
        """`<` vs `<=` is the canonical silent regression and must be covered."""
        for operator in (
            "core/ReplaceComparisonOperator_Lt_LtE",
            "core/ReplaceComparisonOperator_Gt_GtE",
        ):
            assert operator in DEFAULT_OPERATORS

    def test_exclusions_are_justified_in_writing(self) -> None:
        for operator, reason in EXCLUDED_WITH_REASON.items():
            assert operator not in DEFAULT_OPERATORS
            assert len(reason) > 20


class TestMutantIdentity:
    def test_ids_are_deterministic(self) -> None:
        """cosmic-ray assigns uuid4 job ids. If we did the same, the two
        ablation arms could not be joined mutant-by-mutant."""
        args = ("marshmallow", "a" * 40, "src/m/fields.py", "core/AddNot", 7)
        assert MutantSpec.make_id(*args) == MutantSpec.make_id(*args)

    @pytest.mark.parametrize("index", range(5))
    def test_id_depends_on_every_component(self, index: int) -> None:
        base = ["marshmallow", "a" * 40, "src/m/fields.py", "core/AddNot", 7]
        changed = list(base)
        changed[index] = "different" if index != 4 else 8
        assert MutantSpec.make_id(*base) != MutantSpec.make_id(*changed)  # type: ignore[arg-type]

    def test_round_trips_through_a_dict(self) -> None:
        spec = _spec()
        assert MutantSpec.from_dict(spec.to_dict()) == spec


class TestClassification:
    def test_all_tests_passing_is_a_survivor(self) -> None:
        assert MutationRunner._classify(_spec(), _run(0)) == "survived"

    def test_a_failing_test_kills(self) -> None:
        assert MutationRunner._classify(_spec(), _run(1)) == "killed"

    def test_a_hang_counts_as_detection(self) -> None:
        """A mutated loop bound that never terminates is caught: the suite
        would never go green on that change."""
        assert MutationRunner._classify(_spec(), _run(2, timed_out=True)) == "timeout"

    def test_import_failure_naming_the_mutated_module_is_detection(self) -> None:
        """Regression test. This was previously scored 'incompetent' and
        dropped, understating marshmallow's kill-rate by 1.8 points across 96
        mutants. The mutation broke the module so the suite could not even be
        collected - which is the loudest possible detection."""
        stderr = (
            "tests/conftest.py:5: in <module>\n"
            "src/pkg/mod.py:1640: in __init__\n"
            'E   ValueError: "values" must be a subclass of Field.'
        )
        assert MutationRunner._classify(_spec(), _run(4, stderr=stderr)) == "killed_import"

    def test_generic_collection_error_is_detection(self) -> None:
        result = _run(2, stdout="ERROR collecting tests/test_x.py")
        assert MutationRunner._classify(_spec(), result) == "killed_import"

    def test_unexplained_failure_stays_visible_as_incompetent(self) -> None:
        """An error we cannot attribute to the mutation must not be quietly
        folded into a result bucket - that would hide harness problems."""
        assert MutationRunner._classify(_spec(), _run(3, stdout="something odd")) == "incompetent"

    def test_extracts_the_killing_test(self) -> None:
        output = "FAILED tests/test_fields.py::test_dict_values - AssertionError"
        assert MutationRunner._killing_test(output) == "tests/test_fields.py::test_dict_values"

    def test_killing_test_is_none_when_absent(self) -> None:
        assert MutationRunner._killing_test("1188 passed") is None


class TestScoring:
    @staticmethod
    def _stats(**kwargs: int) -> RunStats:
        base = {
            "killed": 0,
            "killed_import": 0,
            "survived": 0,
            "timeout": 0,
            "incompetent": 0,
            "no_mutation": 0,
        }
        base.update(kwargs)
        return RunStats(total=sum(base.values()), **base)  # type: ignore[arg-type]

    def test_kill_rate_counts_every_form_of_detection(self) -> None:
        stats = self._stats(killed=70, killed_import=10, timeout=5, survived=15)
        assert stats.detected == 85
        assert stats.scored == 100
        assert stats.kill_rate == pytest.approx(0.85)

    def test_test_only_kill_rate_excludes_import_kills(self) -> None:
        """The stricter reading, reported alongside so the headline number
        cannot be accused of leaning on trivially-detected mutants."""
        stats = self._stats(killed=70, killed_import=10, timeout=5, survived=15)
        assert stats.test_kill_rate == pytest.approx(0.75)

    def test_unapplied_mutants_are_outside_the_denominator(self) -> None:
        """Mutants that never produced a change say nothing about test quality;
        counting them would let harness noise move the headline number."""
        stats = self._stats(killed=50, survived=50, no_mutation=999, incompetent=999)
        assert stats.scored == 100
        assert stats.kill_rate == pytest.approx(0.5)

    def test_empty_run_does_not_divide_by_zero(self) -> None:
        assert self._stats().kill_rate == 0.0

    def test_stats_are_derived_from_results(self) -> None:
        results = [
            MutantResult(_spec(), status, 0.1, 0, 0, None, "")
            for status in ("killed", "killed", "survived", "timeout", "killed_import")
        ]
        stats = stats_from(results)
        assert (stats.killed, stats.survived, stats.timeout, stats.killed_import) == (2, 1, 1, 1)
        assert stats.kill_rate == pytest.approx(0.8)


class TestDiff:
    def test_produces_standard_unified_diff(self) -> None:
        diff = make_unified_diff("if x is None:\n", "if x is not None:\n", "src/pkg/mod.py")
        assert "--- a/src/pkg/mod.py" in diff
        assert "+++ b/src/pkg/mod.py" in diff
        assert "-if x is None:" in diff
        assert "+if x is not None:" in diff

    def test_identical_input_produces_no_diff(self) -> None:
        assert make_unified_diff("same\n", "same\n", "src/pkg/mod.py") == ""
