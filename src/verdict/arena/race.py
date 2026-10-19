"""Run coding agents on tasks - concurrently, isolated, resumably.

The orchestration problem is shaped by quotas, not by the machine. A ChatGPT
plan, a free API key and a local model each run at their own pace, so work is
queued per **lane** (one quota), lanes run in parallel, and attempts within a
lane are capped. A slow lane never stalls a fast one, and a lane never exceeds
the concurrency its quota tolerates.

Every attempt gets a fresh copy of the task's history-free base tree and its
own test runner, and journals an :class:`Attempt` when it finishes. The journal
makes a race resumable: re-running it skips every attempt that already has a
result, so an interrupted run never spends an agent's quota twice on the same
task. The race is the same for every :class:`Assignment` - fixing an issue or
reproducing one differs only in the brief.
"""

from __future__ import annotations

import json
import stat
import threading
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from verdict.arena.agents.base import CodingAgent, RunRecord
from verdict.arena.assignments import Assignment, Brief
from verdict.arena.layout import ArenaLayout
from verdict.arena.patch import Patch
from verdict.logging import get_logger
from verdict.review.bench import TaskBench
from verdict.review.tasks import IssueTask

log = get_logger(__name__)

DEFAULT_TIMEOUT = 30 * 60

_QUOTA_MARKERS = ("usage limit", "rate limit", "quota", "429", "too many requests")


@dataclass(frozen=True, slots=True)
class Attempt:
    """One agent's attempt at one task: how it ran, and what it left on disk."""

    task_id: str
    agent: str
    assignment: str
    status: str
    """``ok``, ``error``, ``timed_out``, ``quota`` or ``crashed``."""

    record: RunRecord
    patch: Patch
    source_files: tuple[str, ...] = ()
    test_files: tuple[str, ...] = ()

    @property
    def produced(self) -> bool:
        return not self.patch.empty

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["record"] = self.record.to_dict()
        data["patch"] = self.patch.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Attempt:
        return cls(
            task_id=data["task_id"],
            agent=data["agent"],
            assignment=data["assignment"],
            status=data["status"],
            record=RunRecord.from_dict(data["record"]),
            patch=Patch.from_dict(data["patch"]),
            source_files=tuple(data.get("source_files", ())),
            test_files=tuple(data.get("test_files", ())),
        )


@dataclass
class Race:
    """Every (task, agent) attempt at one assignment, for one suite."""

    layout: ArenaLayout
    suite: str
    assignment: Assignment
    timeout: int = DEFAULT_TIMEOUT
    lane_slots: dict[str, int] = field(default_factory=dict)
    retry_failed: bool = False

    @property
    def run_dir(self) -> Path:
        return self.layout.run_dir(self.suite, self.assignment.name)

    def attempt_dir(self, task: IssueTask, agent: CodingAgent) -> Path:
        return self.run_dir / task.task_id / agent.name.replace("/", "__")

    # --- one attempt -------------------------------------------------------------

    def attempt(self, task: IssueTask, agent: CodingAgent) -> Attempt:
        folder = self.attempt_dir(task, agent)
        journal = folder / "result.json"
        if journal.exists():
            cached = Attempt.from_dict(json.loads(journal.read_text(encoding="utf-8")))
            # A failure that produced nothing - a quota wall, a crash, a timeout -
            # is not the agent's answer and may be retried on request. A finished
            # attempt is never re-run: that would spend quota to replace a result.
            if not (self.retry_failed and cached.status != "ok" and not cached.produced):
                return cached

        bench = self.layout.bench(task.target_slug)
        base = self.layout.pristine(task)
        repo = self.layout.checkout(task, folder / "repo")
        runner = self._write_runner(bench, repo, folder / "bin")
        brief = Brief(
            repo=bench.target.full_name,
            title=task.title,
            statement=task.problem_statement,
            source_root=bench.target.source_root,
            test_root=bench.target.test_root,
            runner=runner,
        )

        log.info("%s  %s  %s started", agent.name, self.assignment.name, task.task_id)
        record = agent.run(self.assignment.prompt(brief), repo, folder / "logs", self.timeout)
        patch = Patch.between(base, repo)
        attempt = Attempt(
            task_id=task.task_id,
            agent=agent.name,
            assignment=self.assignment.name,
            status=_status(record, patch, folder / "logs"),
            record=record,
            patch=patch,
            source_files=patch.only(under=bench.target.test_root, keep=False).files,
            test_files=patch.only(under=bench.target.test_root, keep=True).files,
        )
        tmp = journal.with_suffix(".tmp")
        tmp.write_text(json.dumps(attempt.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(journal)  # atomic: a crash never leaves a half-written journal
        log.info(
            "%s  %s  %s %s in %.0fs (%d source, %d test file(s))",
            agent.name,
            self.assignment.name,
            task.task_id,
            attempt.status,
            record.seconds,
            len(attempt.source_files),
            len(attempt.test_files),
        )
        return attempt

    @staticmethod
    def _write_runner(bench: TaskBench, repo: Path, folder: Path) -> Path:
        """A test command bound to this attempt's checkout.

        The target package is deliberately not installed in the environment;
        imports resolve through ``PYTHONPATH`` to the agent's own tree. The path
        is absolute, so it stays right wherever in the tree the agent ``cd``s.
        """
        target = bench.target
        import_root = (repo / target.source_root).parent
        extra = " ".join(f"'{arg}'" for arg in target.extra_pytest_args)
        folder.mkdir(parents=True, exist_ok=True)
        script = folder / "run-tests"
        script.write_text(
            "#!/bin/sh\n"
            f"# Runs {target.full_name}'s tests against the checkout at {repo}\n"
            f'export PYTHONPATH="{import_root}"\n'
            "export PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0 TZ=UTC\n"
            f'exec "{bench.python}" -m pytest -o addopts= -p no:cacheprovider '
            f'-p no:randomly {extra} "$@"\n',
            encoding="utf-8",
        )
        script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return script

    # --- the whole race -------------------------------------------------------------

    def run(
        self,
        tasks: list[IssueTask],
        agents: list[CodingAgent],
        on_attempt: Callable[[Attempt], None] | None = None,
    ) -> list[Attempt]:
        """Every task against every agent, lanes in parallel."""
        queues: dict[str, list[tuple[IssueTask, CodingAgent]]] = defaultdict(list)
        for agent in agents:
            for task in tasks:
                queues[agent.lane].append((task, agent))

        results: list[Attempt] = []
        lock = threading.Lock()

        def work(task: IssueTask, agent: CodingAgent) -> None:
            try:
                attempt = self.attempt(task, agent)
            except Exception as exc:  # one broken attempt must not end the race
                log.exception("%s  %s  crashed", agent.name, task.task_id)
                attempt = Attempt(
                    task.task_id,
                    agent.name,
                    self.assignment.name,
                    "crashed",
                    RunRecord(agent.name, None, False, 0.0, error=repr(exc)),
                    Patch(""),
                )
            with lock:
                results.append(attempt)
                if on_attempt:
                    on_attempt(attempt)

        pools = [
            (ThreadPoolExecutor(max_workers=self.lane_slots.get(lane, 1)), queue)
            for lane, queue in queues.items()
        ]
        futures = [pool.submit(work, t, a) for pool, queue in pools for t, a in queue]
        for future in futures:
            future.result()
        for pool, _ in pools:
            pool.shutdown()
        return results


def _status(record: RunRecord, patch: Patch, logs: Path) -> str:
    if record.timed_out:
        return "timed_out"
    if not record.error:
        return "ok"
    # "Produced nothing" and "was never allowed to finish" are different
    # results, and only the first is a statement about the agent.
    return "quota" if patch.empty and _hit_quota(logs) else "error"


def _hit_quota(logs: Path) -> bool:
    for name in ("stderr.log", "transcript.jsonl"):
        path = logs / name
        if path.exists():
            tail = path.read_text(encoding="utf-8", errors="replace")[-20000:].lower()
            if any(marker in tail for marker in _QUOTA_MARKERS):
                return True
    return False
