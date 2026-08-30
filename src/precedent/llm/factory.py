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

# A Prosecutor call costs roughly 1,400 tokens, so 8,000 tokens/minute allows
# about 5.7 calls a minute. Pacing at 11s stays under that, which is far cheaper
# than repeatedly discovering the limit with a rejected request.
GROQ_MIN_INTERVAL_SECONDS = 11.0

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
) -> AgentPool:
    """Build one agent pair per model lane, for batch evaluation.

    Each lane holds exactly one model and never falls back to another. That is
    deliberate: cross-model fallback would let a mutant be answered by different
    models in the two ablation arms, which is precisely the confound the
    deterministic assignment exists to prevent. A rate-limited lane waits
    instead, which the measured quotas leave ample room for.
    """
    settings = settings or load_settings()
    if not settings.has("groq_api_key"):
        # Without Groq's per-model quotas there is no pool worth building; fall
        # back to a single lane so the command still runs, just slowly.
        return AgentPool.single(*_pair(build_router(settings), settings, review))

    cache = ResponseCache(enabled=settings.llm_cache_enabled)
    key = settings.require("groq_api_key")
    lane_models = models or GROQ_LANE_MODELS
    pairs = []
    for model in lane_models:
        router = LLMRouter(
            [GroqProvider(key, model=model)],
            cache=cache,
            min_interval=GROQ_MIN_INTERVAL_SECONDS,
        )
        pairs.append(_pair(router, settings, review))
    return AgentPool(pairs=tuple(pairs))


def _pair(router: LLMRouter, settings: Settings, review: bool) -> tuple[Prosecutor, Skeptic]:
    prosecutor = Prosecutor(
        router,
        temperature=settings.llm_temperature,
        max_output_tokens=settings.llm_max_output_tokens,
    )
    return prosecutor, Skeptic(router, review_enabled=review)
