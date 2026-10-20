"""mini-swe-agent, the SWE-bench team's minimal agent, on any LiteLLM model."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Unpack, override

from bugbout.agents.base import CodingAgent, Identity, Invocation, Session
from bugbout.sandbox import Confinement

PLUGINS = Path(__file__).resolve().parent / "plugins"
SANDBOX_PREFIX_ENV = "BUGBOUT_SANDBOX_PREFIX"


class MiniSweAgent(CodingAgent):
    """``mini`` with an environment class that runs every command in bugbout's sandbox.

    mini-swe-agent runs shell commands directly on the host, so bugbout supplies
    its own environment (``plugins/bugbout_mini_env.py``): each command gets no
    network and may write only in the checkout. Model calls stay in the agent's
    own process, outside the sandbox.

    Text actions - one bash block per reply - suit local models whose server
    cannot parse tool calls reliably.
    """

    harness = "mini-swe-agent"
    executable = "mini"

    def __init__(
        self,
        model: str,
        *,
        text_actions: bool = False,
        step_limit: int = 75,
        model_kwargs: dict[str, str] | None = None,
        **identity: Unpack[Identity],
    ) -> None:
        """``model`` is a LiteLLM model name, such as ``ollama/qwen3-coder:30b``."""
        provider = model.split("/", 1)[0] if "/" in model else self.harness
        identity.setdefault("quota", provider)
        super().__init__(model, **identity)
        self.text_actions = text_actions
        self.step_limit = step_limit
        self.model_kwargs = model_kwargs or {}

    @override
    def invocation(self, session: Session) -> Invocation:
        settings = [f"agent.step_limit={self.step_limit}", f"environment.cwd={session.workdir}"]
        settings += [
            f"model.model_kwargs.{key}={value}" for key, value in self.model_kwargs.items()
        ]
        argv = [
            self.command(),
            "--model", self.model,
            "--task", session.prompt,
            "--yolo",
            "--exit-immediately",
            "--cost-limit", "0",
            "--environment-class", "bugbout_mini_env.SandboxedEnvironment",
            *(["--model-class", "litellm_textbased"] if self.text_actions else []),
            "-c", "mini_textbased.yaml" if self.text_actions else "mini.yaml",
            *[arg for setting in settings for arg in ("-c", setting)],
            "--output", str(session.logdir / "trajectory.json"),
        ]  # fmt: skip
        confinement = Confinement(writable=(session.workdir, session.logdir))
        inherited = os.environ.get("PYTHONPATH")
        env = {
            "PYTHONPATH": os.pathsep.join(filter(None, [str(PLUGINS), inherited])),
            SANDBOX_PREFIX_ENV: json.dumps(session.sandbox.prefix(confinement)),
            "MSWEA_COST_TRACKING": "ignore_errors",
            "MSWEA_CONFIGURED": "true",
            "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        }
        return Invocation(argv, env=env)

    @override
    def parse(self, logdir: Path) -> tuple[str, dict[str, int]]:
        try:
            data = json.loads((logdir / "trajectory.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return "", {}
        said = [m.get("content") for m in data.get("messages", []) if m.get("role") == "assistant"]
        last = next((s for s in reversed(said) if isinstance(s, str) and s.strip()), "")
        info = data.get("info", {})
        usage = {"steps": len(said)}
        if isinstance(stats := info.get("model_stats"), dict):
            usage.update({k: v for k, v in stats.items() if isinstance(v, int)})
        return f"[{info.get('exit_status', '?')}] {last.strip()[-3000:]}", usage

    @override
    def describe(self) -> dict[str, str]:
        return {
            **super().describe(),
            "actions": "text (one bash block per reply)" if self.text_actions else "tool calls",
            "sandbox": "bugbout's sandbox per command: no network, writes only in the checkout",
            "configuration": f"stock mini config, step limit {self.step_limit}",
        }
