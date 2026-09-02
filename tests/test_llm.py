"""Tests for provider routing and caching.

The router exists because every provider here is on a free tier with a daily
cap, and the evaluation is roughly ten thousand calls. Failover behaviour is
therefore not a nicety - a router that stalls on the first 429 would make the
full run impossible.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from precedent.llm.base import LLMRequest, LLMResponse, ProviderError, RateLimitedError
from precedent.llm.cache import ResponseCache
from precedent.llm.router import AllProvidersFailedError, LLMRouter
from tests.conftest import FakeProvider


@pytest.fixture
def request_() -> LLMRequest:
    return LLMRequest(system="sys", user="usr")


@pytest.fixture
def cache(tmp_path: Path) -> ResponseCache:
    return ResponseCache(root=tmp_path)


class TestCacheKey:
    def test_identical_requests_share_a_key(self, request_: LLMRequest) -> None:
        assert request_.cache_key("m") == LLMRequest(system="sys", user="usr").cache_key("m")

    def test_model_is_part_of_the_key(self, request_: LLMRequest) -> None:
        """Gemini's and Groq's answers to the same prompt are different results
        and must never share a cache entry."""
        assert request_.cache_key("gemini") != request_.cache_key("groq")

    @pytest.mark.parametrize(
        "field_changes",
        [
            {"user": "different"},
            {"system": "different"},
            {"temperature": 0.9},
            {"json_output": False},
        ],
    )
    def test_every_field_affects_the_key(self, request_: LLMRequest, field_changes: dict) -> None:
        """A changed prompt must miss the cache. Otherwise an edited prompt
        would silently return the answer to the previous question."""
        from dataclasses import replace

        assert replace(request_, **field_changes).cache_key("m") != request_.cache_key("m")


class TestJSONParsing:
    def _response(self, text: str) -> LLMResponse:
        return LLMResponse(text=text, provider="p", model="m")

    def test_parses_plain_json(self) -> None:
        assert self._response('{"a": 1}').json() == {"a": 1}

    def test_strips_markdown_fences(self) -> None:
        """Models emit fenced JSON even when told not to. Failing the whole
        evaluation on formatting would be absurd."""
        assert self._response('```json\n{"a": 1}\n```').json() == {"a": 1}

    def test_extracts_json_after_a_preamble(self) -> None:
        assert self._response('Sure! Here it is:\n{"a": 1}').json() == {"a": 1}

    def test_ignores_braces_inside_strings(self) -> None:
        """Generated test code is full of braces; a naive brace-counter would
        truncate the object at the first one inside a string literal."""
        parsed = self._response('{"code": "d = {\'k\': 1}", "n": 2}').json()
        assert parsed["n"] == 2

    def test_raises_on_unparseable_text(self) -> None:
        with pytest.raises(ValueError):
            self._response("no json here at all").json()


class TestCache:
    def test_miss_then_hit(self, cache: ResponseCache) -> None:
        assert cache.get("k") is None
        cache.put("k", LLMResponse(text="t", provider="p", model="m"))
        assert cache.get("k") is not None

    def test_hit_is_marked_as_cached(self, cache: ResponseCache) -> None:
        cache.put("k", LLMResponse(text="t", provider="p", model="m"))
        hit = cache.get("k")
        assert hit is not None and hit.cached

    def test_disabled_cache_stores_nothing(self, tmp_path: Path) -> None:
        disabled = ResponseCache(root=tmp_path, enabled=False)
        disabled.put("k", LLMResponse(text="t", provider="p", model="m"))
        assert disabled.get("k") is None

    def test_corrupt_entry_is_treated_as_a_miss(self, cache: ResponseCache) -> None:
        """An interrupted run can leave a half-written entry. It must not crash
        a later run, and it must not be read as valid."""
        cache.put("k", LLMResponse(text="t", provider="p", model="m"))
        cache._path("k").write_text("{ truncated")
        assert cache.get("k") is None


class TestRouterFailover:
    def test_uses_the_primary_when_it_works(
        self, request_: LLMRequest, cache: ResponseCache
    ) -> None:
        primary = FakeProvider(name="primary", responses=["ok"])
        secondary = FakeProvider(name="secondary", responses=["never"])
        router = LLMRouter([primary, secondary], cache=cache)
        assert router.generate(request_).provider == "primary"
        assert secondary.calls == 0

    def test_falls_over_when_the_primary_is_rate_limited(
        self, request_: LLMRequest, cache: ResponseCache
    ) -> None:
        """The expected steady state on a free tier: the day's Gemini quota is
        spent and the run continues on Groq rather than stopping."""
        primary = FakeProvider(name="primary", error=RateLimitedError("primary"))
        secondary = FakeProvider(name="secondary", responses=["ok"])
        router = LLMRouter([primary, secondary], cache=cache)
        assert router.generate(request_).provider == "secondary"
        assert router.stats.failovers == 1

    def test_a_rate_limit_is_not_retried_on_the_same_provider(
        self, request_: LLMRequest, cache: ResponseCache
    ) -> None:
        """A spent daily quota will not clear in seconds. Retrying would just
        burn wall-clock before the inevitable failover."""
        primary = FakeProvider(name="primary", error=RateLimitedError("primary"))
        router = LLMRouter([primary, FakeProvider(name="s", responses=["ok"])], cache=cache)
        router.generate(request_)
        assert primary.calls == 1

    def test_transient_errors_are_retried_before_failing_over(
        self, request_: LLMRequest, cache: ResponseCache
    ) -> None:
        primary = FakeProvider(name="primary", error=ProviderError("503"))
        router = LLMRouter(
            [primary, FakeProvider(name="s", responses=["ok"])],
            cache=cache,
            max_attempts_per_provider=3,
            backoff_seconds=0.0,
        )
        router.generate(request_)
        assert primary.calls == 3

    def test_raises_when_every_provider_fails(
        self, request_: LLMRequest, cache: ResponseCache
    ) -> None:
        router = LLMRouter(
            [
                FakeProvider(name="a", error=ProviderError("x")),
                FakeProvider(name="b", error=ProviderError("y")),
            ],
            cache=cache,
            max_attempts_per_provider=1,
        )
        with pytest.raises(AllProvidersFailedError, match="No provider could serve"):
            router.generate(request_)

    def test_a_cache_hit_never_reaches_a_provider(
        self, request_: LLMRequest, cache: ResponseCache
    ) -> None:
        """This is what makes a re-run free, and what makes the published
        numbers reproducible rather than merely asserted."""
        provider = FakeProvider(name="p", responses=["first"])
        router = LLMRouter([provider], cache=cache)
        router.generate(request_)
        router.generate(request_)
        assert provider.calls == 1
        assert router.stats.cache_hits == 1

    def test_requires_at_least_one_provider(self) -> None:
        with pytest.raises(ValueError, match="at least one provider"):
            LLMRouter([])


class TestRateLimitWaiting:
    """Measured behaviour drove this design.

    Groq's gpt-oss-120b allows 8,000 tokens per minute and a Prosecutor call
    costs about 1,400 - roughly five calls a minute regardless of worker count.
    An early pilot with four workers lost 29 of 32 requests, because the router
    treated a rate limit as a dead end. It is a temporary condition with a
    published reset window, so the router now waits it out.
    """

    def test_waits_and_retries_when_the_only_lane_is_rate_limited(
        self, request_: LLMRequest, cache: ResponseCache
    ) -> None:
        provider = FakeProvider(
            name="only",
            responses=["ok"],
            error=RateLimitedError("only", retry_after=0.01),
            fail_first=1,
        )
        router = LLMRouter([provider], cache=cache, max_wait_seconds=5.0)
        assert router.generate(request_).text == "ok"
        assert router.stats.rate_limit_waits >= 1

    def test_gives_up_after_the_wait_budget(
        self, request_: LLMRequest, cache: ResponseCache
    ) -> None:
        """Bounded so a wedged run surfaces instead of hanging forever."""
        provider = FakeProvider(name="p", error=RateLimitedError("p", retry_after=0.01))
        router = LLMRouter([provider], cache=cache, max_wait_seconds=0.05)
        with pytest.raises(AllProvidersFailedError):
            router.generate(request_)

    def test_a_healthy_lane_serves_while_another_cools_down(
        self, request_: LLMRequest, cache: ResponseCache
    ) -> None:
        """Failover is what keeps the run moving; waiting is the last resort."""
        limited = FakeProvider(name="limited", error=RateLimitedError("limited", retry_after=60))
        healthy = FakeProvider(name="healthy", model="m2", responses=["ok"])
        router = LLMRouter([limited, healthy], cache=cache)
        assert router.generate(request_).provider == "healthy"
        assert router.stats.rate_limit_waits == 0

    def test_a_cooled_down_lane_is_skipped_without_calling_it(
        self, request_: LLMRequest, cache: ResponseCache
    ) -> None:
        """Once a lane reports a 60s reset, hammering it again wastes quota."""
        limited = FakeProvider(name="limited", error=RateLimitedError("limited", retry_after=60))
        healthy = FakeProvider(name="healthy", model="m2", responses=["a", "b"])
        router = LLMRouter([limited, healthy], cache=cache)
        router.generate(request_)
        router.generate(LLMRequest(system="s", user="different"))
        assert limited.calls == 1

    def test_non_rate_limit_failures_do_not_trigger_waiting(
        self, request_: LLMRequest, cache: ResponseCache
    ) -> None:
        """A malformed request will never succeed; waiting on it burns time."""
        router = LLMRouter(
            [FakeProvider(name="p", error=ProviderError("bad request"))],
            cache=cache,
            max_attempts_per_provider=1,
            max_wait_seconds=5.0,
        )
        with pytest.raises(AllProvidersFailedError):
            router.generate(request_)
        assert router.stats.rate_limit_waits == 0


class TestPacing:
    def test_min_interval_spaces_calls(self, request_: LLMRequest, cache: ResponseCache) -> None:
        """Pacing keeps the run under the limit rather than repeatedly
        rediscovering it with a failed request."""
        import time

        provider = FakeProvider(name="p", responses=["a", "b"])
        router = LLMRouter([provider], cache=cache, min_interval=0.05)
        started = time.monotonic()
        router.generate(request_)
        router.generate(LLMRequest(system="s", user="second"))
        assert time.monotonic() - started >= 0.05

    def test_per_provider_intervals_can_differ(self, cache: ResponseCache) -> None:
        """Quotas are per model, so pacing has to be too."""
        router = LLMRouter(
            [FakeProvider(name="a", responses=["x"]), FakeProvider(name="b", model="m2")],
            cache=cache,
            min_interval={"a": 0.5, "b": 0.0},
        )
        assert router.lanes[0].min_interval == 0.5
        assert router.lanes[1].min_interval == 0.0


class TestCacheDoesNotOverrideModelChoice:
    """Cache residue must not decide which model answers.

    The router originally scanned every lane's cache before calling anything,
    so a cached answer from *any* model satisfied the request. In a phase 3 run
    that silently routed 111 of 115 cases to one model regardless of the lane
    they were assigned, because that model happened to hold cache entries -
    wrecking the paired comparison the ablation depends on.
    """

    def test_prefers_the_first_lane_over_a_later_lanes_cache(
        self, request_: LLMRequest, cache: ResponseCache
    ) -> None:
        preferred = FakeProvider(name="p", model="preferred", responses=["fresh"])
        other = FakeProvider(name="p", model="other")
        # A cached answer exists, but for the *second* lane's model.
        cache.put(
            request_.cache_key("other"),
            LLMResponse(text="stale", provider="p", model="other"),
        )
        router = LLMRouter([preferred, other], cache=cache)
        response = router.generate(request_)
        assert response.model == "preferred"
        assert response.text == "fresh"

    def test_still_uses_the_cache_for_the_preferred_model(
        self, request_: LLMRequest, cache: ResponseCache
    ) -> None:
        provider = FakeProvider(name="p", model="preferred")
        cache.put(
            request_.cache_key("preferred"),
            LLMResponse(text="cached", provider="p", model="preferred"),
        )
        router = LLMRouter([provider], cache=cache)
        assert router.generate(request_).text == "cached"
        assert provider.calls == 0

    def test_a_later_lanes_cache_is_used_when_the_first_is_unavailable(
        self, request_: LLMRequest, cache: ResponseCache
    ) -> None:
        """Falling back is still better than losing the case."""
        first = FakeProvider(name="p", model="first", error=RateLimitedError("p", retry_after=600))
        second = FakeProvider(name="p", model="second")
        cache.put(
            request_.cache_key("second"),
            LLMResponse(text="cached", provider="p", model="second"),
        )
        router = LLMRouter([first, second], cache=cache)
        assert router.generate(request_).model == "second"


class TestTruncationRetry:
    """A budget too small for one answer must not be recorded as a refusal.

    Groq reserves `max_tokens` against the tokens-per-minute allowance whether
    or not it is used, so the cap is set for the common case. When it binds,
    the call is retried once with more room. Getting this wrong is expensive in
    a specific way: Groq reports a truncated JSON response as a 400, so the
    case lands as "the Prosecutor produced no test" - which reads as the agent
    declining rather than as the harness failing. Four cases were lost that way
    before this existed.
    """

    @staticmethod
    def _provider(monkeypatch, responses):  # type: ignore[no-untyped-def]
        import httpx

        from precedent.llm import providers

        sent: list[int] = []

        def fake_post(url, **kwargs):  # type: ignore[no-untyped-def]
            sent.append(kwargs["json"]["max_tokens"])
            return responses.pop(0)

        monkeypatch.setattr(providers.httpx, "post", fake_post)
        return providers.GroqProvider("key", model="openai/gpt-oss-120b"), sent, httpx

    @staticmethod
    def _ok(httpx_module):  # type: ignore[no-untyped-def]
        return httpx_module.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            },
        )

    @staticmethod
    def _truncated(httpx_module):  # type: ignore[no-untyped-def]
        return httpx_module.Response(
            400,
            json={
                "error": {
                    "code": "json_validate_failed",
                    "message": "Failed to generate JSON. max completion tokens reached before "
                    "generating a valid document",
                }
            },
        )

    def test_a_truncated_call_is_retried_with_more_room(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        import httpx as httpx_module

        provider, sent, _ = self._provider(
            monkeypatch, [self._truncated(httpx_module), self._ok(httpx_module)]
        )
        response = provider.generate(LLMRequest(system="s", user="u", max_output_tokens=1536))
        assert response.text == '{"ok": true}'
        assert sent == [1536, 4608]

    def test_the_retry_budget_is_capped(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """A pathological answer must not book an entire minute's allowance."""
        import httpx as httpx_module

        from precedent.llm.providers import MAX_OUTPUT_CEILING

        provider, sent, _ = self._provider(
            monkeypatch, [self._truncated(httpx_module), self._ok(httpx_module)]
        )
        provider.generate(LLMRequest(system="s", user="u", max_output_tokens=4000))
        assert sent == [4000, MAX_OUTPUT_CEILING]

    def test_only_one_retry_is_spent(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """If it truncates again the error surfaces, rather than looping."""
        import httpx as httpx_module

        provider, sent, _ = self._provider(
            monkeypatch, [self._truncated(httpx_module), self._truncated(httpx_module)]
        )
        with pytest.raises(ProviderError, match="400"):
            provider.generate(LLMRequest(system="s", user="u", max_output_tokens=1536))
        assert len(sent) == 2

    def test_a_genuinely_bad_request_is_not_retried(self, monkeypatch) -> None:
        """Only the truncation signature triggers a retry. Spending a second
        call on a malformed prompt would waste the quota this exists to save."""
        import httpx as httpx_module

        bad = httpx_module.Response(
            400, json={"error": {"code": "invalid_request_error", "message": "bad model"}}
        )
        provider, sent, _ = self._provider(monkeypatch, [bad])
        with pytest.raises(ProviderError, match="400"):
            provider.generate(LLMRequest(system="s", user="u", max_output_tokens=1536))
        assert len(sent) == 1

    def test_a_rate_limit_is_still_a_rate_limit(self, monkeypatch) -> None:
        """429 must keep failing over rather than being mistaken for truncation."""
        import httpx as httpx_module

        provider, sent, _ = self._provider(
            monkeypatch, [httpx_module.Response(429, json={"error": {"message": "slow down"}})]
        )
        with pytest.raises(RateLimitedError):
            provider.generate(LLMRequest(system="s", user="u", max_output_tokens=1536))
        assert len(sent) == 1
