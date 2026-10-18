"""Blind examiners: regression tests written from the issue alone.

An examiner is shown the bug report and the relevant code *as it was before
anyone touched it* - never a contestant's patch. That is the point. A test
written while looking at a fix tends to test the fix; a test written from the
report tests the requirement, so it can tell a correct patch from a plausible
one.

Examiners come from different model families than the contestants they judge
where possible, and the judge never lets a family's evidence count for its own
patch. Each examiner makes one call per task, which is what makes the free
tiers enough: the expensive, agentic work is the contestants', and the
judgement is made by running these tests, not by asking a model.
"""

from __future__ import annotations

import ast
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from verdict.llm.base import LLMRequest, Provider, ProviderError, RateLimitedError
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


@dataclass(frozen=True, slots=True)
class Exhibit:
    """One examiner's test file for one task."""

    task_id: str
    author: str
    family: str
    test_code: str
    reasoning: str = ""
    model: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Exhibit:
        return cls(**{k: v for k, v in data.items() if k in set(cls.__slots__)})


def source_context(base: Path, files: list[str], touched: dict[str, set[int]], budget: int) -> str:
    """The base version of the files the contestants changed, within a budget.

    A whole file when it fits; otherwise its imports plus every top-level
    definition a contestant's patch reached into. Files are ordered by how many
    contestants touched them, so the shared hotspot is never the one trimmed.
    """
    sections: list[str] = []
    remaining = budget
    for rel in files:
        path = base / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > remaining:
            text = _excerpt(text, touched.get(rel, set()))
        text = text[:remaining]
        if not text:
            break
        sections.append(f"\n--- {rel} ---\n{text}")
        remaining -= len(text)
    return "".join(sections) or "\n(no source context available)"


def _excerpt(source: str, lines: set[int]) -> str:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source
    keep: list[tuple[int, int]] = []
    for node in tree.body:
        start, end = node.lineno, node.end_lineno or node.lineno
        is_import = isinstance(node, ast.Import | ast.ImportFrom)
        if is_import or any(start <= n <= end for n in lines):
            keep.append((start, end))
    rows = source.splitlines()
    out: list[str] = []
    last = 0
    for start, end in keep:
        if start > last + 1:
            out.append("# ...")
        out.extend(rows[start - 1 : end])
        last = end
    return "\n".join(out) + "\n"


class Examiner:
    """One model family writing blind tests, failing over across its own models."""

    def __init__(
        self,
        author: str,
        family: str,
        providers: list[Provider],
        *,
        context_budget: int,
        cache: ResponseCache | None = None,
        max_wait: float = 180.0,
    ) -> None:
        self.author = author
        self.family = family
        self.providers = providers
        self.context_budget = context_budget
        self.cache = cache or ResponseCache()
        self.max_wait = max_wait

    def examine(self, task: IssueTask, repo: str, context: str) -> Exhibit:
        request = LLMRequest(
            system=SYSTEM,
            user=USER.format(
                repo=repo,
                title=task.title,
                statement=task.problem_statement.strip(),
                context=context,
            ),
            temperature=0.2,
            max_output_tokens=4096,
        )
        errors: list[str] = []
        for provider in self.providers:
            key = request.cache_key(provider.model)
            response = self.cache.get(key)
            waited = 0.0
            while response is None:
                try:
                    response = provider.generate(request)
                    self.cache.put(key, response)
                except RateLimitedError as exc:
                    # A per-minute limit is worth waiting out; a daily one is
                    # not, and the next model in the family has its own quota.
                    pause = exc.retry_after or 30.0
                    if "day" in exc.detail.lower() or waited + pause > self.max_wait:
                        errors.append(f"{provider.model}: {exc}")
                        break
                    time.sleep(pause)
                    waited += pause
                except ProviderError as exc:
                    errors.append(f"{provider.model}: {exc}")
                    break
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
                task.task_id,
                self.author,
                self.family,
                code + "\n",
                str(payload.get("reasoning", "")),
                provider.model,
            )
        return Exhibit(task.task_id, self.author, self.family, "", error="; ".join(errors))
