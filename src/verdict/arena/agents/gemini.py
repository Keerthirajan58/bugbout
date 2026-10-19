"""Google's Gemini CLI, on the free Gemini API tier.

The adapter works, and the measurement is why it is not in the race: the free
tier allows 5 requests a minute and about 20 a day per model, and a single
task exhausted a model's day before the agent finished. It is kept so the
arena can use it wherever a quota allows.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from verdict.arena.agents.base import CodingAgent, binary, read_jsonl, version_of


class GeminiAgent(CodingAgent):
    def __init__(self, model: str, api_key: str) -> None:
        self.model = model
        self._api_key = api_key
        self.name = f"gemini/{model}"
        self.lane = "gemini"

    def prepare(self, workdir: Path) -> None:
        # Workspace settings, not the user's own: web tools off, no telemetry.
        # `.gemini/` is never part of a measured patch.
        settings = {
            "tools": {"exclude": ["web_fetch", "google_web_search"]},
            "privacy": {"usageStatisticsEnabled": False},
            "telemetry": {"enabled": False},
        }
        folder = workdir / ".gemini"
        folder.mkdir(exist_ok=True)
        (folder / "settings.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")

    def command(self, prompt: str, workdir: Path, logdir: Path) -> tuple[list[str], dict[str, str]]:
        argv = [
            binary("gemini"),
            "--prompt",
            prompt,
            "--model",
            self.model,
            "--approval-mode",
            "yolo",
            "--output-format",
            "stream-json",
            "--skip-trust",
            "--sandbox",
        ]
        env = {
            "GEMINI_API_KEY": self._api_key,
            "GEMINI_SANDBOX": "sandbox-exec",
            "SEATBELT_PROFILE": "permissive-open",
        }
        return argv, env

    def parse(self, logdir: Path) -> tuple[str, dict[str, int]]:
        parts: list[str] = []
        usage: dict[str, int] = {}
        for event in read_jsonl(logdir / "transcript.jsonl"):
            if event.get("type") == "message" and event.get("role") == "assistant":
                parts.append(str(event.get("content", "")))
            stats = event.get("stats") if event.get("type") == "result" else None
            if isinstance(stats, dict):
                usage = {k: v for k, v in stats.items() if isinstance(v, int)}
        return "".join(parts).strip()[-4000:], usage

    def describe(self) -> dict[str, Any]:
        return {
            "harness": "gemini-cli",
            "version": version_of(binary("gemini")),
            "model": self.model,
            "provider": "Gemini API free tier",
            "sandbox": "macOS seatbelt (permissive-open)",
            "config": "workspace settings only; web_fetch and google_web_search excluded",
        }
