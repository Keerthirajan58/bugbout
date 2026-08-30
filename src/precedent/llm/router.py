"""Routes a request across providers, with caching and failover.

The evaluation is roughly ten thousand calls and every provider here is on a
free tier with a daily cap. A single-provider client would stall the run the
moment that cap is reached, hours in, with no way to resume except waiting for
the quota to reset.

So the router: serves from cache when it can, tries providers in order, and
treats a rate limit as a signal to move on rather than an error. What it will
not do is retry forever - a genuinely bad request must surface as a failure
rather than burn the remaining quota on both providers.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass

from precedent.llm.base import (
    LLMRequest,
    LLMResponse,
    Provider,
    ProviderError,
    RateLimitedError,
)
from precedent.llm.cache import ResponseCache
from precedent.logging import get_logger

log = get_logger(__name__)


class AllProvidersFailedError(RuntimeError):
    """Every configured provider refused or failed."""

    def __init__(self, failures: dict[str, str]) -> None:
        detail = "\n".join(f"  {name}: {reason}" for name, reason in failures.items())
        super().__init__(f"No provider could serve the request:\n{detail}")
        self.failures = failures


@dataclass
class RouterStats:
    """Where answers came from. Reported so a run's provenance is not a mystery."""

    by_provider: Counter[str]
    cache_hits: int = 0
    cache_misses: int = 0
    failovers: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def summary(self) -> str:
        served = ", ".join(f"{name}={count}" for name, count in self.by_provider.most_common())
        total = self.cache_hits + self.cache_misses
        rate = self.cache_hits / total if total else 0.0
        return (
            f"served [{served or 'none'}] | cache {self.cache_hits}/{total} ({rate:.0%}) "
            f"| failovers {self.failovers} "
            f"| tokens {self.prompt_tokens}+{self.completion_tokens}"
        )


class LLMRouter:
    """A cache-backed, failing-over client over an ordered list of providers."""

    def __init__(
        self,
        providers: list[Provider],
        *,
        cache: ResponseCache | None = None,
        max_attempts_per_provider: int = 2,
        backoff_seconds: float = 2.0,
    ) -> None:
        if not providers:
            raise ValueError("LLMRouter needs at least one provider")
        self.providers = providers
        self.cache = cache or ResponseCache()
        self.max_attempts_per_provider = max_attempts_per_provider
        self.backoff_seconds = backoff_seconds
        self.stats = RouterStats(by_provider=Counter())

    def generate(self, request: LLMRequest) -> LLMResponse:
        """Answer a request from cache, or from the first provider that can."""
        # The cache is keyed per model, so it is checked once per provider
        # rather than once overall: a response cached from Groq is still a
        # valid answer even when Gemini is the current primary.
        for provider in self.providers:
            cached = self.cache.get(request.cache_key(provider.model))
            if cached is not None:
                self.stats.cache_hits += 1
                self.stats.by_provider[f"{cached.provider} (cached)"] += 1
                return cached
        self.stats.cache_misses += 1

        failures: dict[str, str] = {}
        for index, provider in enumerate(self.providers):
            try:
                response = self._call_with_retry(provider, request)
            except ProviderError as exc:
                failures[provider.name] = str(exc)
                if index < len(self.providers) - 1:
                    self.stats.failovers += 1
                    log.warning(
                        "%s unavailable (%s); falling back to %s",
                        provider.name,
                        type(exc).__name__,
                        self.providers[index + 1].name,
                    )
                continue

            self.cache.put(request.cache_key(provider.model), response)
            self.stats.by_provider[provider.name] += 1
            self.stats.prompt_tokens += response.prompt_tokens
            self.stats.completion_tokens += response.completion_tokens
            return response

        raise AllProvidersFailedError(failures)

    def _call_with_retry(self, provider: Provider, request: LLMRequest) -> LLMResponse:
        """Retry a provider a bounded number of times before giving up on it.

        Transient 503s are common on free tiers and usually clear in seconds, so
        a couple of attempts avoids a needless failover. The bound matters: an
        unbounded retry would spend the daily quota on one bad request.
        """
        last: ProviderError | None = None
        for attempt in range(1, self.max_attempts_per_provider + 1):
            try:
                return provider.generate(request)
            except RateLimitedError:
                # A spent quota will not clear in seconds. Fail over immediately
                # rather than sleeping through a retry that cannot succeed.
                raise
            except ProviderError as exc:
                last = exc
                if attempt < self.max_attempts_per_provider:
                    delay = self.backoff_seconds * attempt
                    log.debug(
                        "%s attempt %d failed (%s); retrying in %.1fs",
                        provider.name,
                        attempt,
                        exc,
                        delay,
                    )
                    time.sleep(delay)
        # Unreachable: the loop either returns or leaves `last` set.
        raise last if last else ProviderError(f"{provider.name} failed without an error")
