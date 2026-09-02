"""Build agent lanes from settings, sized to the providers' measured limits."""

from __future__ import annotations

from precedent.agents.prosecutor import Prosecutor
from precedent.agents.skeptic import Skeptic
from precedent.config import Settings, load_settings
from precedent.eval.pool import AgentPool
from precedent.llm.base import Provider
from precedent.llm.cache import ResponseCache
from precedent.llm.providers import GeminiProvider, GroqProvider
from precedent.llm.router import LLMRouter

# Groq models that answered a realistic ~800-token prompt, each with its own
# quota: 8,000 tokens/minute and 1,000 requests/day per model. Four independent
# buckets is what makes the full evaluation finish in about an hour instead of a
# day and a half.
GROQ_LANE_MODELS: tuple[str, ...] = (
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "qwen/qwen3.8-27b",
    "qwen/qwen3.6-27b",
)

# Pacing is set from what Groq actually *debits*, which is not what a call
# costs. It reserves `max_tokens` against the 8,000 tokens/minute budget at
# admission and never refunds the difference (measured: an identical 74-token
# prompt at max_tokens=4096 consumed 126 tokens and removed ~4,170 of headroom).
#
# At the 1,536 cap set in `Settings.llm_max_output_tokens`, a Prosecutor call
# books ~1,200 prompt + 1,536 reserved = ~2,750 tokens, so a lane sustains about
# 2.9 calls a minute. 21s targets ~98% of that.
#
# Slightly under the ceiling on purpose. Pacing at 11s once ran at 96% of the
# then-ceiling and any variance tipped it over: the phase 2 run drew retry hints
# of up to 2,169s and lost cases outright. Waiting a little is strictly faster
# than provoking a multi-minute cooldown.
GROQ_MIN_INTERVAL_SECONDS = 21.0

# Retrieval adds ~430 prompt tokens for four tightened documents, so a call
# books ~3,180 and a lane sustains ~2.5 a minute. Eight documents at full length
# cost ~1,712 extra and held the arm to well under that; the diagnostic showed
# citation precision is 98% on-target, so the signal lives in the first few
# results and the rest was paying tokens to distract.
GROQ_RETRIEVAL_MIN_INTERVAL_SECONDS = 24.0

# Gemini's free tier is 20 requests per day *per model*, which cannot sustain a
# multi-thousand-call evaluation. It is kept for single-diff use, where quality
# matters and volume does not.
GEMINI_DEFAULT_MODEL = "gemini-3.7-flash"


class NoProvidersConfiguredError(RuntimeError):
    """Raised when an agent command runs with no API key set."""

    def __init__(self) -> None:
        super().__init__(
            "No LLM provider is configured. Set at least one of:\n"
            "  PRECEDENT_GROQ_API_KEY    https://console.groq.com -> API Keys\n"
            "  PRECEDENT_GEMINI_API_KEY  https://aistudio.google.com -> Get API key\n"
            "Both are free and neither requires a credit card."
        )


def build_router(settings: Settings | None = None) -> LLMRouter:
    """A single router for one-off use, such as ``precedent verify``.

    Ordered for quality first: volume is irrelevant when auditing one diff, and
    Gemini's larger models are the strongest available on a free tier.
    """
    settings = settings or load_settings()
    providers: list[Provider] = []
    if settings.has("gemini_api_key"):
        providers.append(
            GeminiProvider(settings.require("gemini_api_key"), model=settings.gemini_model)
        )
    if settings.has("groq_api_key"):
        providers.append(GroqProvider(settings.require("groq_api_key"), model=settings.groq_model))
    if not providers:
        raise NoProvidersConfiguredError
    return LLMRouter(
        providers,
        cache=ResponseCache(enabled=settings.llm_cache_enabled),
        min_interval={"groq": GROQ_MIN_INTERVAL_SECONDS},
    )


def build_agent_pool(
    settings: Settings | None = None,
    *,
    review: bool = True,
    models: tuple[str, ...] | None = None,
    min_interval: float | None = None,
    allow_fallback: bool = False,
) -> AgentPool:
    """Build one agent pair per model lane, for batch evaluation.

    Each lane *prefers* its own model, and falls back to the others in rotation
    when its own is exhausted.

    The first design gave each lane exactly one model with no fallback, so that
    a mutant could never be answered by different models in the two ablation
    arms. Measurement showed the cost was unacceptable: Groq returned a 1,174s
    reset on one model, and every case assigned to that lane blocked for twenty
    minutes, stalling a run that was 98/115 complete.

    Rotation keeps the intent while removing the stall. Under normal load the
    pacing keeps each lane on its own model, so assignment stays effectively
    deterministic; under exhaustion a case is answered rather than lost. The
    model that actually served is recorded on every case, so pairing is
    *verified* against the record rather than assumed - and the ablation can be
    reported on the same-model subset as a robustness check.
    """
    settings = settings or load_settings()
    if not settings.has("groq_api_key"):
        # Without Groq's per-model quotas there is no pool worth building; fall
        # back to a single lane so the command still runs, just slowly.
        return AgentPool.single(*_pair(build_router(settings), settings, review))

    cache = ResponseCache(enabled=settings.llm_cache_enabled)
    key = settings.require("groq_api_key")
    lane_models = models or GROQ_LANE_MODELS
    interval = min_interval if min_interval is not None else GROQ_MIN_INTERVAL_SECONDS
    pairs = []
    for index in range(len(lane_models)):
        # Own model first, then the rest in rotation.
        #
        # Fallback is OFF by default for evaluation, and that default was
        # chosen the hard way. With fallback on, a rate-limited lane skips to
        # the next model - and because that model already holds cache entries,
        # it answers instantly and therefore always wins. Three separate phase 3
        # runs were routed to a single model this way (110-111 of 115 cases),
        # which silently destroyed the paired comparison the ablation depends
        # on. Waiting is slower; producing the wrong number is worse.
        ordered = lane_models[index:] + lane_models[:index]
        if not allow_fallback:
            ordered = ordered[:1]
        providers: list[Provider] = [GroqProvider(key, model=m) for m in ordered]
        router = LLMRouter(providers, cache=cache, min_interval=interval)
        pairs.append(_pair(router, settings, review))

    return AgentPool(pairs=tuple(pairs))


def _pair(router: LLMRouter, settings: Settings, review: bool) -> tuple[Prosecutor, Skeptic]:
    prosecutor = Prosecutor(
        router,
        temperature=settings.llm_temperature,
        max_output_tokens=settings.llm_max_output_tokens,
    )
    return prosecutor, Skeptic(router, review_enabled=review)
