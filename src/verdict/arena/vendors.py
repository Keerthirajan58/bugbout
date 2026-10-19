"""Which vendor stands behind an agent or an examiner.

Independence is the judge's central rule - no vendor's evidence may vouch for
its own models - so the mapping from a name to a vendor lives in exactly one
place. Names are matched by marker, in order: ``examiner/codex/gpt-6.1-sol``
and ``mini-swe-agent/gpt-oss:20b`` are both OpenAI models, whatever runs them.
"""

from __future__ import annotations

_MARKERS: tuple[tuple[str, str], ...] = (
    ("claude", "anthropic"),
    ("gemini", "google"),
    ("gemma", "google"),
    ("qwen", "alibaba"),
    ("devstral", "mistral"),
    ("codestral", "mistral"),
    ("mistral", "mistral"),
    ("gpt", "openai"),
    ("codex", "openai"),
)


def vendor_of(name: str) -> str:
    """The vendor whose model a name refers to; unknown names are their own vendor."""
    lowered = name.lower()
    for marker, vendor in _MARKERS:
        if marker in lowered:
            return vendor
    return lowered.split("/")[0]
