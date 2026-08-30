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

from dataclasses import dataclass
from typing import Any

from precedent.agents.context import DiffContext
from precedent.llm.base import LLMRequest
from precedent.llm.router import LLMRouter
from precedent.logging import get_logger

log = get_logger(__name__)

SYSTEM_PROMPT = """\
You are a meticulous senior engineer reviewing a code change for regressions.

Your job is to determine whether the change alters observable behaviour in a way \
the existing test suite would miss, and to prove it with a test.

METHOD
1. Read the diff and the surrounding code.
2. Identify ONE specific input for which the original code and the changed code \
produce different observable results. Be concrete: name the exact value.
3. Write ONE pytest test that PASSES against the ORIGINAL code and FAILS against \
the CHANGED code.

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
  "hypothesis": "one sentence: what observably differs, and for which input",
  "reasoning": "two or three sentences explaining why the change causes it",
  "test_name": "test_something_descriptive",
  "test_code": "the complete test file, imports included, as a single string",
  "confident": true
}
"""


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
    citation: dict[str, Any] | None = None
    """Populated in phase 3 only: which retrieved item motivated the hypothesis."""

    @property
    def usable(self) -> bool:
        """Whether there is a test worth executing."""
        return self.error is None and self.confident and bool(self.test_code.strip())

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

    def accuse(self, context: DiffContext, *, retrieval_block: str = "") -> Accusation:
        """Produce an accusation, or a recorded failure."""
        request = self.build_request(context, retrieval_block=retrieval_block)
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
            citation=payload.get("citation"),
        )
