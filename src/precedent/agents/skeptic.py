"""The Skeptic: refuses to take the Prosecutor's word for anything.

The Skeptic's verdict comes from **execution**, never from argument. A candidate
test counts only if running it proves a real behavioural difference between the
original and changed code. This is the difference between a system that reports
what a language model believes and one that reports what actually happened.

The execution protocol, in order, and why each step exists:

1. **Static screen.** Reject tests that read source instead of exercising
   behaviour, or that reach outside the sandbox. (:mod:`precedent.sandbox.guards`)
2. **Run on the ORIGINAL code. It must pass.** If it fails here, the test is
   simply wrong - it is not evidence about the change. This is the single
   largest source of false positives, and catching it is most of the Skeptic's
   value.
3. **Run on the ORIGINAL code again. It must pass again.** A test that is
   sometimes green is not evidence of anything. Flaky candidates are discarded,
   not scored.
4. **Run on the CHANGED code. It must not pass.** Only now is a behavioural
   difference established.
5. **Adjudicate.** Execution can be satisfied by a test that technically
   differentiates while testing something incidental. A final model pass judges
   whether the test demonstrates the hypothesis it claimed. It can only ever
   *downgrade* a verdict - it can never promote something execution rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import cosmic_ray.plugins as plugins
from cosmic_ray.mutating import mutate_code

from precedent.agents.prosecutor import Accusation
from precedent.llm.base import LLMRequest
from precedent.llm.router import LLMRouter
from precedent.logging import get_logger
from precedent.mutation.mutant import MutantSpec
from precedent.sandbox.execution import ExecutionResult, SubprocessSandbox
from precedent.targets.workspace import TargetWorkspace

log = get_logger(__name__)

Verdict = Literal[
    "confirmed",
    "rejected_by_review",
    "rejected_guard",
    "rejected_invalid_on_original",
    "rejected_flaky",
    "rejected_error",
    "no_difference",
    "no_candidate",
]

REVIEW_SYSTEM_PROMPT = """\
You are auditing another engineer's regression test. It has ALREADY been executed \
and provably passes on the original code and fails on the changed code. A real \
behavioural difference is therefore established as fact - that is not in question.

Your ONLY question is whether that difference is one a user of this library could \
actually observe and would consider a bug.

REJECT only if:
- the test asserts on private internals or implementation details rather than on \
behaviour reachable through the library's API;
- it is tautological, or merely restates the change;
- it depends on object identity, memory addresses, dict ordering, timing or other \
things the library never guaranteed;
- the "difference" is an artefact of the test itself rather than of the code.

ACCEPT otherwise.

IMPORTANT - do NOT reject because the stated hypothesis mispredicted the mechanism. \
If the author expected a TypeError and the code instead returned a wrong value, the \
regression was still caught: an incorrect result is usually a WORSE bug than an \
exception, because nothing announces it. Execution has already proven the behaviour \
differs. Judge the difference, not the author's prose.

Be strict about the four rejection criteria and lenient about everything else. \
Respond with JSON only:
{"verdict": "accept" | "reject", "reason": "one sentence"}
"""


@dataclass(frozen=True, slots=True)
class SkepticVerdict:
    """The outcome of adjudicating one accusation."""

    verdict: Verdict
    reason: str
    original_result: ExecutionResult | None = None
    original_recheck: ExecutionResult | None = None
    changed_result: ExecutionResult | None = None
    review_reason: str | None = None
    review_provider: str | None = None

    @property
    def caught(self) -> bool:
        """Whether this counts as catching the regression. The scored outcome."""
        return self.verdict == "confirmed"


class Skeptic:
    """Adjudicates a candidate test by executing it against both code versions."""

    def __init__(
        self,
        router: LLMRouter | None = None,
        *,
        sandbox: SubprocessSandbox | None = None,
        review_enabled: bool = True,
    ) -> None:
        self.router = router
        self.sandbox = sandbox or SubprocessSandbox()
        # Review needs a router; without one, execution alone decides.
        self.review_enabled = review_enabled and router is not None

    def adjudicate(
        self,
        workspace: TargetWorkspace,
        spec: MutantSpec,
        accusation: Accusation,
    ) -> SkepticVerdict:
        """Run the full protocol for one accusation."""
        if not accusation.usable:
            return SkepticVerdict(
                verdict="no_candidate",
                reason=accusation.error
                or "the Prosecutor produced no test (it found no behavioural difference)",
            )

        path = workspace.repo_dir / spec.module_path
        original_source = path.read_text(encoding="utf-8")
        mutated_source = mutate_code(
            original_source, plugins.get_operator(spec.operator)(), spec.occurrence
        )
        if mutated_source is None:
            return SkepticVerdict(
                verdict="rejected_error", reason="the change could not be reconstructed"
            )

        # --- steps 1-3: the test must be valid and stable on the original ----
        first = self.sandbox.run(workspace, accusation.test_code)
        if first.outcome == "rejected":
            return SkepticVerdict(
                verdict="rejected_guard",
                reason=first.rejection_reason or "rejected by the static screen",
                original_result=first,
            )
        if first.outcome in ("error", "timeout"):
            return SkepticVerdict(
                verdict="rejected_error",
                reason=f"the test would not run on the original code ({first.outcome})",
                original_result=first,
            )
        if first.failed:
            return SkepticVerdict(
                verdict="rejected_invalid_on_original",
                reason="the test fails on the original code, so it is wrong about the library, not about the change",
                original_result=first,
            )

        second = self.sandbox.run(workspace, accusation.test_code)
        if not second.passed:
            return SkepticVerdict(
                verdict="rejected_flaky",
                reason="the test did not pass consistently on unchanged code",
                original_result=first,
                original_recheck=second,
            )

        # --- step 4: does it actually detect the change? --------------------
        try:
            path.write_text(mutated_source, encoding="utf-8")
            changed = self.sandbox.run(workspace, accusation.test_code)
        finally:
            # Unconditional: a leaked mutation would corrupt every later verdict
            # scored in this workspace.
            path.write_text(original_source, encoding="utf-8")

        if changed.passed:
            return SkepticVerdict(
                verdict="no_difference",
                reason="the test passes on the changed code too, so it does not detect the change",
                original_result=first,
                original_recheck=second,
                changed_result=changed,
            )

        # A test that errors or hangs on the changed code but passed twice on the
        # original has still established a difference caused by the change.
        detected_by = "assertion" if changed.failed else changed.outcome

        # --- step 5: adjudication, which may only downgrade -----------------
        if self.review_enabled:
            accepted, review_reason, provider = self._review(spec, accusation, changed)
            if not accepted:
                return SkepticVerdict(
                    verdict="rejected_by_review",
                    reason="execution differentiated, but the test does not demonstrate meaningful behaviour",
                    original_result=first,
                    original_recheck=second,
                    changed_result=changed,
                    review_reason=review_reason,
                    review_provider=provider,
                )
            return SkepticVerdict(
                verdict="confirmed",
                reason=f"passes on the original, {detected_by} on the changed code, and the test is behavioural",
                original_result=first,
                original_recheck=second,
                changed_result=changed,
                review_reason=review_reason,
                review_provider=provider,
            )

        return SkepticVerdict(
            verdict="confirmed",
            reason=f"passes on the original, {detected_by} on the changed code",
            original_result=first,
            original_recheck=second,
            changed_result=changed,
        )

    def _review(
        self, spec: MutantSpec, accusation: Accusation, changed: ExecutionResult
    ) -> tuple[bool, str, str]:
        """Ask a model whether an execution-confirmed test is meaningful.

        Fails open. If the reviewer is unreachable, execution already proved a
        behavioural difference, and discarding that evidence because a free-tier
        API was busy would understate the result.
        """
        if self.router is None:
            return True, "review not configured", ""
        user = "\n".join(
            [
                f"LIBRARY MODULE: {spec.module_path}",
                f"FUNCTION: {spec.function or '(module level)'}",
                "",
                f"STATED HYPOTHESIS: {accusation.hypothesis}",
                "",
                "THE TEST:",
                "```python",
                accusation.test_code.strip(),
                "```",
                "",
                "HOW IT FAILED ON THE CHANGED CODE:",
                "```",
                changed.tail(20),
                "```",
            ]
        )
        try:
            response = self.router.generate(
                LLMRequest(
                    system=REVIEW_SYSTEM_PROMPT, user=user, temperature=0.0, max_output_tokens=512
                )
            )
            payload = response.json()
        except Exception as exc:  # a busy free tier must not discard real evidence
            log.debug("Skeptic review unavailable (%s); accepting on execution alone", exc)
            return True, f"review unavailable: {exc}", ""
        accepted = str(payload.get("verdict", "accept")).lower() != "reject"
        return accepted, str(payload.get("reason") or ""), response.provider
