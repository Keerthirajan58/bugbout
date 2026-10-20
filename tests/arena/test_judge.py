"""The judge's rules, each pinned on a synthetic matrix."""

from __future__ import annotations

import pytest

from bugbout.arena.judge import POLICIES, Judge, Verdict, get_policy
from bugbout.arena.matrix import BASE, Matrix
from bugbout.errors import ConfigError

P, F = "passed", "failed"
CLAUDE, CODEX, THIRD = "claude-code/sonnet", "codex/gpt-5", "codex/gpt-5-mini"
EXAMINER = "reproduce/gemini-cli/gemini-2.5-pro"
REPRO = "tests.test_bugbout__reproduce_gemini::test_bug"
VENDORS = {CLAUDE: "anthropic", CODEX: "openai", THIRD: "openai", EXAMINER: "google"}


def _matrix(outcomes: dict[str, dict[str, str]], exhibits: dict[str, str] | None = None) -> Matrix:
    exhibits = exhibits if exhibits is not None else {EXAMINER: "test_bugbout__reproduce_gemini"}
    trees = [BASE, *sorted(t for t in outcomes if t != BASE)]
    return Matrix(
        task_id="t",
        trees=trees,
        exhibits=exhibits,
        kinds={a: "examiner" if a == EXAMINER else "contestant" for a in exhibits},
        vendors=VENDORS,
        outcomes=outcomes,
        lines_changed=dict.fromkeys(trees, 10),
    )


def test_the_independently_supported_patch_wins() -> None:
    outcomes = {
        BASE: {REPRO: F, "tests.test_x::test_ok": P},
        CLAUDE: {REPRO: P, "tests.test_x::test_ok": P},
        CODEX: {REPRO: F, "tests.test_x::test_ok": P},
    }
    verdict = Judge()(_matrix(outcomes))
    assert verdict.chosen == CLAUDE
    assert {s.entrant: s.gate for s in verdict.standings} == {
        CLAUDE: "approved",
        CODEX: "rejected",
    }


def test_a_test_that_already_passes_on_the_base_is_not_a_reproduction() -> None:
    weak = "tests.test_bugbout__reproduce_gemini::test_trivially_true"
    verdict = Judge()(_matrix({BASE: {weak: P}, CLAUDE: {weak: P}, CODEX: {weak: P}}))
    assert verdict.chosen is None and verdict.invalid_exhibits == (EXAMINER,)
    assert all(s.gate == "unverified" for s in verdict.standings)


def test_a_contestant_s_test_never_vouches_for_its_own_fix() -> None:
    own = "tests.test_bugbout__claude_code_sonnet::test_bug"
    exhibits = {CLAUDE: "test_bugbout__claude_code_sonnet"}
    verdict = Judge()(_matrix({BASE: {own: F}, CLAUDE: {own: P}, CODEX: {own: P}}, exhibits))
    by_name = {s.entrant: s for s in verdict.standings}
    assert by_name[CLAUDE].support is None and by_name[CODEX].support == 1.0
    assert verdict.chosen == CODEX


def test_under_cross_vendor_one_vendor_cannot_vouch_for_another_of_its_models() -> None:
    own = "tests.test_bugbout__codex_gpt_5::test_bug"
    exhibits = {CODEX: "test_bugbout__codex_gpt_5"}
    outcomes = {BASE: {own: F}, CODEX: {own: P}, THIRD: {own: P}}
    assert Judge("majority")(_matrix(outcomes, exhibits)).chosen == THIRD
    assert Judge("cross-vendor")(_matrix(outcomes, exhibits)).chosen is None


def test_breaking_a_test_counts_only_when_the_others_keep_it() -> None:
    kept, changed = "tests.test_x::test_kept_by_others", "tests.test_x::test_everyone_changes"
    outcomes = {
        BASE: {REPRO: F, kept: P, changed: P},
        CLAUDE: {REPRO: P, kept: F, changed: F},
        CODEX: {REPRO: P, kept: P, changed: F},
        THIRD: {REPRO: P, kept: P, changed: F},
    }
    verdict = Judge()(_matrix(outcomes))
    by_name = {s.entrant: s for s in verdict.standings}
    assert by_name[CLAUDE].breaks == (kept,)
    # Every candidate fails `changed`: that is the issue's behaviour change, not a break.
    assert by_name[CODEX].breaks == () and verdict.chosen in (CODEX, THIRD)


def test_it_abstains_when_nothing_reaches_the_threshold() -> None:
    a, b, c = (f"tests.test_bugbout__reproduce_gemini::test_{n}" for n in "abc")
    verdict = Judge()(_matrix({BASE: {a: F, b: F, c: F}, CODEX: {a: P, b: F, c: F}}))
    assert verdict.chosen is None and "50%" in verdict.reason


def test_unanimous_needs_every_reproduction() -> None:
    a, b = (f"tests.test_bugbout__reproduce_gemini::test_{n}" for n in "ab")
    outcomes = {BASE: {a: F, b: F}, CODEX: {a: P, b: F}, CLAUDE: {a: P, b: P}}
    assert Judge("unanimous")(_matrix(outcomes)).chosen == CLAUDE
    assert Judge("unanimous")(_matrix({BASE: {a: F, b: F}, CODEX: {a: P, b: F}})).chosen is None


def test_strict_blocks_a_patch_that_breaks_an_examiner_s_invariant() -> None:
    keep = "tests.test_bugbout__reproduce_gemini::test_unrelated_behaviour"
    outcomes = {BASE: {REPRO: F, keep: P}, CODEX: {REPRO: P, keep: F}}
    assert Judge("unanimous")(_matrix(outcomes)).chosen == CODEX
    assert Judge("strict")(_matrix(outcomes)).chosen is None


def test_class_based_tests_belong_to_their_exhibit() -> None:
    in_class = "tests.test_bugbout__reproduce_gemini.TestBug::test_it"
    matrix = _matrix({BASE: {in_class: F}, CODEX: {in_class: P}})
    assert matrix.author_of(in_class) == EXAMINER
    assert matrix.author_of("tests.test_core::test_add") is None


def test_with_no_results_there_is_no_verdict() -> None:
    assert Judge()(_matrix({BASE: {}})).reason == "the base tree produced no results"
    assert Judge()(_matrix({BASE: {REPRO: F}})).reason == "no candidate fix produced results"


def test_verdicts_and_matrices_round_trip() -> None:
    outcomes = {BASE: {REPRO: F, "tests.a::t": P}, CLAUDE: {REPRO: P, "tests.a::t": "error"}}
    matrix = _matrix(outcomes)
    assert Matrix.from_dict(matrix.to_dict()) == matrix
    verdict = Judge()(matrix)
    assert Verdict.from_dict(verdict.to_dict()) == verdict


def test_an_unknown_exhibit_kind_fails_loudly() -> None:
    data = _matrix({BASE: {REPRO: F}}).to_dict()
    data["kinds"] = {EXAMINER: "blind"}
    with pytest.raises(ValueError, match="unknown exhibit kind"):
        Matrix.from_dict(data)


def test_policies_are_named() -> None:
    assert set(POLICIES) == {"majority", "unanimous", "strict", "cross-vendor"}
    with pytest.raises(ConfigError, match="unknown judge policy"):
        get_policy("lenient")
