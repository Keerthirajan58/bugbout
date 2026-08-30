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

# A Prosecutor call costs roughly 1,400 tokens against an 8,000 tokens/minute
# budget, so the theoretical ceiling is ~5.7 calls a minute. Pacing at 11s ran
# at 96% of that ceiling, and any variance - a longer diff, a repair attempt,
# a retrieval block - pushed it over: the phase 2 run drew retry hints of up to
# 2169s and lost cases outright. 15s targets ~70% utilisation, which is slower
# per lane but strictly faster overall than provoking multi-minute cooldowns.
GROQ_MIN_INTERVAL_SECONDS = 15.0

# Retrieval roughly triples the prompt. Measured on the real corpus: a control
# call is ~900 tokens of prompt, a retrieval call adds ~1,712 more, and with the
# response the whole exchange costs ~3,200 tokens against the same 8,000/min
# budget - about 2.5 calls a minute rather than 5.7.
#
# The pacing is raised rather than the context trimmed. Cutting the retrieved
# block to fit the old interval would understate exactly what the ablation is
# meant to measure, and the cost is only wall-clock: four lanes at 1.75 calls a
# minute still finish 115 cases in well under an hour.
GROQ_RETRIEVAL_MIN_INTERVAL_SECONDS = 34.0

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
        # Own model first, then the rest in rotation, so lanes prefer distinct
        # models but no lane is ever a dead end.
        ordered = lane_models[index:] + lane_models[:index]
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
