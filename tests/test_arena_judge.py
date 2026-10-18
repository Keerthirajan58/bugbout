"""The judge's four rules, each pinned on a synthetic matrix."""

from __future__ import annotations

from verdict.arena.judge import judge
from verdict.arena.matrix import BASE, Matrix, family_of

P, F = "passed", "failed"
CLAUDE, CODEX, GEMINI = "claude-code", "codex/gpt-6.1-sol", "examiner/gemini-flash"


def _matrix(outcomes: dict[str, dict[str, str]], exhibits: dict[str, str]) -> Matrix:
    trees = [BASE, *sorted(t for t in outcomes if t != BASE)]
    return Matrix("t", trees, exhibits, outcomes, {t: 10 for t in trees})


def _exhibits() -> dict[str, str]:
    return {GEMINI: "test_verdict__gemini", CLAUDE: "test_verdict__claude"}


def test_vendors_are_recognised() -> None:
    assert family_of(CLAUDE) == "anthropic"
    assert family_of(CODEX) == "openai"
    assert family_of("mini-swe-agent/gpt-oss:20b") == "openai"
    assert family_of(GEMINI) == "google"


def test_the_independently_supported_patch_wins() -> None:
    repro = "tests.test_verdict__gemini::test_bug"
    outcomes = {
        BASE: {repro: F, "tests.test_x::test_ok": P},
        CLAUDE: {repro: P, "tests.test_x::test_ok": P},
        CODEX: {repro: F, "tests.test_x::test_ok": P},
    }
    verdict = judge(_matrix(outcomes, _exhibits()))
    assert verdict.chosen == CLAUDE


def test_a_test_that_already_passes_on_base_is_not_a_reproduction() -> None:
    weak = "tests.test_verdict__gemini::test_trivially_true"
    outcomes = {BASE: {weak: P}, CLAUDE: {weak: P}, CODEX: {weak: P}}
    verdict = judge(_matrix(outcomes, _exhibits()))
    assert verdict.chosen is None
    assert GEMINI in verdict.invalid_exhibits


def test_a_patch_is_never_supported_by_its_own_vendor() -> None:
    own = "tests.test_verdict__claude::test_bug"
    outcomes = {BASE: {own: F}, CLAUDE: {own: P}}
    verdict = judge(_matrix(outcomes, _exhibits()))
    # Claude's own reproduction is the only evidence, and it cannot vouch for Claude.
    assert verdict.chosen is None
    assert verdict.standings[0].support is None


def test_breaking_a_test_counts_only_when_the_others_keep_it() -> None:
    repro = "tests.test_verdict__gemini::test_bug"
    kept = "tests.test_x::test_kept_by_others"
    changed = "tests.test_x::test_everyone_changes"
    third = "codex/gpt-5.6-luna"
    outcomes = {
        BASE: {repro: F, kept: P, changed: P},
        CLAUDE: {repro: P, kept: F, changed: F},
        CODEX: {repro: P, kept: P, changed: F},
        third: {repro: P, kept: P, changed: F},
    }
    verdict = judge(_matrix(outcomes, _exhibits()))
    by_name = {s.entrant: s for s in verdict.standings}
    assert by_name[CLAUDE].breaks == (kept,)
    # Every candidate fails `changed`: that is the issue's behaviour change, not a break.
    assert by_name[CODEX].breaks == ()
    assert verdict.chosen in (CODEX, third)


def test_abstains_when_nothing_clears_half_the_evidence() -> None:
    a = "tests.test_verdict__gemini::test_a"
    b = "tests.test_verdict__gemini::test_b"
    c = "tests.test_verdict__gemini::test_c"
    outcomes = {BASE: {a: F, b: F, c: F}, CODEX: {a: P, b: F, c: F}}
    verdict = judge(_matrix(outcomes, _exhibits()))
    assert verdict.chosen is None
    assert "half" in verdict.reason
