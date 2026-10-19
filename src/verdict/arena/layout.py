"""The arena's working state on disk: environments, base trees, runs, scratch.

All of it lives under ``VERDICT_HOME`` (``~/.verdict``), outside the repository.
Coding agents run shell commands a model wrote, and this project's own
directory name contains spaces - where an unquoted path does not fail loudly,
it quietly means something else.
"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path

from verdict.paths import verdict_home
from verdict.review.bench import TaskBench
from verdict.review.tasks import IssueTask
from verdict.targets import get_target
from verdict.targets.workspace import TargetWorkspace

_COMPLETE = ".complete"


class ArenaLayout:
    """Paths, plus the per-target test benches and base trees built under them."""

    def __init__(self, home: Path | None = None) -> None:
        self.home = home or verdict_home()
        self._benches: dict[str, TaskBench] = {}
        # Re-entrant: laying down a base tree needs the target's bench first.
        self._lock = threading.RLock()

    @property
    def scratch(self) -> Path:
        """Throwaway trees for scoring and cross-examination."""
        return self.home / "scratch"

    def run_dir(self, suite: str, assignment: str) -> Path:
        """Where one suite's attempts at one assignment are journaled."""
        return self.home / "runs" / suite / assignment

    def bench(self, slug: str) -> TaskBench:
        """The test environment for a target: its dependencies, never the package itself."""
        with self._lock:
            if slug not in self._benches:
                target = get_target(slug)
                bench = TaskBench(
                    target, TargetWorkspace(target).repo_dir, root=self.home / "envs" / slug
                )
                bench.ensure_env()
                self._benches[slug] = bench
            return self._benches[slug]

    def pristine(self, task: IssueTask) -> Path:
        """The task's base tree, laid down once and never written to again."""
        path = self.home / "pristine" / task.task_id
        with self._lock:
            if not (path / _COMPLETE).exists():
                shutil.rmtree(path, ignore_errors=True)
                self.bench(task.target_slug).archive(task.base_commit, path)
                (path / _COMPLETE).touch()
        return path

    def checkout(self, task: IssueTask, destination: Path) -> Path:
        """A fresh, writable copy of the task's base tree."""
        shutil.rmtree(destination, ignore_errors=True)
        shutil.copytree(self.pristine(task), destination, ignore=shutil.ignore_patterns(_COMPLETE))
        return destination
