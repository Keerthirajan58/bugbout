"""OpenAI's Codex CLI, on a ChatGPT plan - or a local open-weights model."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from verdict.arena.agents.base import CodingAgent, binary, read_jsonl, version_of
from verdict.arena.agents.ollama import OllamaServer


class CodexAgent(CodingAgent):
    """``codex exec`` from a clean config, in its own workspace-write sandbox.

    The user's config and rules are ignored, the session is ephemeral, and
    every feature that reaches outside the checkout is switched off. Each model
    runs at its vendor's default reasoning level unless told otherwise: the race
    compares agents as shipped, not as tuned.
    """

    _DISABLED_FEATURES = (
        "apps",
        "browser_use",
        "browser_use_external",
        "browser_use_full_cdp_access",
        "computer_use",
        "image_generation",
        "in_app_browser",
        "plugins",
        "remote_plugin",
        "skill_search",
        "skill_mcp_dependency_install",
        "memories",
    )
    prompt_on_stdin = True

    def __init__(self, model: str, *, effort: str | None = None, local: bool = False) -> None:
        self.model = model
        self.effort = effort
        self.local = local
        self.name = f"codex/{model}"
        self.lane = "ollama" if local else "chatgpt"

    def command(self, prompt: str, workdir: Path, logdir: Path) -> tuple[list[str], dict[str, str]]:
        argv = [
            binary("codex"),
            "exec",
            "--ignore-user-config",
            "--ignore-rules",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "workspace-write",
            "--cd",
            str(workdir),
            "--model",
            self.model,
            *(["-c", f'model_reasoning_effort="{self.effort}"'] if self.effort else []),
            "-c",
            'web_search="disabled"',
            *[flag for feature in self._DISABLED_FEATURES for flag in ("--disable", feature)],
            "--json",
            "--color",
            "never",
            "--output-last-message",
            str(logdir / "last_message.txt"),
        ]
        if self.local:
            argv += ["--oss", "--local-provider", "ollama"]
            return [*argv, "-"], {"CODEX_OSS_PORT": str(OllamaServer.PORT)}
        return [*argv, "-"], {}

    def parse(self, logdir: Path) -> tuple[str, dict[str, int]]:
        last = logdir / "last_message.txt"
        summary = last.read_text(encoding="utf-8").strip() if last.exists() else ""
        usage: dict[str, int] = {}
        for event in read_jsonl(logdir / "transcript.jsonl"):
            if isinstance(found := event.get("usage"), dict):
                for key, value in found.items():
                    if isinstance(value, int):
                        usage[key] = usage.get(key, 0) + value
        return summary, usage

    def describe(self) -> dict[str, Any]:
        return {
            "harness": "codex-cli",
            "version": version_of(binary("codex")),
            "model": self.model,
            "reasoning_effort": self.effort or "model default",
            "provider": "ollama (local)" if self.local else "ChatGPT subscription",
            "sandbox": "codex workspace-write: no shell network, writes only in the checkout",
            "config": "user config and rules ignored; web search and plugins disabled",
        }
