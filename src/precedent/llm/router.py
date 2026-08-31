"""Routes a request across providers, with caching, throttling and failover.

The evaluation is roughly two thousand calls and every provider here is on a
free tier. Measuring those tiers rather than assuming them changed the design:
Groq's ``gpt-oss-120b`` allows **8,000 tokens per minute**, and a Prosecutor call
costs about 1,400 - so roughly five calls a minute, no matter how many workers
are pushing. Four parallel workers against that limit produced 29 failures out
of 32 requests in an early pilot.

So the router does three things a naive client does not:

* **Waits.** A rate limit is a *temporary* condition with a known reset window,
  not an error. Providers report that window (``retry-after``, or Groq's
  ``x-ratelimit-reset-tokens``), and the router honours it instead of guessing.
* **Paces.** A minimum interval between calls to the same provider keeps the run
  under the limit rather than repeatedly discovering it.
* **Fails over.** When one lane is cooling down, another can serve immediately.

A "lane" is a (provider, model) pair, because quotas are per model: adding a
second Groq model adds a genuinely separate token bucket.
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from dataclasses import dataclass, field

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

# How long a single request may spend waiting out rate limits before giving up.
# Bounded so a wedged run surfaces rather than hanging forever, but the bound
# has to exceed what providers actually quote: Groq returned retry hints of
# 777s and 2169s during the phase 2 run, and a 900s budget silently lost 14 of
# 115 cases to them.
MAX_WAIT_SECONDS = 2700.0
# Used when a provider rate-limits without saying for how long.
DEFAULT_COOLDOWN_SECONDS = 30.0

# Longest a lane is ever parked, regardless of what the provider suggests.
#
# Providers quote the reset of their *widest* window: Groq answered a 429 with
# a 2169s hint while the per-minute token bucket refilled in seconds, and one
# model reported a 7-hour reset while still holding 708 of 1000 requests.
# Honouring those literally parked healthy lanes for hours - the single largest
# cause of stalled runs in this project. Re-probing after two minutes costs one
# rejected request and recovers the lane as soon as it is actually usable.
MAX_COOLDOWN_SECONDS = 120.0


class AllProvidersFailedError(RuntimeError):
    """Every configured provider refused or failed."""

    def __init__(self, failures: dict[str, str]) -> None:
        detail = "\n".join(f"  {name}: {reason}" for name, reason in failures.items())
        super().__init__(f"No provider could serve the request:\n{detail}")
        self.failures = failures


@dataclass
class Lane:
    """One provider/model pair, with its own quota and pacing state."""

    provider: Provider
    min_interval: float = 0.0
    """Seconds to leave between calls, to stay under a requests-per-minute cap."""

    _available_at: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def name(self) -> str:
        return f"{self.provider.name}:{self.provider.model}"

    def wait_seconds(self, now: float) -> float:
        """How long until this lane may be used again."""
        return max(0.0, self._available_at - now)

    def reserve(self) -> None:
        """Claim the next slot, pacing calls to the configured interval."""
        with self._lock:
            now = time.monotonic()
            start = max(now, self._available_at)
            self._available_at = start + self.min_interval
        delay = start - now
        if delay > 0:
            time.sleep(delay)

    def cool_down(self, seconds: float) -> None:
        """Park the lane briefly after a rate limit, then re-probe.

        The provider's hint is capped: it describes the reset of whichever
        window was hit, which is often far wider than the one that will
        actually clear. Waiting it out literally is how a run loses hours.
        """
        with self._lock:
            delay = min(seconds, MAX_COOLDOWN_SECONDS)
            self._available_at = max(self._available_at, time.monotonic() + delay)


@dataclass
class RouterStats:
    """Where answers came from. Reported so a run's provenance is not a mystery."""

    by_provider: Counter[str]
    cache_hits: int = 0
    cache_misses: int = 0
    failovers: int = 0
    rate_limit_waits: int = 0
    seconds_waiting: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def summary(self) -> str:
        served = ", ".join(f"{name}={count}" for name, count in self.by_provider.most_common())
        total = self.cache_hits + self.cache_misses
        rate = self.cache_hits / total if total else 0.0
        return (
            f"served [{served or 'none'}] | cache {self.cache_hits}/{total} ({rate:.0%}) "
            f"| failovers {self.failovers} | rate-limit waits {self.rate_limit_waits} "
            f"({self.seconds_waiting / 60:.1f} min) "
            f"| tokens {self.prompt_tokens}+{self.completion_tokens}"
        )


class LLMRouter:
    """A cache-backed, throttled, failing-over client over ordered provider lanes."""

    def __init__(
        self,
        providers: list[Provider],
        *,
        cache: ResponseCache | None = None,
        max_attempts_per_provider: int = 2,
        backoff_seconds: float = 2.0,
        min_interval: float | dict[str, float] = 0.0,
        max_wait_seconds: float = MAX_WAIT_SECONDS,
    ) -> None:
        if not providers:
            raise ValueError("LLMRouter needs at least one provider")
        self.providers = providers
        self.lanes = [
            Lane(
                provider=p,
                min_interval=(
                    min_interval.get(p.name, 0.0)
                    if isinstance(min_interval, dict)
                    else min_interval
                ),
            )
            for p in providers
        ]
        self.cache = cache or ResponseCache()
        self.max_attempts_per_provider = max_attempts_per_provider
        self.backoff_seconds = backoff_seconds
        self.max_wait_seconds = max_wait_seconds
        self.stats = RouterStats(by_provider=Counter())
        self._stats_lock = threading.Lock()

    def generate(self, request: LLMRequest) -> LLMResponse:
        """Answer a request from cache, or from the first lane that can serve it."""
        failures: dict[str, str] = {}
        counted_miss = False
        waited = 0.0

        while True:
            rate_limited: dict[str, float] = {}

            for index, lane in enumerate(self.lanes):
                # The cache is consulted lane by lane, in preference order,
                # rather than scanned across every lane up front.
                #
                # Scanning up front let a cached answer from ANY model satisfy
                # the request, which silently overrode model selection: in one
                # phase 3 run, 111 of 115 cases came back from a single model
                # regardless of which lane they were routed to, purely because
                # that model happened to hold a cache entry. Model choice is
                # part of the experiment and cannot be decided by cache residue.
                cached = self.cache.get(request.cache_key(lane.provider.model))
                if cached is not None:
                    with self._stats_lock:
                        self.stats.cache_hits += 1
                        self.stats.by_provider[f"{cached.provider} (cached)"] += 1
                    return cached
                if not counted_miss:
                    counted_miss = True
                    with self._stats_lock:
                        self.stats.cache_misses += 1

                cooldown = lane.wait_seconds(time.monotonic())
                if cooldown > 0:
                    rate_limited[lane.name] = cooldown
                    continue
                try:
                    lane.reserve()
                    response = self._call_with_retry(lane.provider, request)
                except RateLimitedError as exc:
                    delay = exc.retry_after or DEFAULT_COOLDOWN_SECONDS
                    lane.cool_down(delay)
                    rate_limited[lane.name] = delay
                    failures[lane.name] = f"rate limited, retry in {delay:.0f}s"
                    if index < len(self.lanes) - 1:
                        with self._stats_lock:
                            self.stats.failovers += 1
                    continue
                except ProviderError as exc:
                    failures[lane.name] = str(exc)
                    if index < len(self.lanes) - 1:
                        with self._stats_lock:
                            self.stats.failovers += 1
                    continue

                self.cache.put(request.cache_key(lane.provider.model), response)
                with self._stats_lock:
                    self.stats.by_provider[lane.provider.name] += 1
                    self.stats.prompt_tokens += response.prompt_tokens
                    self.stats.completion_tokens += response.completion_tokens
                return response

            # Nothing served. If it was only rate limits, the condition is
            # temporary by definition - wait it out rather than losing the case.
            if not rate_limited or waited >= self.max_wait_seconds:
                # Lanes skipped while cooling down never reached `failures`,
                # which produced a bare "No provider could serve the request"
                # with nothing after the colon. Fold them in so the reason is
                # always visible.
                for name, cooldown in rate_limited.items():
                    failures.setdefault(
                        name, f"cooling down for {cooldown:.0f}s; waited {waited:.0f}s"
                    )
                raise AllProvidersFailedError(failures)

            delay = min(max(1.0, min(rate_limited.values())), self.max_wait_seconds - waited)
            waited += delay
            with self._stats_lock:
                self.stats.rate_limit_waits += 1
                self.stats.seconds_waiting += delay
            log.info(
                "All lanes rate limited (%s); waiting %.0fs",
                ", ".join(f"{n} {s:.0f}s" for n, s in rate_limited.items()),
                delay,
            )
            time.sleep(delay)

    def _call_with_retry(self, provider: Provider, request: LLMRequest) -> LLMResponse:
        """Retry a provider a bounded number of times before giving up on it.

        Transient 5xx responses are common on free tiers and usually clear in
        seconds. Rate limits are *not* retried here - they are the router's
        business, since another lane may be able to serve immediately.
        """
        last: ProviderError | None = None
        for attempt in range(1, self.max_attempts_per_provider + 1):
            try:
                return provider.generate(request)
            except RateLimitedError:
                raise
            except ProviderError as exc:
                last = exc
                if attempt < self.max_attempts_per_provider:
                    time.sleep(self.backoff_seconds * attempt)
        raise last if last else ProviderError(f"{provider.name} failed without an error")
