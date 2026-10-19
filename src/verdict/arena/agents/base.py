"""A coding agent, as the arena sees it: a command line that edits a checkout.

Verdict reimplements no agent. Each adapter drives the same off-the-shelf CLI
a developer runs, headlessly, and the arena judges it only by what it leaves on
disk. Two rules hold for every adapter, because a race is only as fair as its
weakest lane:

* **Nothing personal leaks in.** A developer's agent config carries plugins,
  memories, MCP servers and house rules. Each adapter starts its CLI from a
  clean configuration, so the comparison is between agents, not set-ups.
* **Nothing from the future leaks in.** These are real historical bugs with
  public fixes. Web tools are off, shell commands have no network where the
  CLI's sandbox allows it, and the checkout has no ``.git`` to read.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class RunRecord:
    """What happened when an agent ran: how it ended, how long, at what cost."""

    agent: str
    exit_code: int | None
    timed_out: bool
    seconds: float
    summary: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunRecord:
        return cls(**{k: v for k, v in data.items() if k in set(cls.__slots__)})


class CodingAgent(ABC):
    """One coding-agent CLI, configured with one model."""

    #: Stable identifier, ``harness/model``. Used in paths and artifacts.
    name: str
    #: The quota this agent draws on. Attempts within a lane are capped;
    #: lanes run concurrently, so a slow lane never stalls a fast one.
    lane: str

    @abstractmethod
    def command(self, prompt: str, workdir: Path, logdir: Path) -> tuple[list[str], dict[str, str]]:
        """The argv and extra environment for one attempt."""

    @abstractmethod
    def describe(self) -> dict[str, Any]:
        """Everything a reader needs to reproduce this agent's runs."""

    def prepare(self, workdir: Path) -> None:  # noqa: B027 - an optional hook
        """Per-attempt setup inside the checkout, before the agent starts."""

    def parse(self, logdir: Path) -> tuple[str, dict[str, int]]:
        """The agent's final summary and token usage, read from its own logs."""
        return "", {}

    #: Whether the prompt is fed on stdin rather than passed as an argument.
    prompt_on_stdin: bool = False

    def run(self, prompt: str, workdir: Path, logdir: Path, timeout: int) -> RunRecord:
        """One attempt: start the CLI in ``workdir``, wait, and record how it ended."""
        logdir.mkdir(parents=True, exist_ok=True)
        self.prepare(workdir)
        argv, extra_env = self.command(prompt, workdir, logdir)
        started = time.monotonic()
        timed_out, code, error = False, None, ""
        with (
            (logdir / "transcript.jsonl").open("w", encoding="utf-8") as out,
            (logdir / "stderr.log").open("w", encoding="utf-8") as err,
        ):
            # A new session, so a timeout can take down every process the agent
            # spawned - an orphaned test run would otherwise keep going.
            proc = subprocess.Popen(  # noqa: S603 - argv built from literals and arena paths
                argv,
                cwd=workdir,
                env={**os.environ, **extra_env},
                stdin=subprocess.PIPE,
                stdout=out,
                stderr=err,
                text=True,
                start_new_session=True,
            )
            try:
                proc.communicate(input=prompt if self.prompt_on_stdin else None, timeout=timeout)
                code = proc.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                error = f"timed out after {timeout}s"
        summary, usage = self.parse(logdir)
        return RunRecord(
            agent=self.name,
            exit_code=code,
            timed_out=timed_out,
            seconds=round(time.monotonic() - started, 1),
            summary=summary,
            usage=usage,
            error=error or ("" if code == 0 else f"exit code {code}"),
        )


def binary(name: str) -> str:
    """A CLI's path: on PATH, or where a per-user npm or uv install puts it."""
    for candidate in (
        shutil.which(name),
        str(Path.home() / ".npm-global" / "bin" / name),
        str(Path.home() / ".local" / "bin" / name),
    ):
        if candidate and Path(candidate).exists():
            return candidate
    return name


def version_of(executable: str) -> str:
    try:
        completed = subprocess.run(  # noqa: S603 - a resolved CLI path and a literal flag
            [executable, "--version"], capture_output=True, text=True, timeout=30, check=False
        )
    except OSError:
        return "unavailable"
    return completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else "unknown"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Every JSON object in a line-delimited log, skipping anything else."""
    events: list[dict[str, Any]] = []
    if not path.exists():
        return events
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events
