"""OpenAI's Codex CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Unpack, override

from bugbout.agents.base import CodingAgent, Identity, Invocation, Session, read_jsonl


class Codex(CodingAgent):
    """``codex exec`` from a clean configuration, in Codex's own sandbox.

    The user's config and rules are ignored, the session is ephemeral, and every
    feature that reaches beyond the checkout is switched off. Shell commands run
    in Codex's ``workspace-write`` sandbox: no network, writes only in the
    checkout. A model runs at its default reasoning effort unless told otherwise,
    so agents are compared as shipped, not as tuned.
    """

    harness = "codex"
    executable = "codex"

    # Switched off through config overrides, which a Codex version that does not
    # know a feature ignores - where ``--disable`` would refuse to start.
    DISABLED_FEATURES = (
        "apps",
        "browser_use",
        "browser_use_external",
        "browser_use_full_cdp_access",
        "computer_use",
        "hooks",
        "image_generation",
        "in_app_browser",
        "memories",
        "plugins",
        "remote_plugin",
        "skill_mcp_dependency_install",
        "skill_search",
    )

    def __init__(
        self,
        model: str,
        *,
        reasoning_effort: str | None = None,
        local_provider: str | None = None,
        **identity: Unpack[Identity],
    ) -> None:
        """``local_provider`` (``ollama`` or ``lmstudio``) serves an open model locally."""
        identity.setdefault("quota", local_provider or self.harness)
        super().__init__(model, **identity)
        self.reasoning_effort = reasoning_effort
        self.local_provider = local_provider

    @override
    def invocation(self, session: Session) -> Invocation:
        effort = (
            ["-c", f'model_reasoning_effort="{self.reasoning_effort}"']
            if self.reasoning_effort
            else []
        )
        local = ["--oss", "--local-provider", self.local_provider] if self.local_provider else []
        argv = [
            self.command(),
            "exec",
            "--ignore-user-config",
            "--ignore-rules",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox", "workspace-write",
            "--cd", str(session.workdir),
            "--model", self.model,
            *effort,
            *local,
            "-c", 'web_search="disabled"',
            *[arg for feature in self.DISABLED_FEATURES for arg in ("-c", f"features.{feature}=false")],
            "--json",
            "--color", "never",
            "--output-last-message", str(session.logdir / "last_message.txt"),
            "-",
        ]  # fmt: skip
        return Invocation(argv, stdin=session.prompt)

    @override
    def parse(self, logdir: Path) -> tuple[str, dict[str, int]]:
        last = logdir / "last_message.txt"
        summary = last.read_text(encoding="utf-8").strip() if last.exists() else ""
        usage: dict[str, int] = {}
        for event in read_jsonl(logdir / "stdout.log"):
            if isinstance(found := event.get("usage"), dict):
                for key, value in found.items():
                    if isinstance(value, int):
                        usage[key] = usage.get(key, 0) + value
        return summary, usage

    @override
    def describe(self) -> dict[str, str]:
        return {
            **super().describe(),
            "reasoning_effort": self.reasoning_effort or "model default",
            "provider": f"{self.local_provider} (local)" if self.local_provider else "OpenAI",
            "sandbox": "Codex workspace-write: no network for commands, writes only in the checkout",
            "configuration": "user config and rules ignored; web search, plugins and hooks off",
        }
