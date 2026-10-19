"""mini-swe-agent - the SWE-bench team's minimal agent - on any LiteLLM model.

It runs shell commands directly on the host, so the arena supplies its own
environment class (``plugins/verdict_mini_env.py``) that wraps every command in
a seatbelt sandbox: no network, writes only inside the attempt's checkout. The
agent's model calls happen in its own process, outside that sandbox.

Two action formats are supported. Tool calls suit hosted models with solid
function calling. Text actions - one bash block per reply - exist for local
models served through Ollama, whose tool-call parser rejected gpt-oss:20b's
heredoc-heavy commands outright.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from verdict.arena.agents.base import CodingAgent, binary, version_of
from verdict.arena.agents.sandbox import command_profile

PLUGINS = Path(__file__).resolve().parent / "plugins"


class MiniSweAgent(CodingAgent):
    def __init__(
        self,
        model: str,
        *,
        lane: str,
        env: dict[str, str] | None = None,
        model_kwargs: dict[str, str] | None = None,
        text_actions: bool = False,
        step_limit: int = 75,
    ) -> None:
        self.model = model
        self.lane = lane
        self.name = f"mini-swe-agent/{model.split('/', 1)[-1]}"
        self._env = env or {}
        self._model_kwargs = model_kwargs or {}
        self._text_actions = text_actions
        self._step_limit = step_limit

    def command(self, prompt: str, workdir: Path, logdir: Path) -> tuple[list[str], dict[str, str]]:
        profile = logdir / "sandbox.sb"
        profile.write_text(command_profile(workdir, logdir), encoding="utf-8")
        config = "mini_textbased.yaml" if self._text_actions else "mini.yaml"
        argv = [
            binary("mini"),
            "--model",
            self.model,
            "--task",
            prompt,
            "--yolo",
            "--exit-immediately",
            "--cost-limit",
            "0",
            "--environment-class",
            "verdict_mini_env.SeatbeltEnvironment",
            *(["--model-class", "litellm_textbased"] if self._text_actions else []),
            "-c",
            config,
            "-c",
            f"agent.step_limit={self._step_limit}",
            "-c",
            f"environment.cwd={workdir}",
            *[
                arg
                for key, value in self._model_kwargs.items()
                for arg in ("-c", f"model.model_kwargs.{key}={value}")
            ],
            "--output",
            str(logdir / "trajectory.json"),
        ]
        env = {
            **self._env,
            "PYTHONPATH": str(PLUGINS),
            "VERDICT_SANDBOX_PROFILE": str(profile),
            "MSWEA_COST_TRACKING": "ignore_errors",
            "MSWEA_CONFIGURED": "true",
            "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        }
        return argv, env

    def parse(self, logdir: Path) -> tuple[str, dict[str, int]]:
        path = logdir / "trajectory.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return "", {}
        messages = data.get("messages", [])
        said = [m.get("content") for m in messages if m.get("role") == "assistant"]
        last = next((s for s in reversed(said) if isinstance(s, str) and s.strip()), "")
        info = data.get("info", {})
        usage = {"steps": len(said)}
        if isinstance(stats := info.get("model_stats"), dict):
            usage.update({k: v for k, v in stats.items() if isinstance(v, int)})
        return f"[{info.get('exit_status', '?')}] {last.strip()[-3000:]}", usage

    def describe(self) -> dict[str, Any]:
        return {
            "harness": "mini-swe-agent",
            "version": version_of(binary("mini")),
            "model": self.model,
            "actions": "text (one bash block per reply)" if self._text_actions else "tool calls",
            "sandbox": "macOS seatbelt per command: no network, writes only in the checkout",
            "config": f"stock mini config, step limit {self._step_limit}",
        }
