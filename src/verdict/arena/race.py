"""Run several coding agents on the same tasks - concurrently, isolated, resumably.

The orchestration problem is shaped by the contestants' quotas, not by the
machine. A ChatGPT subscription, a free Gemini key and a local model each run
at their own pace, so work is queued per **lane** (one quota), lanes run in
parallel, and attempts within a lane are capped. A slow local model never
stalls a cloud lane, and a cloud lane never exceeds the concurrency its quota
tolerates.

Every attempt gets a fresh copy of the task's history-free base tree and its
own test runner, and writes ``result.json`` when it finishes. That file is the
journal: re-running a race skips every attempt that already has one, so an
interrupted overnight run resumes rather than restarts - and never spends a
contestant's quota twice on the same task.
"""

from __future__ import annotations

import json
import shutil
import stat
import threading
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

from verdict.arena.contestants import PROMPT, Contestant, RunRecord
from verdict.arena.patch import Patch
from verdict.logging import get_logger
from verdict.review.bench import TaskBench
from verdict.review.tasks import IssueTask

log = get_logger(__name__)

DEFAULT_TIMEOUT = 30 * 60

Entry = dict[str, Any]


def safe_name(name: str) -> str:
    return name.replace("/", "__")


_QUOTA_MARKERS = ("usage limit", "rate limit", "quota", "429", "too many requests")


def _hit_quota(logs: Path) -> bool:
    """Whether an attempt ended because a quota ran out, judged from its own logs.

    The distinction matters for the record: "the agent produced nothing" and
    "the agent was never allowed to finish" are different results, and only
    the first one is a statement about the agent.
    """
    for name in ("stderr.log", "transcript.jsonl"):
        path = logs / name
        if path.exists():
            tail = path.read_text(encoding="utf-8", errors="replace")[-20000:].lower()
            if any(marker in tail for marker in _QUOTA_MARKERS):
                return True
    return False


class Race:
    """Every (task, contestant) attempt for one named run."""

    def __init__(
        self,
        run_dir: Path,
        pristine_dir: Path,
        benches: dict[str, TaskBench],
        *,
        timeout: int = DEFAULT_TIMEOUT,
        lane_slots: dict[str, int] | None = None,
        retry_failed: bool = False,
    ) -> None:
        self.run_dir = run_dir
        self.pristine_dir = pristine_dir
        self.benches = benches
        self.timeout = timeout
        self.lane_slots = lane_slots or {}
        self.retry_failed = retry_failed
        self._pristine_lock = threading.Lock()

    # --- layout ----------------------------------------------------------------

    def attempt_dir(self, task: IssueTask, contestant: Contestant) -> Path:
        return self.run_dir / task.task_id / safe_name(contestant.name)

    def pristine(self, task: IssueTask) -> Path:
        """The base tree, laid down once per task and never written to again."""
        path = self.pristine_dir / task.task_id
        with self._pristine_lock:
            if not (path / ".complete").exists():
                shutil.rmtree(path, ignore_errors=True)
                self.benches[task.target_slug].archive(task.base_commit, path)
                (path / ".complete").touch()
        return path

    def write_runner(self, task: IssueTask, repo: Path, folder: Path) -> Path:
        """A test command bound to this attempt's checkout.

        The target package is deliberately not installed in the environment;
        imports resolve through ``PYTHONPATH`` to the agent's own tree. The path
        is absolute, so it stays right wherever in the tree the agent ``cd``s.
        """
        bench = self.benches[task.target_slug]
        target = bench.target
        import_root = (repo / target.source_root).parent
        extra = " ".join(f"'{arg}'" for arg in target.extra_pytest_args)
        script = folder / "run-tests"
        folder.mkdir(parents=True, exist_ok=True)
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

    def prompt(self, task: IssueTask, runner: Path) -> str:
        target = self.benches[task.target_slug].target
        return PROMPT.format(
            repo=target.full_name,
            title=task.title,
            statement=task.problem_statement.strip(),
            source_root=target.source_root,
            test_root=target.test_root,
            runner=runner,
        )

    # --- one attempt ------------------------------------------------------------

    def attempt(self, task: IssueTask, contestant: Contestant) -> Entry:
        folder = self.attempt_dir(task, contestant)
        journal = folder / "result.json"
        if journal.exists():
            cached: Entry = json.loads(journal.read_text(encoding="utf-8"))
            # A failure that produced nothing - a quota wall, a crash, a
            # timeout - is not the agent's answer, and may be retried on
            # request. A completed attempt is never re-run: that would spend
            # quota to replace a result rather than to obtain one.
            if not (self.retry_failed and cached["status"] != "ok" and not cached["fix_files"]):
                return cached

        base = self.pristine(task)
        repo = folder / "repo"
        shutil.rmtree(repo, ignore_errors=True)
        shutil.copytree(base, repo, ignore=shutil.ignore_patterns(".complete"))
        runner = self.write_runner(task, repo, folder / "bin")

        log.info("%s  %s  started", contestant.name, task.task_id)
        record = contestant.solve(self.prompt(task, runner), repo, folder / "logs", self.timeout)
        record = replace(record, task_id=task.task_id)
        patch = Patch.between(base, repo)
        test_root = self.benches[task.target_slug].target.test_root
        status = "timed_out" if record.timed_out else ("ok" if not record.error else "error")
        if status != "ok" and patch.empty and _hit_quota(folder / "logs"):
            status = "quota"
        entry: Entry = {
            "task_id": task.task_id,
            "entrant": contestant.name,
            "status": status,
            "record": record.to_dict(),
            "patch": patch.to_dict(),
            "fix_files": list(patch.only(under=test_root, keep=False).files),
            "test_files": list(patch.only(under=test_root, keep=True).files),
        }
        tmp = journal.with_suffix(".tmp")
        tmp.write_text(json.dumps(entry, indent=2), encoding="utf-8")
        tmp.replace(journal)  # atomic: a crash never leaves a half-written journal
        log.info(
            "%s  %s  %s in %.0fs, %d fix file(s)",
            contestant.name,
            task.task_id,
            entry["status"],
            record.seconds,
            len(entry["fix_files"]),
        )
        return entry

    # --- the whole race -----------------------------------------------------------

    def run(
        self,
        tasks: list[IssueTask],
        contestants: list[Contestant],
        on_entry: Callable[[Entry], None] | None = None,
    ) -> list[Entry]:
        """Every task against every contestant, lanes in parallel."""
        jobs: dict[str, list[tuple[IssueTask, Contestant]]] = defaultdict(list)
        for contestant in contestants:
            for task in tasks:
                jobs[contestant.lane].append((task, contestant))

        entries: list[Entry] = []
        lock = threading.Lock()

        def work(task: IssueTask, contestant: Contestant) -> None:
            try:
                entry = self.attempt(task, contestant)
            except Exception as exc:  # one broken attempt must not end the race
                log.exception("%s  %s  crashed", contestant.name, task.task_id)
                entry = {
                    "task_id": task.task_id,
                    "entrant": contestant.name,
                    "status": "crashed",
                    "record": RunRecord(
                        contestant.name, task.task_id, None, False, 0.0, error=repr(exc)
                    ).to_dict(),
                    "patch": Patch("").to_dict(),
                    "fix_files": [],
                    "test_files": [],
                }
            with lock:
                entries.append(entry)
                if on_entry:
                    on_entry(entry)

        pools = [
            (ThreadPoolExecutor(max_workers=self.lane_slots.get(lane, 1)), queue)
            for lane, queue in jobs.items()
        ]
        futures = [pool.submit(work, t, c) for pool, queue in pools for t, c in queue]
        for future in futures:
            future.result()
        for pool, _ in pools:
            pool.shutdown()
        return entries
