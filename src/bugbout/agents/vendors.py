"""Which vendor made the model behind a name.

Independence is the judge's central rule - no vendor's evidence may vouch for its
own models - so the mapping from a name to a vendor lives in exactly one place.
Names are matched by marker, in order: ``codex/gpt-5.1`` and
``mini-swe-agent/ollama/gpt-oss:20b`` are both OpenAI models, whatever runs them.
"""

from __future__ import annotations

import re

_MARKERS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), vendor)
    for pattern, vendor in (
        (r"claude", "anthropic"),
        (r"gemini|gemma", "google"),
        (r"qwen", "alibaba"),
        (r"devstral|codestral|magistral|mistral", "mistral"),
        (r"deepseek", "deepseek"),
        (r"llama", "meta"),
        (r"grok", "xai"),
        (r"kimi|moonshot", "moonshot"),
        (r"glm", "zhipu"),
        (r"gpt|codex|(?<![a-z0-9])o[1-9](?![0-9])", "openai"),
    )
)


def vendor_of(name: str) -> str:
    """The vendor whose model ``name`` refers to; an unknown name is its own vendor."""
    lowered = name.lower()
    for marker, vendor in _MARKERS:
        if marker.search(lowered):
            return vendor
    return lowered.split("/")[0]
