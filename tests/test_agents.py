"""Tests for the Prosecutor, the Skeptic's contract, and the eval harness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import FakeProvider
from verdict.agents.change import CodeChange, FileVersion
from verdict.agents.context import build_context
from verdict.agents.prosecutor import Accusation, Prosecutor
from verdict.agents.skeptic import Skeptic, SkepticVerdict
from verdict.eval.case import CaseOutcome
from verdict.eval.harness import EvalHarness, stats_from
from verdict.eval.pool import AgentPool
from verdict.llm.cache import ResponseCache
from verdict.llm.router import LLMRouter
from verdict.mutation.mutant import MutantResult, MutantSpec

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


def _change(source: str = SOURCE, diff: str = "diff") -> CodeChange:
    """The Prosecutor's view of a change, shaped exactly as the harness builds it."""
    spec = _spec()
    return CodeChange(
        files=(FileVersion(spec.module_path, source, source),),
        module_path=spec.module_path,
        function=spec.function,
        start_line=spec.start_line,
        diff=diff,
    )


def _router(*responses: str, cache_root: Path) -> LLMRouter:
    return LLMRouter(
        [FakeProvider(responses=list(responses))], cache=ResponseCache(root=cache_root)
    )


class TestContext:
    def test_extracts_the_enclosing_function(self) -> None:
        context = build_context(_change())
        assert "def set_value" in context.enclosing_source
        assert "dct[key] = value" in context.enclosing_source

    def test_lists_public_names_for_legitimate_imports(self) -> None:
        """The Prosecutor is told what it may import, so it tests the public API
        instead of reaching into private internals."""
        assert "set_value" in build_context(_change()).public_names

    def test_render_includes_the_diff_and_the_code(self) -> None:
        rendered = build_context(_change(diff="@@ -1 +1 @@")).render()
        assert "@@ -1 +1 @@" in rendered
        assert "src/marshmallow/utils.py" in rendered
        assert "set_value" in rendered

    def test_unparseable_source_still_yields_context(self) -> None:
        """A syntax error must degrade to a window, not abort the case."""
        assert build_context(_change("def broken(:\n")).enclosing_source


class TestProsecutor:
    def test_parses_a_well_formed_accusation(self, tmp_path: Path) -> None:
        prosecutor = Prosecutor(_router(GOOD_RESPONSE, cache_root=tmp_path))
        accusation = prosecutor.accuse(build_context(_change()))
        assert accusation.usable
        assert accusation.test_name == "test_three_segment_key"
        assert "ValueError" in accusation.hypothesis

    def test_a_malformed_response_is_recorded_not_raised(self, tmp_path: Path) -> None:
        """One bad response must not abort an evaluation of 454 mutants; it is
        simply a mutant the agent failed to catch."""
        prosecutor = Prosecutor(_router("not json at all", cache_root=tmp_path))
        accusation = prosecutor.accuse(build_context(_change()))
        assert not accusation.usable
        assert accusation.error is not None

    def test_a_provider_outage_is_recorded_not_raised(self, tmp_path: Path) -> None:
        router = LLMRouter(
            [FakeProvider(error=RuntimeError("down"))],
            cache=ResponseCache(root=tmp_path),
            max_attempts_per_provider=1,
        )
        accusation = Prosecutor(router).accuse(build_context(_change()))
        assert not accusation.usable
        assert accusation.error is not None

    def test_an_unconfident_accusation_is_still_executed(self, tmp_path: Path) -> None:
        """Execution arbitrates, not the model's self-assessment.

        Letting a model veto its own test would put the verdict back in the
        model's hands, which is exactly what the Prosecutor/Skeptic split
        exists to prevent. A hedged test that provably passes on the original
        and fails on the change has caught the regression, whatever its author
        thought of it. Seen on the real corpus: two cases produced a complete
        test, flagged themselves unsure, and were discarded unrun.
        """
        response = json.dumps(
            {"hypothesis": "h", "test_code": "def test_x(): pass", "confident": False}
        )
        accusation = Prosecutor(_router(response, cache_root=tmp_path)).accuse(
            build_context(_change())
        )
        assert accusation.usable
        assert not accusation.confident  # recorded, just not disqualifying

    def test_an_accusation_with_no_test_is_not_executed(self, tmp_path: Path) -> None:
        """Genuinely declining - producing no test at all - is still honoured."""
        response = json.dumps({"hypothesis": "", "test_code": "", "confident": False})
        accusation = Prosecutor(_router(response, cache_root=tmp_path)).accuse(
            build_context(_change())
        )
        assert not accusation.usable

    def test_phase_two_prompt_carries_no_retrieval(self, tmp_path: Path) -> None:
        """The control arm. Its prompt must contain only what is visible in the
        diff itself, or the ablation compares nothing."""
        prosecutor = Prosecutor(_router(GOOD_RESPONSE, cache_root=tmp_path))
        request = prosecutor.build_request(build_context(_change()))
        assert "RETRIEVED" not in request.user.upper()

    def test_retrieval_block_is_appended_when_supplied(self, tmp_path: Path) -> None:
        """Phase 3 differs from phase 2 in exactly this one respect."""
        prosecutor = Prosecutor(_router(GOOD_RESPONSE, cache_root=tmp_path))
        request = prosecutor.build_request(
            build_context(_change()),
            retrieval_block="RETRIEVED CONTEXT: incident #42",
        )
        assert "incident #42" in request.user

    def test_the_two_prompts_differ_only_by_the_retrieval_block(self, tmp_path: Path) -> None:
        prosecutor = Prosecutor(_router(GOOD_RESPONSE, cache_root=tmp_path))
        context = build_context(_change())
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

    def adjudicate(self, workspace, change, accusation):  # type: ignore[no-untyped-def]
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


class TestCodeChange:
    """The change under review, decoupled from how it was produced.

    Mutants are the evaluation's ground truth, but the Prosecutor and Skeptic
    only ever need before-and-after source. Keeping that the interface is what
    lets the identical protocol audit a diff a coding agent wrote - with no
    branch, so the real-diff study cannot silently take a different path than
    the measured one.
    """

    @staticmethod
    def _versions(original: str, changed: str) -> list[FileVersion]:
        return [FileVersion("src/pkg/mod.py", original, changed)]

    def test_an_unmodified_file_is_not_a_change(self) -> None:
        assert CodeChange.from_sources(self._versions(SOURCE, SOURCE)) is None

    def test_the_enclosing_function_is_found_from_the_edited_line(self) -> None:
        change = CodeChange.from_sources(
            self._versions(SOURCE, SOURCE.replace("dct[key] = value", "dct[key] = None"))
        )
        assert change is not None
        assert change.function == "set_value"

    def test_the_primary_file_is_the_one_most_changed(self) -> None:
        """A hypothesis has to point somewhere. With nothing else to go on, the
        file carrying the most edited lines is the best available guess."""
        change = CodeChange.from_sources(
            [
                FileVersion("src/pkg/small.py", "a = 1\n", "a = 2\n"),
                FileVersion("src/pkg/big.py", "b = 1\nc = 2\nd = 3\n", "b = 9\nc = 9\nd = 9\n"),
            ]
        )
        assert change is not None
        assert change.module_path == "src/pkg/big.py"

    def test_the_generated_diff_shows_both_sides(self) -> None:
        change = CodeChange.from_sources(self._versions("x = 1\n", "x = 2\n"))
        assert change is not None
        assert "-x = 1" in change.diff
        assert "+x = 2" in change.diff

    def test_reverting_restores_every_touched_file(self, tmp_path: Path) -> None:
        """A leaked edit would corrupt every verdict scored afterwards in the
        same workspace, and would do it silently."""

        class _Workspace:
            repo_dir = tmp_path

        for name in ("one.py", "two.py"):
            (tmp_path / name).write_text("original\n", encoding="utf-8")
        change = CodeChange.from_sources(
            [FileVersion(n, "original\n", "changed\n") for n in ("one.py", "two.py")]
        )
        assert change is not None
        change.apply(_Workspace())  # type: ignore[arg-type]
        assert (tmp_path / "two.py").read_text() == "changed\n"
        change.revert(_Workspace())  # type: ignore[arg-type]
        assert [(tmp_path / n).read_text() for n in ("one.py", "two.py")] == ["original\n"] * 2


class TestGroundingGate:
    """Counting candidates that were wrong about the library, not the change.

    A candidate test must pass against the UNMUTATED code before it may be
    evidence about a diff. That rule already decided verdicts; what was missing
    was a count. The first ablation could only say "16 cases ended as
    rejected_invalid_on_original", which conflates a mutant whose first draft
    was wrong and was then repaired with one that was wrong twice. The
    false-positive rate is the interesting number, and it needs per-attempt
    accounting to exist at all.
    """

    def test_a_clean_first_candidate_records_no_gate_failure(self, survivor) -> None:  # type: ignore[no-untyped-def]
        harness = EvalHarness(_StubProsecutor(_usable()), _StubSkeptic("confirmed"))  # type: ignore[arg-type]
        assert harness.run_one(_FakeWorkspace(), survivor).gate_failures == 0

    def test_a_repaired_candidate_still_records_its_first_rejection(self, survivor) -> None:  # type: ignore[no-untyped-def]
        """The catch counts, and so does the false positive that preceded it."""
        harness = EvalHarness(  # type: ignore[arg-type]
            _StubProsecutor(_usable(), _usable("t2")),
            _StubSkeptic("rejected_invalid_on_original", "confirmed"),
        )
        outcome = harness.run_one(_FakeWorkspace(), survivor)
        assert outcome.verdict == "confirmed"
        assert outcome.gate_failures == 1

    def test_both_attempts_failing_the_gate_are_counted(self, survivor) -> None:  # type: ignore[no-untyped-def]
        harness = EvalHarness(  # type: ignore[arg-type]
            _StubProsecutor(_usable(), _usable("t2")),
            _StubSkeptic("rejected_invalid_on_original"),
        )
        assert harness.run_one(_FakeWorkspace(), survivor).gate_failures == 2

    def test_a_test_that_never_ran_is_not_a_gate_failure(self, survivor) -> None:  # type: ignore[no-untyped-def]
        """`rejected_error` means the candidate never executed, so it says
        nothing about whether the model understood the library. Folding it into
        the gate count would inflate the false-positive rate with provider
        noise."""
        harness = EvalHarness(_StubProsecutor(_usable()), _StubSkeptic("rejected_error"))  # type: ignore[arg-type]
        assert harness.run_one(_FakeWorkspace(), survivor).gate_failures == 0


class TestDiffPolarity:
    """The Prosecutor must name both sides of the change before writing a test.

    Three of the four catches retrieval lost in the first ablation were
    direction inversions: the model described the *mutant's* behaviour as the
    original's, so its assertion could only ever fail against unchanged code.
    Requiring the two predictions as separate fields makes the polarity explicit
    while the model can still correct it, and records it so the failure mode is
    countable afterwards rather than only visible by reading test bodies.
    """

    def test_both_predictions_are_recorded(self, tmp_path: Path) -> None:
        response = json.dumps(
            {
                "original_behaviour": "returns 3",
                "changed_behaviour": "returns 1",
                "hypothesis": "h",
                "reasoning": "r",
                "test_name": "test_x",
                "test_code": "def test_x():\n    assert True\n",
                "confident": True,
            }
        )
        accusation = Prosecutor(_router(response, cache_root=tmp_path)).accuse(
            build_context(_change())
        )
        assert accusation.original_behaviour == "returns 3"
        assert accusation.changed_behaviour == "returns 1"

    def test_a_response_omitting_them_still_parses(self, tmp_path: Path) -> None:
        """Free-tier models drop fields. A missing prediction must not discard
        an otherwise usable test - execution, not form, is the arbiter."""
        accusation = Prosecutor(_router(GOOD_RESPONSE, cache_root=tmp_path)).accuse(
            build_context(_change())
        )
        assert accusation.usable
        assert accusation.original_behaviour == ""

    def test_the_prompt_states_which_side_of_the_diff_to_assert(self, tmp_path: Path) -> None:
        """The instruction is the fix; if it is edited away the inversions
        return, and nothing else in the suite would notice."""
        request = Prosecutor(_router(GOOD_RESPONSE, cache_root=tmp_path)).build_request(
            build_context(_change())
        )
        assert "ORIGINAL (`-`)" in request.system
        assert "AS IT IS AFTER THE CHANGE" in request.system


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


class TestPairedAssignment:
    """Routing the second arm back to the first arm's models.

    In the first phase 3 run only 41 of 115 mutants met the same model in both
    arms, and every one of the 12 regressions the retrieval arm appeared to
    lose had swapped models. The aggregate looked like retrieval hurting; it
    was a measurement artefact.
    """

    @staticmethod
    def _pool(models: tuple[str, ...]) -> AgentPool:
        pairs = tuple(
            (
                Prosecutor(LLMRouter([FakeProvider(model=m)], cache=ResponseCache(enabled=False))),
                Skeptic(None, review_enabled=False),
            )
            for m in models
        )
        return AgentPool(pairs=pairs)

    def test_a_preference_routes_to_that_model(self) -> None:
        pool = self._pool(("a", "b", "c", "d")).with_preferences({"m1": "c"})
        assert pool.model_for("m1") == "c"

    def test_mutants_without_a_preference_still_hash(self) -> None:
        base = self._pool(("a", "b", "c", "d"))
        pinned = base.with_preferences({"other": "c"})
        assert pinned.model_for("m2") == base.model_for("m2")

    def test_an_unknown_model_falls_back_to_hashing(self) -> None:
        """A preference naming a model no longer in the pool must not crash the
        run - it degrades to the default assignment."""
        base = self._pool(("a", "b"))
        pinned = base.with_preferences({"m1": "retired-model"})
        assert pinned.model_for("m1") == base.model_for("m1")

    def test_preferences_do_not_mutate_the_original_pool(self) -> None:
        base = self._pool(("a", "b"))
        base.with_preferences({"m1": "b"})
        assert base.preferred == {}
