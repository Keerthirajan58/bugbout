"""Configuration must never require a credential before it is actually used."""

from __future__ import annotations

import pytest

from precedent.config import MissingCredentialError, Settings


def test_settings_construct_with_no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Phases 0 and 1 must be runnable on a machine with no API keys."""
    for var in ("GEMINI_API_KEY", "GROQ_API_KEY", "GITHUB_TOKEN", "DATABASE_URL"):
        monkeypatch.delenv(f"PRECEDENT_{var}", raising=False)
    settings = Settings(_env_file=None)
    assert settings.gemini_api_key is None
    assert not settings.has("gemini_api_key")


def test_require_explains_how_to_obtain_a_missing_credential() -> None:
    settings = Settings(_env_file=None, gemini_api_key=None)
    with pytest.raises(MissingCredentialError) as exc:
        settings.require("gemini_api_key")
    message = str(exc.value)
    assert "PRECEDENT_GEMINI_API_KEY" in message
    assert "aistudio.google.com" in message


def test_require_returns_the_plaintext_secret() -> None:
    settings = Settings(_env_file=None, gemini_api_key="abc123")  # type: ignore[arg-type]
    assert settings.require("gemini_api_key") == "abc123"


def test_secrets_do_not_leak_through_repr() -> None:
    settings = Settings(_env_file=None, github_token="ghp_supersecret")  # type: ignore[arg-type]
    assert "ghp_supersecret" not in repr(settings)


def test_settings_read_the_precedent_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRECEDENT_GROQ_API_KEY", "gsk_test")
    assert Settings(_env_file=None).require("groq_api_key") == "gsk_test"


@pytest.mark.parametrize(
    ("field", "value"),
    [("llm_temperature", 3.0), ("retrieval_top_k", 0), ("max_parallel_jobs", 0)],
)
def test_out_of_range_values_are_rejected(field: str, value: float) -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, **{field: value})
