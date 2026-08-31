"""Runtime configuration.

Credentials are optional by design: phases 0 and 1 (scaffold, mutation harness)
must run with no API keys at all, so nothing here is mandatory at import time.
Commands that genuinely need a key ask for it via :meth:`Settings.require`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class MissingCredentialError(RuntimeError):
    """Raised when a command needs a credential the user has not supplied."""

    def __init__(self, field: str, how_to_get: str) -> None:
        super().__init__(f"{field} is not set.\n  How to obtain it: {how_to_get}")
        self.field = field


# Where each credential comes from, surfaced verbatim in the error message so a
# user is never left guessing which console to open.
_CREDENTIAL_HELP = {
    "gemini_api_key": "https://aistudio.google.com -> Get API key (no credit card required)",
    "groq_api_key": "https://console.groq.com -> API Keys -> Create (no credit card required)",
    "github_token": (
        "https://github.com/settings/personal-access-tokens -> Generate new token. "
        "Public repositories, read-only. Raises the API limit from 60/hr to 5000/hr."
    ),
    "database_url": "https://neon.tech -> create a free project -> copy the pooled connection string",
}


class Settings(BaseSettings):
    """Process-wide settings, populated from the environment and ``.env``."""

    model_config = SettingsConfigDict(
        env_prefix="PRECEDENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- credentials (all optional; validated on demand) ---------------------
    gemini_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    github_token: SecretStr | None = None
    database_url: SecretStr | None = None

    # --- agent models --------------------------------------------------------
    # Gemini is primary for its 1500 req/day free tier; Groq absorbs overflow
    # when Gemini returns 429, which is what makes the full eval feasible at $0.
    # Measured against the live free tiers in August 2026. Gemini allows only
    # 20 requests/day per model, so it is used for single-diff work; Groq's
    # per-model buckets (8k tokens/min, 1k requests/day each) carry batch runs.
    gemini_model: str = "gemini-3.7-flash"
    groq_model: str = "openai/gpt-oss-120b"
    llm_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    llm_max_output_tokens: int = Field(default=4096, gt=0)

    # A run must be replayable without spending quota, so responses are cached
    # on disk keyed by a hash of the full request.
    llm_cache_enabled: bool = True

    # --- retrieval -----------------------------------------------------------
    # Local ONNX embeddings: free, rate-limit-free, and deterministic, which the
    # ablation number depends on. Off-the-shelf weights, nothing trained here.
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384
    bm25_weight: float = Field(default=0.5, ge=0.0, le=1.0)
    # Measured: citation precision is 98% on-target, so the signal sits in the
    # first few results. Eight documents cost ~1,712 prompt tokens and tripled
    # the call cost; four at half the body length cost ~500, which both reduces
    # distraction and roughly triples throughput under a fixed token budget.
    retrieval_top_k: int = Field(default=4, gt=0)

    # --- execution -----------------------------------------------------------
    sandbox_backend: Literal["subprocess", "docker"] = "subprocess"
    test_timeout_seconds: int = Field(default=300, gt=0)
    max_parallel_jobs: int = Field(default=4, gt=0)

    def require(self, field: str) -> str:
        """Return a credential's plaintext value, or explain how to obtain it."""
        value = getattr(self, field, None)
        if value is None:
            raise MissingCredentialError(
                f"PRECEDENT_{field.upper()}",
                _CREDENTIAL_HELP.get(field, "see README.md"),
            )
        return value.get_secret_value() if isinstance(value, SecretStr) else str(value)

    def has(self, field: str) -> bool:
        """Whether a credential is present, without raising."""
        return getattr(self, field, None) is not None


def load_settings() -> Settings:
    """Build a :class:`Settings` instance from the current environment."""
    return Settings()
