"""Real git repositories, built in a temporary directory, for end-to-end tests.

The toy project has one real bug and its fix:

    1. "Initial commit"             ``add`` subtracts; only ``mul`` is tested.
    2. "Fix add subtracting (#12)"  ``add`` is fixed, and ``test_add`` is added.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from bugbout.tasks.issues import Issue
from bugbout.workspace.environment import PythonProject
from bugbout.workspace.patch import Patch
from bugbout.workspace.repository import Repository
from bugbout.workspace.workspace import Workspace

BUGGY = "def add(a, b):\n    return a - b\n\n\ndef mul(a, b):\n    return a * b\n"
FIXED = BUGGY.replace("a - b", "a + b")
TESTS_BEFORE = "from toy.core import mul\n\n\ndef test_mul():\n    assert mul(2, 3) == 6\n"
TESTS_AFTER = (
    "from toy.core import add, mul\n\n\n"
    "def test_mul():\n    assert mul(2, 3) == 6\n\n\n"
    "def test_add():\n    assert add(2, 3) == 5\n"
)
ISSUE = Issue(12, "add() subtracts", "Calling add(2, 3) returns -1 instead of the expected 5.")


class GitRepo:
    """A scratch git repository driven from tests."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.mkdir(parents=True)
        self.git("init", "--quiet", "--initial-branch=main")
        self.git("config", "user.email", "toy@example.com")
        self.git("config", "user.name", "Toy")
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.path, capture_output=True, text=True, check=True
        ).stdout.strip()

    def write(self, files: dict[str, str]) -> None:
        for relative, content in files.items():
            target = self.path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)

    def commit(self, *message: str) -> str:
        self.git("add", "--all")
        self.git("commit", "--quiet", *(arg for part in message for arg in ("-m", part)))
        return self.git("rev-parse", "HEAD")


@dataclass
class Toy:
    repo: GitRepo
    base: str
    fix: str

    @property
    def repository(self) -> Repository:
        return Repository.open(self.repo.path)

    def workspace(self, root: Path) -> Workspace:
        project = PythonProject(
            source_root="src/toy", test_root="tests", interpreter=sys.executable
        )
        return Workspace(root, self.repository, project)


def build_toy(path: Path) -> Toy:
    repo = GitRepo(path)
    repo.write(
        {"src/toy/__init__.py": "", "src/toy/core.py": BUGGY, "tests/test_core.py": TESTS_BEFORE}
    )
    base = repo.commit("Initial commit")
    repo.write({"src/toy/core.py": FIXED, "tests/test_core.py": TESTS_AFTER})
    return Toy(repo, base, repo.commit("Fix add subtracting (#12)"))


def patch_to(tmp: Path, core: str) -> Patch:
    """A patch that turns the toy's buggy ``core.py`` into ``core``."""
    for side, text in (("before", BUGGY), ("after", core)):
        (tmp / side / "src" / "toy").mkdir(parents=True)
        (tmp / side / "src" / "toy" / "core.py").write_text(text)
    return Patch.between(tmp / "before", tmp / "after")


class StaticIssues:
    """An issue tracker that serves issues from memory."""

    def __init__(self, *issues: Issue) -> None:
        self.issues = {issue.number: issue for issue in issues}
        self.requested: list[int] = []

    def fetch(self, number: int) -> Issue | None:
        self.requested.append(number)
        return self.issues.get(number)
