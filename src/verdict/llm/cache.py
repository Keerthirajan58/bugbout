"""On-disk cache of model responses.

This is load-bearing for three separate reasons, not just speed:

1. **Cost.** The full evaluation is roughly ten thousand calls across two arms
   and two agents. Free tiers are capped per day, so without a cache the run
   would have to be spread over a week and could not be repeated.
2. **Reproducibility.** The project's headline claim is a comparison between two
   evaluation runs. A cached run replays exactly, so anyone can re-derive the
   number instead of taking it on trust.
3. **Resumability.** A run interrupted at mutant 300 of 454 resumes from 300
   rather than re-spending quota on work already done.

Entries are keyed by a hash of the full request including the model, so changing
a prompt, a temperature, or a provider correctly misses the cache rather than
silently returning a stale answer to a different question.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from verdict.llm.base import LLMResponse
from verdict.logging import get_logger
from verdict.paths import cache_dir

log = get_logger(__name__)


class ResponseCache:
    """A content-addressed store of model responses."""

    def __init__(self, root: Path | None = None, *, enabled: bool = True) -> None:
        self.root = root or (cache_dir() / "llm")
        self.enabled = enabled
        self.hits = 0
        self.misses = 0

    def _path(self, key: str) -> Path:
        # Sharded by the first two characters: a single directory with tens of
        # thousands of entries is slow to list on most filesystems.
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> LLMResponse | None:
        if not self.enabled:
            return None
        path = self._path(key)
        if not path.exists():
            self.misses += 1
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # A half-written entry from an interrupted run is worthless but not
            # fatal; drop it and treat this as a miss.
            log.warning("Discarding corrupt cache entry %s", path)
            path.unlink(missing_ok=True)
            self.misses += 1
            return None
        self.hits += 1
        return LLMResponse(**{**data, "cached": True})

    def put(self, key: str, response: LLMResponse) -> None:
        if not self.enabled:
            return
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write then rename, so an interruption cannot leave a partial entry
        # that a later run would read as valid.
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(response), indent=2), encoding="utf-8")
        temporary.replace(path)

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0

    def stats(self) -> str:
        return f"cache {self.hits} hits / {self.misses} misses ({self.hit_rate:.0%})"
