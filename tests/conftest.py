"""Shared test doubles."""

from __future__ import annotations

from dataclasses import dataclass, field

from precedent.llm.base import LLMRequest, LLMResponse, ProviderError


@dataclass
class FakeProvider:
    """A scripted provider, so agent logic can be tested without network or keys."""

    name: str = "fake"
    model: str = "fake-1"
    responses: list[str] = field(default_factory=list)
    error: Exception | None = None
    calls: int = 0

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        if self.error is not None:
            raise self.error
        if not self.responses:
            raise ProviderError("fake provider has no scripted responses left")
        text = self.responses.pop(0)
        return LLMResponse(text=text, provider=self.name, model=self.model)
