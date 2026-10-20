"""Shared test doubles."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tests.toy import Toy, build_toy
from verdict.llm.base import LLMRequest, LLMResponse, ProviderError


@dataclass
class FakeProvider:
    """A scripted provider, so agent logic can be tested without network or keys."""

    name: str = "fake"
    model: str = "fake-1"
    responses: list[str] = field(default_factory=list)
    error: Exception | None = None
    calls: int = 0

    #: Raise ``error`` for the first N calls only, then serve ``responses``.
    fail_first: int = 0

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        if self.error is not None and (self.fail_first == 0 or self.calls <= self.fail_first):
            raise self.error
        if not self.responses:
            raise ProviderError("fake provider has no scripted responses left")
        text = self.responses.pop(0)
        return LLMResponse(text=text, provider=self.name, model=self.model)


@pytest.fixture
def toy(tmp_path: Path) -> Toy:
    """The toy project: one real bug, fixed in a later commit with a new test."""
    return build_toy(tmp_path / "toy")
