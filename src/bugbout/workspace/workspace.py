"""One project's working state: its repository, test environment and trees on disk.

Everything heavy lives under a cache directory (see :mod:`bugbout.home`), one
subdirectory per project, so the stages of a run share base trees and an
environment instead of rebuilding them.
"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from bugbout.workspace.environment import PythonProject, TestEnvironment
from bugbout.workspace.repository import Repository

if TYPE_CHECKING:
    from bugbout.sandbox import Sandbox
    from bugbout.tasks.task import Task

_COMPLETE = ".complete"


class Workspace:
    def __init__(
        self,
        root: Path,
        repository: Repository,
        project: PythonProject,
        sandbox: Sandbox | None = None,
    ) -> None:
        self.root = root
        self.repository = repository
        self.project = project
        self.environment = TestEnvironment(root / "env", project, sandbox)
        self._lock = threading.Lock()

    @property
    def scratch(self) -> Path:
        """Throwaway trees for scoring and cross-examination."""
        return self.root / "scratch"

    def run_dir(self, stage: str) -> Path:
        """Where one stage's agent runs do their work."""
        return self.root / "runs" / stage

    def prepare(self) -> None:
        """Build the test environment if it is not there yet."""
        self.environment.ensure(self.repository.path)

    def pristine(self, task: Task) -> Path:
        """The task's base tree, laid down once and never written to again."""
        path = self.root / "pristine" / task.task_id
        with self._lock:
            if not (path / _COMPLETE).exists():
                self.repository.archive(task.base_commit, path)
                # A project that committed its bugbout results must not show them to agents.
                shutil.rmtree(path / ".bugbout", ignore_errors=True)
                (path / _COMPLETE).touch()
        return path

    def checkout(self, task: Task, destination: Path) -> Path:
        """A fresh, writable copy of the task's base tree."""
        shutil.rmtree(destination, ignore_errors=True)
        shutil.copytree(
            self.pristine(task),
            destination,
            symlinks=True,
            ignore=shutil.ignore_patterns(_COMPLETE),
        )
        return destination

    def install_fix_tests(self, task: Task, tree: Path) -> None:
        """Copy the maintainers' test files, as of the fix, into ``tree``.

        Only ever done when scoring - never where an agent can see it.
        """
        for relative in task.test_files:
            content = self.repository.show(task.fix_commit, relative)
            if content is None:
                continue
            target = tree / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
