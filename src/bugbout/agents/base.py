"""A coding agent, as bugbout sees it: a command line that edits a checkout.

bugbout reimplements no agent. Each adapter drives the same off-the-shelf CLI a
developer runs, headlessly, and judges it only by what it leaves on disk. Two
rules hold for every adapter, because a bout is only as fair as its weakest
contestant:

* **Nothing personal leaks in.** A developer's agent configuration carries
  plugins, memories, MCP servers and house rules. Each adapter starts its CLI
  from a clean configuration, so the comparison is between agents, not set-ups.
* **Nothing from the future leaks in.** Tasks are real bugs with public fixes,
  so web tools are off, shell commands are sandboxed, and the checkout has no
  ``.git`` to read.

Writing an adapter for another agent means implementing :meth:`CodingAgent.invocation`
and, optionally, :meth:`~CodingAgent.prepare` and :meth:`~CodingAgent.parse`.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, ClassVar, TypedDict

from bugbout.agents.vendors import vendor_of
from bugbout.errors import AgentNotAvailableError
from bugbout.sandbox import Sandbox


@dataclass(frozen=True, slots=True)
class AgentRun:
    """How one run of an agent ended, how long it took, and what it cost."""

    agent: str
    exit_code: int | None
    timed_out: bool
    seconds: float
    summary: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.timed_out and not self.error

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentRun:
        return cls(**{k: v for k, v in data.items() if k in set(cls.__slots__)})


@dataclass(frozen=True, slots=True)
class Session:
    """Everything one run of an agent is given."""

    prompt: str
    workdir: Path
    """The checkout the agent works in, and the only place it may change."""

    logdir: Path
    """Where the agent's own logs go."""

    sandbox: Sandbox
    """Confines the agent's commands, for agents that do not confine themselves."""


@dataclass(frozen=True, slots=True)
class Invocation:
    """The command line that starts one run."""

    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)
    """Added to the caller's environment, which the agent inherits for its credentials."""

    stdin: str | None = None
    unset: tuple[str, ...] = ()
    """Variables of the caller's environment the agent must not inherit."""


class Identity(TypedDict, total=False):
    """How a configured agent is told apart from others: overrides for the defaults."""

    name: str
    vendor: str
    quota: str


class CodingAgent(ABC):
    """One coding-agent CLI, configured with one model."""

    harness: ClassVar[str]
    """The agent's own name, such as ``codex``; the default prefix of :attr:`name`."""

    executable: str
    """The command that starts it."""

    def __init__(
        self,
        model: str = "",
        *,
        name: str | None = None,
        vendor: str | None = None,
        quota: str | None = None,
    ) -> None:
        self.model = model
        """The model to run; empty for the CLI's own default."""
        self.name = name or (f"{self.harness}/{model}" if model else self.harness)
        """Unique within a bout; used in reports and paths."""
        self.vendor = vendor or vendor_of(model or self.harness)
        """Who made the model, so the judge can keep evidence independent."""
        self.quota = quota or self.harness
        """The rate limit this agent draws on. Agents sharing one run in a shared lane."""

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.name!r})"

    # --- what an adapter provides ---------------------------------------------------

    @abstractmethod
    def invocation(self, session: Session) -> Invocation:
        """The command line for one run."""

    def prepare(self, workdir: Path) -> None:  # noqa: B027 - an optional hook
        """Set up the checkout before the agent starts, e.g. workspace settings."""

    def parse(self, logdir: Path) -> tuple[str, dict[str, int]]:  # noqa: ARG002 - a default
        """The agent's closing summary and token usage, read from its logs."""
        return "", {}

    def describe(self) -> dict[str, str]:
        """What a reader needs to reproduce this agent's runs."""
        return {
            "harness": self.harness,
            "version": version_of(self.command()),
            "model": self.model or "the CLI's default",
            "vendor": self.vendor,
        }

    # --- running ----------------------------------------------------------------------

    def command(self) -> str:
        """The executable to start: found on ``PATH`` or in a per-user install."""
        return find_executable(self.executable) or self.executable

    def check(self) -> None:
        """Raise :class:`AgentNotAvailableError` unless the agent can be started."""
        if find_executable(self.executable) is None:
            raise AgentNotAvailableError(f"{self.name}: `{self.executable}` is not installed")

    def run(
        self, prompt: str, workdir: Path, logdir: Path, *, sandbox: Sandbox, timeout: int
    ) -> AgentRun:
        """Start the agent in ``workdir``, wait for it, and record how it ended."""
        logdir.mkdir(parents=True, exist_ok=True)
        self.prepare(workdir)
        invocation = self.invocation(Session(prompt, workdir, logdir, sandbox))
        started = time.monotonic()
        code, timed_out, error = _execute(invocation, workdir, logdir, timeout)
        summary, usage = self.parse(logdir)
        return AgentRun(
            agent=self.name,
            exit_code=code,
            timed_out=timed_out,
            seconds=round(time.monotonic() - started, 1),
            summary=summary,
            usage=usage,
            error=error,
        )


def _execute(
    invocation: Invocation, workdir: Path, logdir: Path, timeout: int
) -> tuple[int | None, bool, str]:
    """Run to the end or the timeout: the exit code, whether it timed out, and any error."""
    with (
        (logdir / "stdout.log").open("w", encoding="utf-8") as out,
        (logdir / "stderr.log").open("w", encoding="utf-8") as err,
    ):
        try:
            # A session of its own, so a timeout can take down every process the
            # agent spawned - an orphaned test run would otherwise keep going.
            process = subprocess.Popen(  # noqa: S603 - an adapter's own command line
                invocation.argv,
                cwd=workdir,
                env={**_inherited(invocation.unset), **invocation.env},
                stdin=subprocess.PIPE,
                stdout=out,
                stderr=err,
                text=True,
                start_new_session=True,
            )
        except OSError as exc:
            return None, False, f"could not start {invocation.argv[0]}: {exc}"
        with _RUNNING_LOCK:
            _RUNNING.add(process)
        try:
            process.communicate(input=invocation.stdin, timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill(process)
            return None, True, f"timed out after {timeout}s"
        except BaseException:
            _kill(process)  # interrupted: never leave an agent running unattended
            raise
        finally:
            with _RUNNING_LOCK:
                _RUNNING.discard(process)
    code = process.returncode
    return code, False, "" if code == 0 else f"exit code {code}"


def _inherited(unset: tuple[str, ...]) -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key not in unset}


_RUNNING: set[subprocess.Popen[str]] = set()
_RUNNING_LOCK = threading.Lock()


def stop_all() -> None:
    """Kill every agent still running, with everything it started.

    Agents run in sessions of their own, so an interrupt in the terminal does
    not reach them; whoever handles the interrupt calls this.
    """
    with _RUNNING_LOCK:
        running = list(_RUNNING)
    for process in running:
        _kill(process)


def _kill(process: subprocess.Popen[str]) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()


def find_executable(name: str) -> str | None:
    """``name`` on ``PATH``, or where a per-user npm or uv install puts it."""
    for candidate in (
        shutil.which(name),
        str(Path.home() / ".npm-global" / "bin" / name),
        str(Path.home() / ".local" / "bin" / name),
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def version_of(executable: str) -> str:
    """The first line ``executable --version`` prints."""
    try:
        completed = subprocess.run(  # noqa: S603 - a resolved CLI path and a literal flag
            [executable, "--version"], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    lines = completed.stdout.strip().splitlines()
    return lines[0] if lines else "unknown"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Every JSON object in a line-delimited log, skipping anything else."""
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
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
