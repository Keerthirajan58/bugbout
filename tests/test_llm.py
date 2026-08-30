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
