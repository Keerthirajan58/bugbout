"""Any coding agent with a command line, configured instead of coded."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import override

from bugbout.agents.base import CodingAgent, Invocation, Session
from bugbout.agents.vendors import vendor_of
from bugbout.errors import ConfigError
from bugbout.sandbox import Confinement

PLACEHOLDERS = ("prompt", "prompt_file", "workdir", "logdir", "model")


class CommandAgent(CodingAgent):
    """Runs a command line, with placeholders filled in for each run.

    In the command, ``{prompt}``, ``{prompt_file}``, ``{workdir}``, ``{logdir}``
    and ``{model}`` are replaced; when it uses neither ``{prompt}`` nor
    ``{prompt_file}``, the prompt arrives on standard input. The whole process
    runs in bugbout's sandbox - it may use the network to reach its model, but
    write only in the checkout, its logs and the ``state_dirs`` it names.
    """

    harness = "command"

    def __init__(
        self,
        command: Sequence[str],
        *,
        name: str,
        model: str = "",
        env: dict[str, str] | None = None,
        state_dirs: Sequence[str] = (),
        sandboxed: bool = True,
        vendor: str | None = None,
        quota: str | None = None,
    ) -> None:
        if not command:
            raise ConfigError(f"agent {name!r}: the command is empty")
        super().__init__(
            model, name=name, vendor=vendor or vendor_of(model or name), quota=quota or name
        )
        self.argv = list(command)
        self.executable = self.argv[0]
        self.env = env or {}
        self.state_dirs = tuple(Path(d).expanduser() for d in state_dirs)
        self.sandboxed = sandboxed

    @override
    def invocation(self, session: Session) -> Invocation:
        prompt_file = session.logdir / "prompt.md"
        prompt_file.write_text(session.prompt, encoding="utf-8")
        values = {
            "prompt": session.prompt,
            "prompt_file": str(prompt_file),
            "workdir": str(session.workdir),
            "logdir": str(session.logdir),
            "model": self.model,
        }
        argv = [self.command(), *(_fill(arg, values) for arg in self.argv[1:])]
        takes_prompt = any("{prompt}" in arg or "{prompt_file}" in arg for arg in self.argv)
        if self.sandboxed:
            writable = (session.workdir, session.logdir, *self.state_dirs)
            argv = session.sandbox.wrap(argv, Confinement(writable=writable, network=True))
        return Invocation(argv, env=self.env, stdin=None if takes_prompt else session.prompt)

    @override
    def describe(self) -> dict[str, str]:
        return {
            **super().describe(),
            "command": " ".join(self.argv),
            "sandbox": "bugbout's sandbox" if self.sandboxed else "none",
        }


def _fill(arg: str, values: dict[str, str]) -> str:
    # Plain replacement, not str.format: arguments may hold braces of their own.
    for key in PLACEHOLDERS:
        arg = arg.replace("{" + key + "}", values[key])
    return arg
