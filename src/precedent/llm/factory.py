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
# quota. Three limits per model, and the third is the one that actually bounds
# this project:
#
#   8,000 tokens/minute    - advertised in x-ratelimit-limit-tokens
#   1,000 requests/day     - advertised in x-ratelimit-limit-requests
#   200,000 tokens/DAY     - advertised NOWHERE
#
# The daily token cap appears in no response header. It is visible only in the
# body of the 429 it eventually produces ("on tokens per day (TPD): Limit
# 200000, Used 199738, Requested 20072"), which is why the router now logs that
# body: without it, three different problems - per-minute pacing, an oversized
# retry, and an exhausted day - all present as the same word, "rate limited".
#
# It counts the *booking* (prompt + max_tokens), like the per-minute bucket. So
# across four models the whole free tier is 800,000 booked tokens a day, and at
# ~5,250 a call that is about 150 calls - roughly 60 evaluated mutants. A
# two-arm ablation over 115 survivors costs ~2.1M and has never fitted in one
# day; v1 accumulated its numbers across several.
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
# Both intervals are derived from measured token *bookings*, not from estimates
# and not from trial and error. Groq reserves prompt + max_tokens on admission,
# so what a lane can sustain is arithmetic:
#
#   prompt 890-1,633 (sampled on eight real Prosecutor prompts)
#   + the 4,096 cap  = ~5,250 booked per call
#   8,000 / 5,250    = 1.52 calls a minute at 100% of the lane
#
# 44s targets ~90%. Two earlier values were set from guessed prompt sizes and
# both were wrong in the same direction: 25s asked for 124% of the budget, and
# every lane sat in a permanent 429 while the API reported 7,900 of 8,000 tokens
# free. It looked like a quota problem twice, and was arithmetic twice.
GROQ_MIN_INTERVAL_SECONDS = 44.0

# Measured the same way: four retrieved documents add ~950 tokens, so a call
# books ~6,200 and a lane sustains 1.29 a minute. 52s targets ~90%. Eight
# documents at full length cost far more and held the arm well under that; the
# diagnostic showed citation precision is 98% on-target, so the signal lives in
# the first few results and the rest was paying tokens to distract.
GROQ_RETRIEVAL_MIN_INTERVAL_SECONDS = 52.0

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
