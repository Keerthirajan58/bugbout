"""The vendor behind a model is recognised from its name."""

from __future__ import annotations

import pytest

from bugbout.agents import vendor_of


@pytest.mark.parametrize(
    ("name", "vendor"),
    [
        ("claude-sonnet-5-5", "anthropic"),
        ("gemini-2.5-pro", "google"),
        ("ollama/qwen3-coder:30b", "alibaba"),
        ("mistral/codestral-latest", "mistral"),
        ("gpt-5.1-codex", "openai"),
        ("o3", "openai"),
        ("deepseek-coder", "deepseek"),
        ("acme/house-model", "acme"),
    ],
)
def test_vendors_are_recognised_by_model_name(name: str, vendor: str) -> None:
    assert vendor_of(name) == vendor
