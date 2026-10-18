"""Provider-independent LLM types.

Kept deliberately small. Two free-tier providers with different wire formats sit
behind this, and the agents above must not care which one answered - swapping
providers mid-evaluation because one hit its daily cap has to be invisible.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Protocol


class ProviderError(RuntimeError):
    """A provider failed in a way that is worth trying elsewhere."""


class RateLimitedError(ProviderError):
    """The provider refused because a quota was exhausted.

    Distinct from a generic failure because it is the expected steady state on
    a free tier, and it is the signal the router uses to fail over rather than
    to give up.
    """

    def __init__(self, provider: str, retry_after: float | None = None, detail: str = "") -> None:
        # The provider's own message is carried through. Without it, "rate
        # limited" is a symptom with no diagnosis: on this project it meant
        # tokens-per-minute twice and a self-inflicted oversized request once,
        # and those need opposite fixes.
        super().__init__(f"{provider} is rate limited{f': {detail}' if detail else ''}")
        self.provider = provider
        self.detail = detail
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class LLMRequest:
    """One model call, described independently of any provider's wire format."""

    system: str
    user: str
    temperature: float = 0.2
    max_output_tokens: int = 4096
    json_output: bool = True
    """Ask the model for JSON. Both agents return structured results, and free
    text would have to be parsed heuristically, which fails silently."""

    def cache_key(self, model: str) -> str:
        """Content address for this request against a specific model.

        The model is part of the key: the same prompt answered by Gemini and by
        Groq are different results and must not share a cache entry.
        """
        payload = json.dumps(
            {
                "system": self.system,
                "user": self.user,
                "temperature": self.temperature,
                "max_output_tokens": self.max_output_tokens,
                "json_output": self.json_output,
                "model": model,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """A model's answer, plus what it cost and who served it."""

    text: str
    provider: str
    model: str
    cached: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def json(self) -> dict[str, Any]:
        """Parse the response as JSON, tolerating the usual model preambles.

        Models wrap JSON in markdown fences or prepend a sentence even when told
        not to. Rather than fail the whole evaluation on formatting, the first
        balanced JSON object in the text is extracted.
        """
        text = self.text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1 : -1 if lines[-1].strip() == "```" else None])
        try:
            parsed: dict[str, Any] = json.loads(text)
        except json.JSONDecodeError:
            extracted = _first_json_object(text)
            if extracted is None:
                raise
            parsed = extracted
        return parsed


def _first_json_object(text: str) -> dict[str, Any] | None:
    """Find the first balanced ``{...}`` block, ignoring braces inside strings."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed: dict[str, Any] = json.loads(text[start : index + 1])
                except json.JSONDecodeError:
                    return None
                return parsed
    return None


class Provider(Protocol):
    """What the router needs from any backend."""

    name: str
    model: str

    def generate(self, request: LLMRequest) -> LLMResponse: ...
