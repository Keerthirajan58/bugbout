"""Google's Gemini CLI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import override

from bugbout.agents.base import CodingAgent, Invocation, Session, read_jsonl
from bugbout.sandbox import Confinement

# Workspace settings, not the user's: no web tools, nothing reported home.
_SETTINGS = {
    "tools": {"exclude": ["web_fetch", "google_web_search"]},
    "privacy": {"usageStatisticsEnabled": False},
    "telemetry": {"enabled": False},
}


class GeminiCli(CodingAgent):
    """``gemini --prompt`` in auto-approve mode, inside bugbout's sandbox.

    Web tools are excluded through workspace settings. The whole process runs in
    bugbout's sandbox: it reaches the Gemini API, but writes only in the
    checkout, its logs and Gemini's own state directory.
    """

    harness = "gemini-cli"
    executable = "gemini"

    @override
    def prepare(self, workdir: Path) -> None:
        folder = workdir / ".gemini"  # never part of a measured patch
        folder.mkdir(exist_ok=True)
        (folder / "settings.json").write_text(json.dumps(_SETTINGS, indent=2), encoding="utf-8")

    @override
    def invocation(self, session: Session) -> Invocation:
        argv = [
            self.command(),
            "--prompt", session.prompt,
            *(["--model", self.model] if self.model else []),
            "--approval-mode", "yolo",
            "--output-format", "stream-json",
            "--skip-trust",
        ]  # fmt: skip
        state = Path.home() / ".gemini"
        confinement = Confinement(writable=(session.workdir, session.logdir, state), network=True)
        # Gemini CLI would otherwise start a sandbox of its own inside bugbout's.
        return Invocation(session.sandbox.wrap(argv, confinement), env={"GEMINI_SANDBOX": "false"})

    @override
    def parse(self, logdir: Path) -> tuple[str, dict[str, int]]:
        parts: list[str] = []
        usage: dict[str, int] = {}
        for event in read_jsonl(logdir / "stdout.log"):
            if event.get("type") == "message" and event.get("role") == "assistant":
                parts.append(str(event.get("content", "")))
            stats = event.get("stats") if event.get("type") == "result" else None
            if isinstance(stats, dict):
                usage = {k: v for k, v in stats.items() if isinstance(v, int)}
        return "".join(parts).strip()[-4000:], usage

    @override
    def describe(self) -> dict[str, str]:
        return {
            **super().describe(),
            "sandbox": "bugbout's sandbox: writes only in the checkout and ~/.gemini",
            "configuration": "workspace settings; web_fetch and google_web_search excluded",
        }
