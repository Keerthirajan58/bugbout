"""Build a router from settings, with a clear message when nothing is configured."""

from __future__ import annotations

from precedent.config import Settings, load_settings
from precedent.llm.base import Provider
from precedent.llm.cache import ResponseCache
from precedent.llm.providers import GeminiProvider, GroqProvider
from precedent.llm.router import LLMRouter


class NoProvidersConfiguredError(RuntimeError):
    """Raised when an agent command runs with no API key set."""

    def __init__(self) -> None:
        super().__init__(
            "No LLM provider is configured. Set at least one of:\n"
            "  PRECEDENT_GEMINI_API_KEY  https://aistudio.google.com -> Get API key\n"
            "  PRECEDENT_GROQ_API_KEY    https://console.groq.com -> API Keys\n"
            "Both are free and neither requires a credit card."
        )


def build_router(settings: Settings | None = None) -> LLMRouter:
    """Assemble the provider chain in priority order.

    Gemini first for its larger context and steadier JSON, Groq second for its
    much higher daily ceiling - so the run degrades to the fallback under load
    instead of stopping.
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
    return LLMRouter(providers, cache=ResponseCache(enabled=settings.llm_cache_enabled))
