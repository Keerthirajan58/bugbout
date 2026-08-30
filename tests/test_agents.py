"""Tests for the Prosecutor, the Skeptic's contract, and the eval harness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from precedent.agents.context import build_context
from precedent.agents.prosecutor import Accusation, Prosecutor
from precedent.agents.skeptic import SkepticVerdict
from precedent.eval.case import CaseOutcome
from precedent.eval.harness import stats_from
from precedent.llm.cache import ResponseCache
from precedent.llm.router import LLMRouter
from precedent.mutation.mutant import MutantSpec
from tests.conftest import FakeProvider

SOURCE = '''\
def set_value(dct, key, value):
    """Set a value in a nested dict."""
    if "." in key:
        head, rest = key.split(".", 2)
        target = dct.setdefault(head, {})
        set_value(target, rest, value)
    else:
        dct[key] = value
'''

GOOD_RESPONSE = json.dumps(
    {
        "hypothesis": "A three-segment key raises ValueError instead of nesting",
        "reasoning": "split with maxsplit=2 yields three parts for two targets.",
        "test_name": "test_three_segment_key",
        "test_code": "def test_three_segment_key():\n    assert True\n",
        "confident": True,
    }
)


def _spec() -> MutantSpec:
    return MutantSpec(
        mutant_id="abc123def456",
        target_slug="marshmallow",
        target_commit="0" * 40,
        module_path="src/marshmallow/utils.py",
        operator="core/NumberReplacer",
        family="constants",
        occurrence=0,
        start_line=4,
        start_col=0,
        end_line=4,
        end_col=10,
        function="set_value",
    )


def _router(*responses: str, cache_root: Path) -> LLMRouter:
    return LLMRouter(
        [FakeProvider(responses=list(responses))], cache=ResponseCache(root=cache_root)
    )


class TestContext:
    def test_extracts_the_enclosing_function(self) -> None:
        context = build_context(_spec(), SOURCE, "diff")
        assert "def set_value" in context.enclosing_source
        assert "dct[key] = value" in context.enclosing_source

    def test_lists_public_names_for_legitimate_imports(self) -> None:
        """The Prosecutor is told what it may import, so it tests the public API
        instead of reaching into private internals."""
        assert "set_value" in build_context(_spec(), SOURCE, "diff").public_names

    def test_render_includes_the_diff_and_the_code(self) -> None:
        rendered = build_context(_spec(), SOURCE, "@@ -1 +1 @@").render()
        assert "@@ -1 +1 @@" in rendered
        assert "src/marshmallow/utils.py" in rendered
        assert "set_value" in rendered

    def test_unparseable_source_still_yields_context(self) -> None:
        """A syntax error must degrade to a window, not abort the case."""
        assert build_context(_spec(), "def broken(:\n", "diff").enclosing_source


class TestProsecutor:
    def test_parses_a_well_formed_accusation(self, tmp_path: Path) -> None:
        prosecutor = Prosecutor(_router(GOOD_RESPONSE, cache_root=tmp_path))
        accusation = prosecutor.accuse(build_context(_spec(), SOURCE, "diff"))
        assert accusation.usable
        assert accusation.test_name == "test_three_segment_key"
        assert "ValueError" in accusation.hypothesis

    def test_a_malformed_response_is_recorded_not_raised(self, tmp_path: Path) -> None:
        """One bad response must not abort an evaluation of 454 mutants; it is
        simply a mutant the agent failed to catch."""
        prosecutor = Prosecutor(_router("not json at all", cache_root=tmp_path))
        accusation = prosecutor.accuse(build_context(_spec(), SOURCE, "diff"))
        assert not accusation.usable
        assert accusation.error is not None

    def test_a_provider_outage_is_recorded_not_raised(self, tmp_path: Path) -> None:
        router = LLMRouter(
            [FakeProvider(error=RuntimeError("down"))],
            cache=ResponseCache(root=tmp_path),
            max_attempts_per_provider=1,
        )
        accusation = Prosecutor(router).accuse(build_context(_spec(), SOURCE, "diff"))
        assert not accusation.usable
        assert accusation.error is not None

    def test_an_unconfident_accusation_is_not_executed(self, tmp_path: Path) -> None:
        """The Prosecutor is told a wrong accusation is worse than none. When it
        declines, we must honour that rather than run the test anyway."""
        response = json.dumps(
            {"hypothesis": "", "test_code": "def test_x(): pass", "confident": False}
        )
        accusation = Prosecutor(_router(response, cache_root=tmp_path)).accuse(
            build_context(_spec(), SOURCE, "diff")
        )
        assert not accusation.usable

    def test_phase_two_prompt_carries_no_retrieval(self, tmp_path: Path) -> None:
        """The control arm. Its prompt must contain only what is visible in the
        diff itself, or the ablation compares nothing."""
        prosecutor = Prosecutor(_router(GOOD_RESPONSE, cache_root=tmp_path))
        request = prosecutor.build_request(build_context(_spec(), SOURCE, "diff"))
        assert "RETRIEVED" not in request.user.upper()

    def test_retrieval_block_is_appended_when_supplied(self, tmp_path: Path) -> None:
        """Phase 3 differs from phase 2 in exactly this one respect."""
        prosecutor = Prosecutor(_router(GOOD_RESPONSE, cache_root=tmp_path))
        request = prosecutor.build_request(
            build_context(_spec(), SOURCE, "diff"),
            retrieval_block="RETRIEVED CONTEXT: incident #42",
        )
        assert "incident #42" in request.user

    def test_the_two_prompts_differ_only_by_the_retrieval_block(self, tmp_path: Path) -> None:
        prosecutor = Prosecutor(_router(GOOD_RESPONSE, cache_root=tmp_path))
        context = build_context(_spec(), SOURCE, "diff")
        without = prosecutor.build_request(context)
        with_ = prosecutor.build_request(context, retrieval_block="EXTRA")
        assert with_.system == without.system
        assert with_.user.startswith(without.user)


class TestVerdictContract:
    def test_only_confirmed_counts_as_caught(self) -> None:
        """Every other verdict is a miss. The Skeptic exists to make sure a
        confident-sounding accusation cannot be scored without execution."""
        assert SkepticVerdict(verdict="confirmed", reason="").caught
        for rejected in (
            "rejected_by_review",
            "rejected_guard",
            "rejected_invalid_on_original",
            "rejected_flaky",
            "rejected_error",
            "no_difference",
            "no_candidate",
        ):
            assert not SkepticVerdict(verdict=rejected, reason="").caught


class TestScoring:
    @staticmethod
    def _outcome(verdict: str, caught: bool) -> CaseOutcome:
        return CaseOutcome(
            mutant_id="x",
            target_slug="t",
            module_path="m.py",
            function="f",
            operator="op",
            family="constants",
            diff="",
            verdict=verdict,
            caught=caught,
            reason="",
        )

    def test_catch_rate_is_over_every_mutant_attempted(self) -> None:
        """Cases where the model failed stay in the denominator. Dropping them
        would let a flaky free-tier provider inflate the score."""
        outcomes = [
            self._outcome("confirmed", True),
            self._outcome("confirmed", True),
            self._outcome("no_difference", False),
            self._outcome("no_candidate", False),
        ]
        stats = stats_from(outcomes)
        assert stats.total == 4
        assert stats.caught == 2
        assert stats.catch_rate == pytest.approx(0.5)

    def test_empty_corpus_does_not_divide_by_zero(self) -> None:
        assert stats_from([]).catch_rate == 0.0

    def test_case_round_trips_through_a_dict(self) -> None:
        outcome = self._outcome("confirmed", True)
        assert CaseOutcome.from_dict(outcome.to_dict()) == outcome

    def test_accusation_failure_helper_is_unusable(self) -> None:
        assert not Accusation.failed("boom").usable
