"""The Prosecutor: argues that a diff introduces a regression.

Given a change and its surrounding code, it forms one specific hypothesis about
how observable behaviour differs, and writes a single pytest test intended to
pass on the original code and fail on the changed code.

It is never told the change is a mutant, or that a bug is definitely present.
That framing matters: the real use case is auditing a pull request, where the
change may well be fine, and an agent told "find the bug" will invent one.

Phase 3 extends this with retrieval, adding a required citation to the retrieved
item that motivated the hypothesis. The prompt is structured so that addition is
purely additive - the phase 2 baseline stays exactly as it was measured.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from verdict.agents.context import DiffContext
from verdict.llm.base import LLMRequest
from verdict.llm.router import LLMRouter
from verdict.logging import get_logger

log = get_logger(__name__)

SYSTEM_PROMPT = """\
You are a meticulous senior engineer reviewing a code change for regressions.

Your job is to determine whether the change alters observable behaviour in a way \
the existing test suite would miss, and to prove it with a test.

READING THE DIFF - GET THIS RIGHT BEFORE ANYTHING ELSE
Lines beginning `-` are the ORIGINAL code. Lines beginning `+` are the CHANGED \
code. The enclosing source shown to you is the code AS IT IS AFTER THE CHANGE, so \
do not read it as the original. Your test must assert what the ORIGINAL (`-`) code \
does. If you assert the CHANGED (`+`) behaviour by mistake, your test fails against \
the original code, proves nothing about the change, and is discarded.

METHOD
1. Read the diff. Name which side is `-` and which is `+`.
2. Identify ONE specific input for which the original and the changed code produce \
different observable results. Be concrete: name the exact value.
3. Write down separately what the ORIGINAL produces for that input and what the \
CHANGED code produces. If those two are the same, you have no case - say so.
4. Write ONE pytest test whose assertions match the ORIGINAL value from step 3. It \
PASSES against the ORIGINAL code and FAILS against the CHANGED code.

HARD REQUIREMENTS for the test
- A single self-contained function named `test_...`, plus its imports.
- Import from the library's public API. Do not reach into private internals.
- Assert on RETURN VALUES, RAISED EXCEPTIONS, or MUTATED ARGUMENTS - real behaviour.
- Deterministic. No randomness, no clocks, no network, no filesystem, no sleeps.
- Never inspect source code or bytecode. Do not import `inspect` or `dis`, and do \
not read `__code__`. A test that reads source text instead of running the code \
proves nothing and will be discarded.
- No pytest fixtures from conftest. Everything the test needs, it constructs.
- If you genuinely cannot find a behavioural difference, say so honestly by \
setting "confident" to false. A wrong accusation is worse than no accusation.

Respond with JSON only, matching this shape exactly:
{
  "used_evidence": "what the supplied evidence told you, or 'nothing relevant' (omit if none was supplied)",
  "original_behaviour": "for your chosen input, what the ORIGINAL (`-`) code returns or raises",
  "changed_behaviour": "for the same input, what the CHANGED (`+`) code returns or raises",
  "hypothesis": "one sentence: what observably differs, and for which input",
  "reasoning": "two or three sentences explaining why the change causes it",
  "test_name": "test_something_descriptive",
  "test_code": "the complete test file, imports included, as a single string",
  "confident": true
}
"""


# A citation may arrive in its own field, or embedded in prose. Models asked
# for both a narrative field and a separate id routinely merge them - observed
# on the real corpus: `used_evidence` read
# "code_chunk:adbccc069e34 showed original indexing behavior of pop" with the
# `citation` field omitted entirely, which read as 0% engagement when in fact
# every case was engaging. Grounding is measured from the response, so the
# parser has to accept both shapes.
_CITATION = re.compile(r"\b(incident|revert|call_site|code_chunk):([0-9a-f]{8,16})\b")


def _extract_citation(payload: dict[str, Any]) -> str | None:
    """Find a document reference in the response, wherever the model put it."""
    direct = payload.get("citation")
    if isinstance(direct, str) and (match := _CITATION.search(direct)):
        return f"{match.group(1)}:{match.group(2)}"
    for key in ("citation", "used_evidence", "reasoning", "hypothesis"):
        value = payload.get(key)
        if isinstance(value, str) and (match := _CITATION.search(value)):
            return f"{match.group(1)}:{match.group(2)}"
    return None


@dataclass(frozen=True, slots=True)
class Accusation:
    """The Prosecutor's output for one diff."""

    hypothesis: str
    reasoning: str
    test_name: str
    test_code: str
    confident: bool
    provider: str
    model: str
    cached: bool
    latency_seconds: float
    raw: str
    error: str | None = None
    citation: str | None = None
    """Phase 3 only: the retrieved item that motivated the hypothesis."""

    used_evidence: str = ""
    """Phase 3 only: the Prosecutor's own statement of what the evidence told it."""

    original_behaviour: str = ""
    """What the model predicts the ORIGINAL code does for its chosen input.

    Required before the test is written. Three of the four catches lost to
    retrieval in the first ablation were direction inversions - the model
    described the *mutant's* behaviour as the original's, so its assertion could
    only ever fail on unchanged code. Forcing both sides to be named separately
    makes the polarity explicit while the model still has room to correct it.
    """

    changed_behaviour: str = ""
    """What the model predicts the CHANGED code does for the same input."""

    @property
    def usable(self) -> bool:
        """Whether there is a test worth executing.

        Note what is *not* required: confidence. If the Prosecutor produced a
        test, it gets executed even when the model flagged itself unsure.

        That follows from the project's central claim. The Skeptic decides by
        running the test against both versions of the code - execution is the
        arbiter, not persuasion. Letting a model's self-assessment veto its own
        test would put the verdict back in the model's hands, which is exactly
        what the Prosecutor/Skeptic split exists to prevent. A hedged test that
        provably passes on the original and fails on the change has caught the
        regression, whatever its author thought of it.

        Confidence is still recorded, so unconfident catches can be reported
        separately if that distinction ever matters.
        """
        return self.error is None and bool(self.test_code.strip())

    @staticmethod
    def failed(reason: str) -> Accusation:
        """A non-answer, recorded rather than raised.

        One malformed model response must not abort an evaluation of 454
        mutants; it is simply a mutant the agent failed to catch.
        """
        return Accusation(
            hypothesis="",
            reasoning="",
            test_name="",
            test_code="",
            confident=False,
            provider="",
            model="",
            cached=False,
            latency_seconds=0.0,
            raw="",
            error=reason,
        )


REPAIR_PROMPT = """\
Your previous regression test did not run correctly against the ORIGINAL, \
UNCHANGED code. It must PASS there before it can be evidence about the change.

Read the failure below and fix the test.

Check the most likely cause FIRST: did you assert the CHANGED (`+`) behaviour \
instead of the ORIGINAL (`-`) behaviour? If the failure shows the original code \
producing a value you predicted for the changed code, you read the diff backwards. \
Flip the assertion to the value the original actually produced - your hypothesis \
may still be correct, only stated the wrong way round.

Other common causes: importing a name that does not exist, calling the API with the \
wrong signature, or asserting behaviour the library never had. Keep the same \
hypothesis if it is still sound; abandon it by setting "confident" to false if the \
failure shows it was wrong.

Respond with JSON only, in exactly the same shape as before.
"""


class Prosecutor:
    """Turns a diff into a candidate regression test."""

    def __init__(
        self,
        router: LLMRouter,
        *,
        temperature: float = 0.2,
        max_output_tokens: int = 4096,
    ) -> None:
        self.router = router
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens

    def build_request(self, context: DiffContext, *, retrieval_block: str = "") -> LLMRequest:
        """Compose the prompt.

        ``retrieval_block`` is empty in phase 2 and carries the retrieved
        history in phase 3. Keeping it a single appended section is what makes
        the two arms differ in exactly one respect.
        """
        user = context.render()
        if retrieval_block:
            user = f"{user}\n\n{retrieval_block}"
        return LLMRequest(
            system=SYSTEM_PROMPT,
            user=user,
            temperature=self.temperature,
            max_output_tokens=self.max_output_tokens,
            json_output=True,
        )

    def revise(
        self,
        context: DiffContext,
        previous: Accusation,
        failure_output: str,
        *,
        retrieval_block: str = "",
    ) -> Accusation:
        """One chance to repair a test that failed against the unchanged code.

        A test that does not run says nothing about the change, and discarding
        it outright measures the model's first-draft syntax rather than whether
        it understood the diff. Real review agents iterate, so one bounded
        repair attempt is both fairer and more representative.

        It is bounded at one attempt deliberately: an unbounded loop would let a
        model grind against the library until something incidentally differs,
        which is a different (and much weaker) claim than catching the
        regression. The same allowance is given to both ablation arms.
        """
        base = self.build_request(context, retrieval_block=retrieval_block)
        user = "\n".join(
            [
                base.user,
                "",
                REPAIR_PROMPT,
                f"YOUR PREVIOUS HYPOTHESIS: {previous.hypothesis}",
                "",
                "YOUR PREVIOUS TEST:",
                "```python",
                previous.test_code.strip(),
                "```",
                "",
                "HOW IT FAILED ON THE ORIGINAL, UNCHANGED CODE:",
                "```",
                failure_output.strip()[-1500:],
                "```",
            ]
        )
        return self._generate(
            LLMRequest(
                system=SYSTEM_PROMPT,
                user=user,
                temperature=self.temperature,
                max_output_tokens=self.max_output_tokens,
                json_output=True,
            ),
            context,
        )

    def accuse(self, context: DiffContext, *, retrieval_block: str = "") -> Accusation:
        """Produce an accusation, or a recorded failure."""
        return self._generate(self.build_request(context, retrieval_block=retrieval_block), context)

    def _generate(self, request: LLMRequest, context: DiffContext) -> Accusation:
        try:
            response = self.router.generate(request)
        except Exception as exc:  # one bad call must not stop a 454-mutant run
            log.warning("Prosecutor call failed for %s: %s", context.module_path, exc)
            return Accusation.failed(f"llm call failed: {exc}")

        try:
            payload = response.json()
        except ValueError as exc:
            return Accusation.failed(f"response was not JSON: {exc}")

        test_code = str(payload.get("test_code") or "").strip()
        return Accusation(
            hypothesis=str(payload.get("hypothesis") or ""),
            reasoning=str(payload.get("reasoning") or ""),
            test_name=str(payload.get("test_name") or ""),
            test_code=test_code,
            confident=bool(payload.get("confident", True)),
            provider=response.provider,
            model=response.model,
            cached=response.cached,
            latency_seconds=response.latency_seconds,
            raw=response.text,
            citation=_extract_citation(payload),
            used_evidence=str(payload.get("used_evidence") or ""),
            original_behaviour=str(payload.get("original_behaviour") or ""),
            changed_behaviour=str(payload.get("changed_behaviour") or ""),
        )
