"""Prompted examiners: one model call per task, no execution.

Cheap enough for the free tiers - and measured to be weak evidence: on the
development suite, tests written this way picked patches worse than chance and
flagged 16 of 39 correct patches. They are kept as a baseline and as the only
examiner a quota-starved vendor can afford.
"""

from __future__ import annotations

import time

from verdict.arena.examiners.base import Examiner, ExamRoom
from verdict.arena.examiners.exhibit import Exhibit
from verdict.llm.base import LLMRequest, LLMResponse, Provider, ProviderError, RateLimitedError
from verdict.llm.cache import ResponseCache
from verdict.logging import get_logger
from verdict.review.tasks import IssueTask

log = get_logger(__name__)

SYSTEM = """\
You are a senior engineer writing a regression test for a bug report, before anyone has \
fixed it.

You are given the issue and the relevant source code as it is now, with the bug still \
present. Write a pytest test file that:
- FAILS on the current code, because of the bug the issue describes, and
- PASSES once the bug is correctly fixed.

Rules:
- Test the behaviour the issue asks for, through the library's public API. Do not assert \
on private helpers or implementation details, and do not pin exact error-message wording \
unless the issue is about the message.
- Write a few focused test functions, each exercising one concrete input the issue \
mentions or clearly implies.
- Do not test anything the issue does not ask to change.
- Deterministic and self-contained: imports plus test functions, no network, no clocks, \
no randomness, no conftest fixtures beyond pytest's built-ins.

Respond with JSON only:
{"reasoning": "two or three sentences: what the bug is and how the tests expose it", \
"test_code": "the complete test file as a single string"}
"""

USER = """\
Repository: {repo}

<issue>
# {title}

{statement}
</issue>

Relevant source, as it is now (the bug is still present):
{context}
"""

_TRUNCATED = ("MAX_TOKENS", "length")


class PromptExaminer(Examiner):
    """One vendor's models, tried in order: the next takes over when one is spent."""

    def __init__(
        self,
        author: str,
        providers: list[Provider],
        *,
        context_budget: int,
        max_output_tokens: int = 4096,
        cache: ResponseCache | None = None,
        max_wait: float = 180.0,
    ) -> None:
        self.author = author
        self.providers = providers
        self.context_budget = context_budget
        self.max_output_tokens = max_output_tokens
        self.cache = cache or ResponseCache()
        self.max_wait = max_wait
        self._spent: set[str] = set()
        """Models whose daily quota ran out this run; not retried on later tasks."""

    @property
    def uses_local_models(self) -> bool:
        return any(p.name == "ollama" for p in self.providers)

    def describe(self) -> dict[str, object]:
        return {"kind": "prompted", "models": [p.model for p in self.providers]}

    def examine(self, tasks: list[IssueTask], room: ExamRoom) -> list[Exhibit]:
        exhibits = []
        for task in tasks:
            bench = room.layout.bench(task.target_slug)
            exhibit = self.examine_one(
                task, bench.target.full_name, room.source_context(task, self.context_budget)
            )
            log.info("%s  %s  %s", self.author, task.task_id, exhibit.model or exhibit.error[:90])
            exhibits.append(exhibit)
        return exhibits

    def examine_one(self, task: IssueTask, repo: str, context: str) -> Exhibit:
        request = LLMRequest(
            system=SYSTEM,
            user=USER.format(
                repo=repo,
                title=task.title,
                statement=task.problem_statement.strip(),
                context=context,
            ),
            temperature=0.2,
            max_output_tokens=self.max_output_tokens,
        )
        errors: list[str] = []
        for provider in self.providers:
            response = self._ask(provider, request, errors)
            if response is None:
                continue
            try:
                payload = response.json()
            except ValueError as exc:
                errors.append(f"{provider.model}: unparseable response ({exc})")
                continue
            code = str(payload.get("test_code", "")).strip()
            if not code:
                errors.append(f"{provider.model}: no test_code in response")
                continue
            return Exhibit(
                task_id=task.task_id,
                author=self.author,
                vendor=self.vendor,
                kind="blind",
                test_code=code + "\n",
                reasoning=str(payload.get("reasoning", "")),
                model=provider.model,
            )
        return Exhibit(task.task_id, self.author, self.vendor, "blind", "", error="; ".join(errors))

    def _ask(
        self, provider: Provider, request: LLMRequest, errors: list[str]
    ) -> LLMResponse | None:
        """A cached or fresh answer from one model, or ``None`` with the reason recorded."""
        key = request.cache_key(provider.model)
        if (cached := self.cache.get(key)) is not None:
            return cached
        if provider.model in self._spent:
            return None
        waited = 0.0
        while True:
            try:
                response = provider.generate(request)
            except RateLimitedError as exc:
                # A per-minute limit is worth waiting out; a daily one is not,
                # and the next model in the vendor's list has its own quota.
                pause = exc.retry_after or 30.0
                if "day" in exc.detail.lower():
                    self._spent.add(provider.model)
                    errors.append(f"{provider.model}: daily quota exhausted")
                    return None
                if waited + pause > self.max_wait:
                    errors.append(f"{provider.model}: {exc}")
                    return None
                time.sleep(pause)
                waited += pause
            except ProviderError as exc:
                errors.append(f"{provider.model}: {exc}")
                return None
            else:
                if response.metadata.get("finish_reason") not in _TRUNCATED:
                    self.cache.put(key, response)  # never cache a truncated answer
                return response
