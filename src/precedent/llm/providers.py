"""Concrete free-tier providers.

Both are called over plain HTTP rather than through their vendor SDKs. Two
reasons: the SDKs pull large dependency trees for a single POST each, and their
exception hierarchies differ enough that normalising rate limiting across them
is more code than issuing the request directly.
"""

from __future__ import annotations

import re
import time
from typing import Any

import httpx

from precedent.llm.base import (
    LLMRequest,
    LLMResponse,
    ProviderError,
    RateLimitedError,
)
from precedent.logging import get_logger

log = get_logger(__name__)

_TIMEOUT = httpx.Timeout(240.0, connect=15.0)
# Free tiers answer 429 when a quota is spent and 503 when capacity is short.
# Both mean "ask someone else", which is different from a malformed request.
_FAILOVER_STATUS = {429, 500, 502, 503, 504}


class GeminiProvider:
    """Google AI Studio. Primary: ~1,500 requests/day free, no credit card."""

    name = "gemini"
    _BASE = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash") -> None:
        self._api_key = api_key
        self.model = model

    def generate(self, request: LLMRequest) -> LLMResponse:
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": request.system}]},
            "contents": [{"role": "user", "parts": [{"text": request.user}]}],
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_output_tokens,
            },
        }
        if request.json_output:
            body["generationConfig"]["responseMimeType"] = "application/json"

        started = time.perf_counter()
        try:
            response = httpx.post(
                f"{self._BASE}/{self.model}:generateContent",
                params={"key": self._api_key},
                json=body,
                timeout=_TIMEOUT,
            )
        except httpx.RequestError as exc:
            raise ProviderError(f"gemini transport error: {exc}") from exc

        if response.status_code in _FAILOVER_STATUS:
            raise RateLimitedError("gemini", _retry_after(response))
        if response.status_code >= 400:
            raise ProviderError(f"gemini {response.status_code}: {response.text[:300]}")

        payload = response.json()
        candidates = payload.get("candidates") or []
        if not candidates:
            # Usually a safety filter. Not retryable elsewhere in a useful way,
            # but it must not be mistaken for an empty successful answer.
            raise ProviderError(f"gemini returned no candidates: {str(payload)[:300]}")

        parts = candidates[0].get("content", {}).get("parts") or []
        text = "".join(part.get("text", "") for part in parts)
        usage = payload.get("usageMetadata", {})
        return LLMResponse(
            text=text,
            provider=self.name,
            model=self.model,
            prompt_tokens=usage.get("promptTokenCount", 0),
            completion_tokens=usage.get("candidatesTokenCount", 0),
            latency_seconds=time.perf_counter() - started,
            metadata={"finish_reason": candidates[0].get("finishReason", "")},
        )


class GroqProvider:
    """Groq. Fallback: much higher daily ceiling, OpenAI-compatible wire format."""

    name = "groq"
    _URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile") -> None:
        self._api_key = api_key
        self.model = model

    def generate(self, request: LLMRequest) -> LLMResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
        }
        if request.json_output:
            body["response_format"] = {"type": "json_object"}

        started = time.perf_counter()
        try:
            response = httpx.post(
                self._URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=body,
                timeout=_TIMEOUT,
            )
        except httpx.RequestError as exc:
            raise ProviderError(f"groq transport error: {exc}") from exc

        if response.status_code in _FAILOVER_STATUS:
            raise RateLimitedError("groq", _retry_after(response))
        if response.status_code >= 400:
            raise ProviderError(f"groq {response.status_code}: {response.text[:300]}")

        payload = response.json()
        choices = payload.get("choices") or []
        if not choices:
            raise ProviderError(f"groq returned no choices: {str(payload)[:300]}")

        usage = payload.get("usage", {})
        return LLMResponse(
            text=choices[0]["message"]["content"] or "",
            provider=self.name,
            model=self.model,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            latency_seconds=time.perf_counter() - started,
            metadata={"finish_reason": choices[0].get("finish_reason", "")},
        )


_DURATION = re.compile(r"(?:(\d+(?:\.\d+)?)m)?(?:(\d+(?:\.\d+)?)s)?")


def _retry_after(response: httpx.Response) -> float | None:
    """How long the provider says to wait, in seconds.

    Checked in order of specificity. Groq reports its own reset windows in
    ``x-ratelimit-reset-*`` as durations like ``4.147s`` or ``7m12s``; honouring
    those is far better than guessing, because the token-per-minute bucket is
    what actually gates a long batch run.
    """
    raw = response.headers.get("retry-after")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    for header in ("x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
        value = response.headers.get(header)
        if not value:
            continue
        match = _DURATION.fullmatch(value.strip())
        if match and any(match.groups()):
            minutes = float(match.group(1) or 0)
            seconds = float(match.group(2) or 0)
            return minutes * 60 + seconds
    return None
