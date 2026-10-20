"""Run coding agents on tasks - concurrently, isolated, resumably.

The orchestration problem is shaped by quotas, not by the machine. A ChatGPT
plan, an API key and a local model each run at their own pace, so work is
queued per **lane** (one quota), lanes run in parallel, and runs within a lane
are capped. A slow lane never stalls a fast one, and a lane never exceeds the
concurrency its quota tolerates.

Every attempt gets a fresh copy of the task's history-free base tree and its
own test runner, and is journaled when it finishes. The journal makes a race
resumable: running it again skips every attempt that already has a result, so
an interrupted bout never spends an agent's quota twice on the same task.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from bugbout.agents.base import AgentRun, CodingAgent, stop_all
from bugbout.arena.briefs import Assignment, Brief
from bugbout.errors import ArtifactError
from bugbout.jsonfile import read_json, write_json
from bugbout.log import get_logger
from bugbout.naming import slug
from bugbout.sandbox import Sandbox
from bugbout.tasks.task import Task
from bugbout.workspace.patch import Patch
from bugbout.workspace.workspace import Workspace

log = get_logger(__name__)

DEFAULT_TIMEOUT = 30 * 60
_QUOTA_MARKERS = ("usage limit", "rate limit", "quota", "429", "too many requests")

Status = Literal["ok", "error", "timed_out", "quota", "crashed"]


@dataclass(frozen=True, slots=True)
class Attempt:
    """One agent's attempt at one task: how it ran, and what it left on disk."""

    task_id: str
    agent: str
    assignment: str
    status: Status
    run: AgentRun
    patch: Patch
    vendor: str = ""

    @property
    def produced(self) -> bool:
        return not self.patch.empty

    @property
    def retryable(self) -> bool:
        """Whether it failed without leaving anything - a quota wall, a crash, a timeout.

        Such a failure is not the agent's answer and may be run again on request;
        a finished attempt never is, since that would spend quota to replace a result.
        """
        return self.status != "ok" and not self.produced

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent": self.agent,
            "assignment": self.assignment,
            "status": self.status,
            "vendor": self.vendor,
            "run": self.run.to_dict(),
            "patch": self.patch.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Attempt:
        return cls(
            task_id=data["task_id"],
            agent=data["agent"],
            assignment=data["assignment"],
            status=data["status"],
            run=AgentRun.from_dict(data["run"]),
            patch=Patch.from_dict(data["patch"]),
            vendor=data.get("vendor", ""),
        )


class Race:
    """Every agent attempts every task under one assignment, journaled under ``journal``."""

    def __init__(
        self,
        workspace: Workspace,
        assignment: Assignment,
        journal: Path,
        *,
        sandbox: Sandbox,
        repo: str,
        timeout: int = DEFAULT_TIMEOUT,
        quotas: Mapping[str, int] | None = None,
        retry_failed: bool = False,
    ) -> None:
        self.workspace = workspace
        self.assignment = assignment
        self.journal = journal
        self.sandbox = sandbox
        self.repo = repo
        """How the project is named to agents, e.g. ``owner/name``."""
        self.timeout = timeout
        self.quotas = dict(quotas or {})
        self.retry_failed = retry_failed
        self._stopping = threading.Event()
        self._reporting = threading.Lock()

    def run(
        self,
        tasks: Sequence[Task],
        agents: Sequence[CodingAgent],
        on_attempt: Callable[[Attempt], None] | None = None,
    ) -> list[Attempt]:
        """Every task against every agent, one lane per quota, lanes in parallel."""
        lanes: dict[str, list[tuple[Task, CodingAgent]]] = defaultdict(list)
        for agent in agents:
            for task in tasks:
                lanes[agent.quota].append((task, agent))
        pools = {
            quota: ThreadPoolExecutor(max(1, self.quotas.get(quota, 1)), f"bugbout-{quota}")
            for quota in lanes
        }
        futures: list[Future[Attempt | None]] = [
            pools[quota].submit(self._work, task, agent, on_attempt)
            for quota, jobs in lanes.items()
            for task, agent in jobs
        ]
        try:
            pending: set[Future[Attempt | None]] = set(futures)
            while pending:
                # Short waits, so an interrupt is acted on promptly rather than once
                # the slowest agent happens to finish.
                _, pending = wait(pending, timeout=0.5)
            finished = [future.result() for future in futures]
        except KeyboardInterrupt:
            self._stop(pools.values(), futures)
            raise
        finally:
            for pool in pools.values():
                pool.shutdown(wait=True, cancel_futures=True)
        return [attempt for attempt in finished if attempt is not None]

    def attempt(self, task: Task, agent: CodingAgent) -> Attempt:
        """Run ``agent`` on ``task`` once, in a fresh checkout. Nothing is journaled."""
        folder = self.workspace.run_dir(self.assignment.name) / task.task_id / slug(agent.name)
        repo = self.workspace.checkout(task, folder / "repo")
        runner = self.workspace.environment.write_runner(repo, folder / "bin" / "run-tests")
        project = self.workspace.project
        brief = Brief(
            self.repo,
            task.title,
            task.problem_statement,
            project.source_root,
            project.test_root,
            runner,
        )
        log.info("%s started %s on %s", agent.name, self.assignment.name, task.task_id)
        run = agent.run(
            self.assignment.prompt(brief),
            repo,
            folder / "logs",
            sandbox=self.sandbox,
            timeout=self.timeout,
        )
        patch = Patch.between(self.workspace.pristine(task), repo)
        status = _status(run, patch, folder / "logs")
        log.info(
            "%s finished %s on %s: %s in %.0fs%s",
            agent.name,
            self.assignment.name,
            task.task_id,
            status,
            run.seconds,
            "" if status == "ok" else f" ({run.error}; logs in {folder / 'logs'})",
        )
        return Attempt(
            task.task_id, agent.name, self.assignment.name, status, run, patch, agent.vendor
        )

    def journaled(self, task: Task, agent: CodingAgent) -> Attempt | None:
        path = self._journal_path(task.task_id, agent.name)
        if not path.exists():
            return None
        try:
            return Attempt.from_dict(read_json(path))
        except (ArtifactError, KeyError, TypeError) as exc:
            log.warning("Ignoring an unreadable journal entry %s: %s", path, exc)
            return None

    # --- internals ----------------------------------------------------------------

    def _work(
        self, task: Task, agent: CodingAgent, on_attempt: Callable[[Attempt], None] | None
    ) -> Attempt | None:
        attempt = self.journaled(task, agent)
        if attempt is None or (self.retry_failed and attempt.retryable):
            if self._stopping.is_set():
                return None
            try:
                attempt = self.attempt(task, agent)
            except Exception as exc:  # one broken attempt must not end the race
                log.exception("%s crashed on %s", agent.name, task.task_id)
                return self._report(_crashed(task, agent, self.assignment, exc), on_attempt)
            if self._stopping.is_set():
                return None  # an interrupted run is not the agent's answer
            write_json(self._journal_path(task.task_id, agent.name), attempt.to_dict())
        return self._report(attempt, on_attempt)

    def _report(self, attempt: Attempt, on_attempt: Callable[[Attempt], None] | None) -> Attempt:
        if on_attempt is not None:
            with self._reporting:
                on_attempt(attempt)
        return attempt

    def _journal_path(self, task_id: str, agent: str) -> Path:
        return self.journal / task_id / f"{slug(agent)}.json"

    def _stop(
        self, pools: Iterable[ThreadPoolExecutor], futures: list[Future[Attempt | None]]
    ) -> None:
        """Cancel what has not started, and keep killing agents until every worker is done."""
        self._stopping.set()
        for pool in pools:
            pool.shutdown(wait=False, cancel_futures=True)
        while not all(future.done() for future in futures):
            stop_all()
            wait(futures, timeout=0.5)


def _crashed(task: Task, agent: CodingAgent, assignment: Assignment, exc: Exception) -> Attempt:
    run = AgentRun(agent.name, None, False, 0.0, error=repr(exc))
    return Attempt(
        task.task_id, agent.name, assignment.name, "crashed", run, Patch(""), agent.vendor
    )


def _status(run: AgentRun, patch: Patch, logs: Path) -> Status:
    if run.timed_out:
        return "timed_out"
    if not run.error:
        return "ok"
    # "Produced nothing" and "was never allowed to finish" are different results,
    # and only the first is a statement about the agent.
    return "quota" if patch.empty and _hit_quota(logs) else "error"


def _hit_quota(logs: Path) -> bool:
    for name in ("stderr.log", "stdout.log"):
        path = logs / name
        if path.exists():
            tail = path.read_text(encoding="utf-8", errors="replace")[-20000:].lower()
            if any(marker in tail for marker in _QUOTA_MARKERS):
                return True
    return False
