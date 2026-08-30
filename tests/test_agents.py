"""Tests for the Prosecutor, the Skeptic's contract, and the eval harness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from precedent.agents.context import build_context
from precedent.agents.prosecutor import Accusation, Prosecutor
from precedent.agents.skeptic import Skeptic, SkepticVerdict
from precedent.eval.case import CaseOutcome
from precedent.eval.harness import EvalHarness, stats_from
from precedent.eval.pool import AgentPool
from precedent.llm.cache import ResponseCache
from precedent.llm.router import LLMRouter
from precedent.mutation.mutant import MutantResult, MutantSpec
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


class _FakeWorkspace:
    """Stands in for a checkout. The stub Skeptic never touches disk."""

    class _Repo:
        def __truediv__(self, other: str) -> _FakeWorkspace._Path:
            return _FakeWorkspace._Path()

    class _Path:
        def read_text(self, encoding: str = "utf-8") -> str:
            return SOURCE

    repo_dir = _Repo()


class _StubRetrieval:
    def block_for(self, result):  # type: ignore[no-untyped-def]
        return "RETRIEVED CONTEXT: incident:abc", [{"citation": "incident:abc"}]


@pytest.fixture
def survivor() -> MutantResult:
    return MutantResult(
        spec=_spec(),
        status="survived",
        duration_seconds=0.1,
        tests_passed=1,
        tests_failed=0,
        killing_test=None,
        diff="@@ -1 +1 @@\n-a\n+b\n",
    )


class _StubProsecutor:
    """Returns scripted accusations and records how it was called."""

    def __init__(self, first: Accusation, revised: Accusation | None = None) -> None:
        self.first = first
        self.revised = revised
        self.accuse_calls = 0
        self.revise_calls = 0
        self.revise_blocks: list[str] = []

    def accuse(self, context, *, retrieval_block: str = ""):  # type: ignore[no-untyped-def]
        self.accuse_calls += 1
        return self.first

    def revise(self, context, previous, failure_output, *, retrieval_block: str = ""):  # type: ignore[no-untyped-def]
        self.revise_calls += 1
        self.revise_blocks.append(retrieval_block)
        return self.revised or self.first


class _StubSkeptic:
    """Returns scripted verdicts in order."""

    def __init__(self, *verdicts: str) -> None:
        self.verdicts = list(verdicts)
        self.calls = 0

    def adjudicate(self, workspace, spec, accusation):  # type: ignore[no-untyped-def]
        self.calls += 1
        verdict = self.verdicts[min(self.calls - 1, len(self.verdicts) - 1)]
        return SkepticVerdict(verdict=verdict, reason=verdict)


def _usable(name: str = "t") -> Accusation:
    return Accusation(
        hypothesis="h",
        reasoning="r",
        test_name=name,
        test_code=f"def {name}():\n    assert True\n",
        confident=True,
        provider="fake",
        model="fake",
        cached=False,
        latency_seconds=0.0,
        raw="",
    )


class TestRepairLoop:
    """One bounded repair attempt.

    A test that does not run against the unchanged code says nothing about the
    diff, and discarding it outright measures the model's first-draft syntax
    rather than whether it understood the change. Real review agents iterate.

    The bound matters just as much: an unbounded loop would let a model grind
    against the library until something incidentally differs, which is a much
    weaker claim than catching the regression. Both ablation arms get exactly
    the same allowance, so it cannot inflate the delta.
    """

    @staticmethod
    def _harness(prosecutor, skeptic, **kwargs):  # type: ignore[no-untyped-def]
        return EvalHarness(prosecutor, skeptic, **kwargs)  # type: ignore[arg-type]

    def _run(self, harness, result, monkeypatch=None):  # type: ignore[no-untyped-def]
        return harness.run_one(_FakeWorkspace(), result)

    def test_a_confirmed_test_is_never_repaired(self, survivor) -> None:  # type: ignore[no-untyped-def]
        prosecutor = _StubProsecutor(_usable())
        harness = self._harness(prosecutor, _StubSkeptic("confirmed"))
        outcome = self._run(harness, survivor)
        assert outcome.verdict == "confirmed"
        assert outcome.repairs == 0
        assert prosecutor.revise_calls == 0

    def test_a_test_failing_on_the_original_gets_one_repair(self, survivor) -> None:  # type: ignore[no-untyped-def]
        prosecutor = _StubProsecutor(_usable(), _usable("t2"))
        harness = self._harness(
            prosecutor, _StubSkeptic("rejected_invalid_on_original", "confirmed")
        )
        outcome = self._run(harness, survivor)
        assert outcome.verdict == "confirmed"
        assert outcome.repairs == 1
        assert prosecutor.revise_calls == 1

    def test_repairs_are_capped(self, survivor) -> None:  # type: ignore[no-untyped-def]
        """Never more than max_repairs, however many times it keeps failing."""
        prosecutor = _StubProsecutor(_usable(), _usable("t2"))
        harness = self._harness(
            prosecutor, _StubSkeptic("rejected_invalid_on_original"), max_repairs=1
        )
        outcome = self._run(harness, survivor)
        assert outcome.repairs == 1
        assert prosecutor.revise_calls == 1

    def test_repair_can_be_disabled(self, survivor) -> None:  # type: ignore[no-untyped-def]
        prosecutor = _StubProsecutor(_usable(), _usable("t2"))
        harness = self._harness(
            prosecutor, _StubSkeptic("rejected_invalid_on_original"), max_repairs=0
        )
        outcome = self._run(harness, survivor)
        assert outcome.repairs == 0
        assert prosecutor.revise_calls == 0

    def test_a_non_discriminating_test_is_not_repaired(self, survivor) -> None:  # type: ignore[no-untyped-def]
        """`no_difference` means the test ran fine and found nothing. Retrying
        would be fishing for any difference at all, not repairing a broken
        test - a materially weaker claim."""
        prosecutor = _StubProsecutor(_usable(), _usable("t2"))
        harness = self._harness(prosecutor, _StubSkeptic("no_difference", "confirmed"))
        outcome = self._run(harness, survivor)
        assert outcome.verdict == "no_difference"
        assert prosecutor.revise_calls == 0

    def test_a_review_rejection_is_not_repaired(self, survivor) -> None:  # type: ignore[no-untyped-def]
        """The Skeptic judged the test meaningless. Rewriting it would be an
        attempt to get around that judgement rather than to fix a broken test."""
        prosecutor = _StubProsecutor(_usable(), _usable("t2"))
        harness = self._harness(prosecutor, _StubSkeptic("rejected_by_review", "confirmed"))
        outcome = self._run(harness, survivor)
        assert outcome.verdict == "rejected_by_review"
        assert prosecutor.revise_calls == 0

    def test_the_repair_prompt_carries_the_same_retrieval_block(self, survivor) -> None:  # type: ignore[no-untyped-def]
        """Otherwise the retrieval arm would silently lose its context on the
        second attempt, understating what retrieval contributes."""
        prosecutor = _StubProsecutor(_usable(), _usable("t2"))
        harness = EvalHarness(  # type: ignore[arg-type]
            prosecutor,
            _StubSkeptic("rejected_invalid_on_original", "confirmed"),
            retrieval=_StubRetrieval(),
        )
        self._run(harness, survivor)
        assert prosecutor.revise_blocks == ["RETRIEVED CONTEXT: incident:abc"]


class TestAgentPool:
    """Deterministic model assignment.

    Free-tier limits force the evaluation across four model lanes. Assignment
    must depend only on the mutant id, so a mutant draws the same model in both
    arms and is compared against itself.
    """

    @staticmethod
    def _pool(n: int) -> AgentPool:
        pairs = tuple(
            (
                Prosecutor(
                    LLMRouter([FakeProvider(model=f"m{i}")], cache=ResponseCache(enabled=False))
                ),
                Skeptic(None, review_enabled=False),
            )
            for i in range(n)
        )
        return AgentPool(pairs=pairs)

    def test_assignment_is_stable_across_runs(self) -> None:
        pool = self._pool(4)
        assert pool.model_for("0183c16f1ccd") == pool.model_for("0183c16f1ccd")

    def test_assignment_does_not_depend_on_corpus_order(self) -> None:
        """Hashing the id rather than using its position means filtering or
        re-ordering the corpus cannot shift which model serves a mutant."""
        pool = self._pool(4)
        before = {m: pool.model_for(m) for m in ("aaa", "bbb", "ccc", "ddd")}
        after = {m: pool.model_for(m) for m in ("ddd", "ccc", "bbb", "aaa")}
        assert before == after

    def test_work_is_spread_across_lanes(self) -> None:
        pool = self._pool(4)
        spread = pool.distribution([f"mutant{i:04d}" for i in range(400)])
        assert len(spread) == 4
        assert all(count > 40 for count in spread.values())

    def test_a_single_lane_pool_always_returns_it(self) -> None:
        pool = self._pool(1)
        assert pool.index_for("anything") == 0

    def test_an_empty_pool_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least one agent pair"):
            AgentPool(pairs=())
