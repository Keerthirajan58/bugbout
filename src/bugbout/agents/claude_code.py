"""Anthropic's Claude Code."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Unpack, override

from bugbout.agents.base import CodingAgent, Identity, Invocation, Session, read_jsonl

# Shell commands run in Claude Code's own sandbox and only there: if the sandbox
# cannot start, Claude Code stops instead of running them unconfined.
_SETTINGS = {
    "sandbox": {
        "enabled": True,
        "failIfUnavailable": True,
        "autoAllowBashIfSandboxed": True,
        "allowUnsandboxedCommands": False,
    }
}


# What a running Claude Code session exports to the commands it runs. bugbout may
# itself be run from inside Claude Code; a contestant is a session of its own and
# must never attach to that one.
PARENT_SESSION = (
    "CLAUDECODE",
    "CLAUDE_AGENT_SDK_VERSION",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_EMIT_STARTUP_TIMING",
    "CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING",
    "CLAUDE_CODE_ENABLE_TASKS",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_QUESTION_PREVIEW_FORMAT",
    "CLAUDE_CODE_SESSION_ATTENDED",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_SSE_PORT",
    "CLAUDE_EFFORT",
    "CLAUDE_PID",
    "ENABLE_IDE_INTEGRATION",
)


class ClaudeCode(CodingAgent):
    """``claude --print`` from a clean configuration, in Claude Code's own sandbox.

    Safe mode leaves out the user's CLAUDE.md, skills, plugins, hooks and MCP
    servers; web tools are disallowed and no session is saved. Edits are
    accepted inside the checkout only, shell commands run sandboxed, and anything
    that would need a person's approval is refused rather than asked about.
    """

    harness = "claude-code"
    executable = "claude"

    def __init__(
        self, model: str = "", *, effort: str | None = None, **identity: Unpack[Identity]
    ) -> None:
        """``model`` is a full model name or an alias such as ``sonnet``; empty for the default."""
        identity.setdefault("vendor", "anthropic")  # an alias names no vendor
        super().__init__(model, **identity)
        self.effort = effort

    @override
    def invocation(self, session: Session) -> Invocation:
        argv = [
            self.command(),
            "--print",
            "--output-format", "stream-json",
            "--verbose",
            *(["--model", self.model] if self.model else []),
            *(["--effort", self.effort] if self.effort else []),
            "--safe-mode",
            "--no-session-persistence",
            "--permission-mode", "acceptEdits",
            "--permission-prompts", "none",
            "--settings", json.dumps(_SETTINGS),
            "--disallowedTools", "WebFetch,WebSearch",
        ]  # fmt: skip
        return Invocation(argv, stdin=session.prompt, unset=PARENT_SESSION)

    @override
    def parse(self, logdir: Path) -> tuple[str, dict[str, int]]:
        for event in reversed(read_jsonl(logdir / "stdout.log")):
            if event.get("type") == "result":
                usage = event.get("usage")
                counts = (
                    {k: v for k, v in usage.items() if isinstance(v, int)}
                    if isinstance(usage, dict)
                    else {}
                )
                if isinstance(turns := event.get("num_turns"), int):
                    counts["turns"] = turns
                return str(event.get("result") or "").strip(), counts
        return "", {}

    @override
    def describe(self) -> dict[str, str]:
        return {
            **super().describe(),
            "effort": self.effort or "model default",
            "sandbox": "Claude Code's sandbox for shell commands; edits only in the checkout",
            "configuration": "safe mode (no CLAUDE.md, skills, plugins, hooks or MCP); no web tools",
        }
