"""The coding agents that compete, each driven headlessly through its own CLI.

Verdict reimplements none of them. A contestant is the same off-the-shelf
binary a developer runs, given the same prompt, in its own history-free
checkout - and judged only by what it leaves on disk.

Two rules hold for every adapter, because a race is only as fair as its
weakest lane:

* **Nothing personal leaks in.** A developer's agent config carries plugins,
  memories, MCP servers and house rules. Each adapter starts its CLI from a
  clean configuration, so the comparison is between agents, not between
  whoever set theirs up more carefully.
* **Nothing from the future leaks in.** These are real historical bugs whose
  fixes are public. Web search and fetch tools are switched off, shell network
  access is denied where the CLI's sandbox allows it, and the checkout has no
  ``.git`` directory to read.
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

PROMPT = """\
You are resolving an issue in the {repo} repository. Your working directory is a checkout of \
the repository at a commit where the issue below is still open. There is no git history.

<issue>
# {title}

{statement}
</issue>

Do the following:
1. Fix the issue by editing the library's source code (under `{source_root}/`).
2. Write a regression test for this issue in a NEW file, `{test_root}/test_issue_regression.py`. \
It must fail on the code as it is now and pass once your fix is in place. Do not modify or \
delete existing tests.
3. Run the tests to check your work with this exact command, which points Python at this \
checkout:
   {runner} {test_root}/test_issue_regression.py
   Pass any other test file, or a -k expression, the same way.

Work only inside this directory, and do not download the project or look it up online. When \
you are finished, reply with a short summary of what you changed and why.
"""

# The file each contestant is asked to put its own reproduction test in.
REGRESSION_TEST = "test_issue_regression.py"


@dataclass(frozen=True, slots=True)
class RunRecord:
    """What happened when one contestant attempted one task."""

    entrant: str
    task_id: str
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


class Contestant(ABC):
    """One coding agent, as a command line."""

    #: Stable identifier, ``harness/model``. Used in paths and artifacts.
    name: str
    #: Which quota this contestant draws on. Runs in one lane are serialised;
    #: lanes run concurrently, so a slow local model never stalls a cloud one.
    lane: str

    @abstractmethod
    def command(self, prompt: str, workdir: Path, logdir: Path) -> tuple[list[str], dict[str, str]]:
        """The argv and extra environment for one attempt."""

    @abstractmethod
    def describe(self) -> dict[str, Any]:
        """Everything about this contestant a reader needs to reproduce the run."""

    def prepare(self, workdir: Path) -> None:  # noqa: B027 - optional hook
        """Per-attempt setup inside the checkout, before the agent starts."""

    def parse(self, logdir: Path) -> tuple[str, dict[str, int]]:
        """The agent's final summary and token usage, from its own logs."""
        return "", {}

    def solve(self, prompt: str, workdir: Path, logdir: Path, timeout: int) -> RunRecord:
        logdir.mkdir(parents=True, exist_ok=True)
        self.prepare(workdir)
        argv, extra_env = self.command(prompt, workdir, logdir)
        env = {**os.environ, **extra_env}
        started = time.monotonic()
        timed_out, code, error = False, None, ""
        with (
            (logdir / "transcript.jsonl").open("w", encoding="utf-8") as out,
            (logdir / "stderr.log").open("w", encoding="utf-8") as err,
        ):
            # A new session, so a timeout can take down every process the
            # agent spawned - an orphaned test run would otherwise keep going.
            proc = subprocess.Popen(
                argv,
                cwd=workdir,
                env=env,
                stdin=subprocess.PIPE,
                stdout=out,
                stderr=err,
                text=True,
                start_new_session=True,
            )
            try:
                proc.communicate(input=prompt if "-" in argv[-1:] else None, timeout=timeout)
                code = proc.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                error = f"timed out after {timeout}s"
        summary, usage = self.parse(logdir)
        return RunRecord(
            entrant=self.name,
            task_id="",
            exit_code=code,
            timed_out=timed_out,
            seconds=round(time.monotonic() - started, 1),
            summary=summary,
            usage=usage,
            error=error or ("" if code == 0 else f"exit code {code}"),
        )


def _version(binary: str) -> str:
    try:
        return subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=30, check=False
        ).stdout.strip()
    except OSError:
        return "unavailable"


def _binary(name: str) -> str:
    return shutil.which(name) or str(Path.home() / ".npm-global" / "bin" / name)


class OllamaServer:
    """A private Ollama server for the local lane, with a context window an agent fits in.

    Ollama sizes its default context from available VRAM, and on a 24 GB laptop
    that default is 4,096 tokens - less than a coding agent's system prompt and
    tool definitions, before the task has even been read. Rather than change the
    user's own Ollama service, the arena runs its own on a separate port and
    stops it afterwards. Models are shared on disk, so nothing is downloaded twice.
    """

    PORT = 11435
    CONTEXT = 65536

    def __init__(self) -> None:
        self._proc: subprocess.Popen[bytes] | None = None

    def _up(self) -> bool:
        import urllib.request

        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.PORT}/api/version", timeout=2):
                return True
        except OSError:
            return False

    def __enter__(self) -> OllamaServer:
        if not self._up():
            env = {
                **os.environ,
                "OLLAMA_HOST": f"127.0.0.1:{self.PORT}",
                "OLLAMA_CONTEXT_LENGTH": str(self.CONTEXT),
                "OLLAMA_KEEP_ALIVE": "30m",
            }
            self._proc = subprocess.Popen(
                [_binary("ollama"), "serve"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            for _ in range(60):
                if self._up():
                    break
                time.sleep(0.5)
            else:
                raise RuntimeError("the arena's Ollama server did not come up")
        return self

    def __exit__(self, *_: object) -> None:
        if self._proc is not None:
            self._proc.terminate()
            self._proc.wait(timeout=30)


class CodexContestant(Contestant):
    """OpenAI's Codex CLI - with a ChatGPT model, or a local open-weights one."""

    # Features that reach outside the checkout or depend on the user's setup.
    _DISABLED = (
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

    def __init__(self, model: str, *, effort: str | None = None, local: bool = False) -> None:
        self.model = model
        self.effort = effort
        self.local = local
        self.name = f"codex/{model}"
        self.lane = "ollama" if local else "chatgpt"

    def command(self, prompt: str, workdir: Path, logdir: Path) -> tuple[list[str], dict[str, str]]:
        argv = [
            _binary("codex"),
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
            # Each model at its vendor's default reasoning level unless told
            # otherwise: the race compares agents as shipped, not as tuned.
            *(["-c", f'model_reasoning_effort="{self.effort}"'] if self.effort else []),
            "-c",
            'web_search="disabled"',
            *[flag for feature in self._DISABLED for flag in ("--disable", feature)],
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
        for event in _jsonl(logdir / "transcript.jsonl"):
            if isinstance(found := event.get("usage"), dict):
                for key, value in found.items():
                    if isinstance(value, int):
                        usage[key] = usage.get(key, 0) + value
        return summary, usage

    def describe(self) -> dict[str, Any]:
        return {
            "harness": "codex-cli",
            "version": _version(_binary("codex")),
            "model": self.model,
            "reasoning_effort": self.effort or "model default",
            "provider": "ollama (local)" if self.local else "ChatGPT subscription",
            "sandbox": "workspace-write, no shell network",
            "config": "user config and rules ignored; web search and plugins disabled",
        }


class GeminiContestant(Contestant):
    """Google's Gemini CLI, on the free Gemini API tier."""

    def __init__(self, model: str, api_key: str) -> None:
        self.model = model
        self.api_key = api_key
        self.name = f"gemini/{model}"
        self.lane = "gemini"

    def prepare(self, workdir: Path) -> None:
        # Workspace settings rather than the user's own: web tools off, no
        # telemetry. `.gemini/` is never part of a measured patch.
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
            _binary("gemini"),
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
            "GEMINI_API_KEY": self.api_key,
            "GEMINI_SANDBOX": "sandbox-exec",
            "SEATBELT_PROFILE": "permissive-open",
        }
        return argv, env

    def parse(self, logdir: Path) -> tuple[str, dict[str, int]]:
        parts: list[str] = []
        usage: dict[str, int] = {}
        for event in _jsonl(logdir / "transcript.jsonl"):
            if event.get("type") == "message" and event.get("role") == "assistant":
                parts.append(str(event.get("content", "")))
            stats = event.get("stats") if event.get("type") == "result" else None
            if isinstance(stats, dict):
                usage = {k: v for k, v in stats.items() if isinstance(v, int)}
        return "".join(parts).strip()[-4000:], usage

    def describe(self) -> dict[str, Any]:
        return {
            "harness": "gemini-cli",
            "version": _version(_binary("gemini")),
            "model": self.model,
            "provider": "Gemini API free tier",
            "sandbox": "macOS seatbelt (permissive-open)",
            "config": "workspace settings only; web_fetch and google_web_search excluded",
        }


SEATBELT = """\
(version 1)
(allow default)
(deny file-write*)
(allow file-write*
    (subpath "{workdir}")
    (subpath "{logdir}")
    (subpath "/private/tmp")
    (subpath "/private/var/folders")
    (subpath "{home}/Library/Application Support/mini-swe-agent")
    (subpath "{home}/Library/Caches")
    (literal "/dev/null")
    (literal "/dev/tty")
    (regex #"^/dev/fd/"))
(deny network*)
(allow network-outbound (remote ip "localhost:{port}"))
(allow network* (local unix-socket))
"""


class MiniSweAgentContestant(Contestant):
    """mini-swe-agent - the SWE-bench team's bash-only agent - on a local model.

    Chosen for the open-weights lane because a small model handles one action
    format (a bash block in plain text) far better than a full agent's tool
    suite: under Codex, gpt-oss:20b kept passing working directories that did
    not exist and ended without an edit. mini-swe-agent runs commands unsandboxed, so the arena wraps it in a
    macOS seatbelt profile: writes only inside its own checkout and logs, and no
    network except the local model server.
    """

    def __init__(self, model: str, *, step_limit: int = 75) -> None:
        self.model = model
        self.step_limit = step_limit
        self.name = f"mini-swe-agent/{model}"
        self.lane = "ollama"

    def command(self, prompt: str, workdir: Path, logdir: Path) -> tuple[list[str], dict[str, str]]:
        profile = logdir / "sandbox.sb"
        profile.write_text(
            SEATBELT.format(
                workdir=workdir.resolve(),
                logdir=logdir.resolve(),
                home=Path.home(),
                port=OllamaServer.PORT,
            ),
            encoding="utf-8",
        )
        argv = [
            "/usr/bin/sandbox-exec",
            "-f",
            str(profile),
            shutil.which("mini") or str(Path.home() / ".local" / "bin" / "mini"),
            "--model",
            f"ollama_chat/{self.model}",
            "--task",
            prompt,
            "--yolo",
            "--exit-immediately",
            "--cost-limit",
            "0",
            # Text-based actions, not function calls: Ollama's tool-call parser
            # rejects gpt-oss's heredoc-heavy commands, and every rejection is
            # a retried request that makes no progress.
            "--model-class",
            "litellm_textbased",
            "-c",
            "mini_textbased.yaml",
            "-c",
            f"agent.step_limit={self.step_limit}",
            "-c",
            f"model.model_kwargs.api_base=http://127.0.0.1:{OllamaServer.PORT}",
            "--output",
            str(logdir / "trajectory.json"),
        ]
        env = {
            "MSWEA_COST_TRACKING": "ignore_errors",
            "MSWEA_CONFIGURED": "true",
            "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        }
        return argv, env

    def parse(self, logdir: Path) -> tuple[str, dict[str, int]]:
        path = logdir / "trajectory.json"
        if not path.exists():
            return "", {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return "", {}
        messages = data.get("messages", [])
        said = [m.get("content") for m in messages if m.get("role") == "assistant"]
        last = next((s for s in reversed(said) if isinstance(s, str) and s.strip()), "")
        info = data.get("info", {})
        usage = {"steps": sum(1 for m in messages if m.get("role") == "assistant")}
        if isinstance(stats := info.get("model_stats"), dict):
            usage.update({k: v for k, v in stats.items() if isinstance(v, int)})
        return f"[{info.get('exit_status', '?')}] {last.strip()[-3000:]}", usage

    def describe(self) -> dict[str, Any]:
        mini = shutil.which("mini") or str(Path.home() / ".local" / "bin" / "mini")
        return {
            "harness": "mini-swe-agent",
            "version": _version(mini),
            "model": self.model,
            "provider": "ollama (local, 64k context)",
            "sandbox": "macOS seatbelt: writes only to its checkout, network only to the model",
            "config": f"stock mini_textbased.yaml, step limit {self.step_limit}",
        }


def _jsonl(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not path.exists():
        return events
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                events.append(parsed)
    return events
