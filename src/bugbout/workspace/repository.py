"""The project's git repository: history to mine, and history-free checkouts.

A checkout handed to an agent has no ``.git`` directory at all - the fix, the
maintainers' tests and every later commit are physically absent, rather than
merely off-limits.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from bugbout.errors import RepositoryError

_GITHUB_REMOTE = re.compile(r"github\.com[:/](?P<slug>[^/\s]+/[^/\s]+?)(?:\.git)?/?$")
_FIELD, _RECORD = "\x1f", "\x1e"


@dataclass(frozen=True, slots=True)
class Commit:
    sha: str
    parents: tuple[str, ...]
    subject: str
    body: str


class Repository:
    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def open(cls, path: Path) -> Repository:
        if not (path / ".git").exists():
            raise RepositoryError(f"{path} is not a git repository (no .git directory)")
        return cls(path)

    @classmethod
    def clone(cls, url: str, destination: Path) -> Repository:
        """A full clone of ``url`` at ``destination``, reused and refreshed if present."""
        if (destination / ".git").exists():
            repo = cls(destination)
            repo.git("fetch", "--quiet", "origin")
            return repo
        destination.parent.mkdir(parents=True, exist_ok=True)
        _run(["git", "clone", "--quiet", url, str(destination)], cwd=destination.parent)
        return cls(destination)

    def git(self, *args: str, env: dict[str, str] | None = None) -> str:
        return _run(["git", *args], cwd=self.path, env=env).stdout

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").strip()

    def log(self, since: str | None = None) -> list[Commit]:
        """Commits on the mainline, newest first, optionally only those after ``since``.

        Following first parents yields one commit per change that landed - a
        squashed commit, a rebased commit, or a pull request's merge commit -
        and never the work-in-progress commits inside a merged branch.
        """
        window = [f"--since={since}"] if since else []
        out = self.git(
            "log", "--first-parent", *window, f"--format=%H{_FIELD}%P{_FIELD}%s{_FIELD}%b{_RECORD}"
        )
        commits = []
        for record in out.split(_RECORD):
            if not record.strip():
                continue
            sha, parents, subject, body = record.strip("\n").split(_FIELD, 3)
            commits.append(Commit(sha, tuple(parents.split()), subject, body.strip()))
        return commits

    def changed_files(self, commit: Commit) -> list[str]:
        """Files ``commit`` added or modified, relative to its first parent."""
        if not commit.parents:
            return []
        out = self.git("diff", "--name-only", "--diff-filter=d", commit.parents[0], commit.sha)
        return [line for line in out.splitlines() if line]

    def show(self, commit: str, path: str) -> bytes | None:
        """A file's exact bytes at ``commit``, or ``None`` if it did not exist there."""
        completed = subprocess.run(  # noqa: S603 - literal git argv
            ["git", "cat-file", "blob", f"{commit}:{path}"],  # noqa: S607
            cwd=self.path,
            capture_output=True,
            check=False,
        )
        return completed.stdout if completed.returncode == 0 else None

    def archive(self, commit: str, destination: Path) -> None:
        """Lay down the tree at ``commit`` with no history attached.

        Files are checked out through a private index, so the repository's own
        index and working tree are never touched. Unlike ``git archive``, this
        ignores ``export-ignore`` attributes, which some projects put on their
        test directories.
        """
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir(parents=True)
        with tempfile.TemporaryDirectory(prefix="bugbout-index-") as scratch:
            env = {**os.environ, "GIT_INDEX_FILE": str(Path(scratch) / "index")}
            self.git("read-tree", commit, env=env)
            prefix = f"--prefix={destination.resolve()}/"
            self.git("checkout-index", "--all", "--force", prefix, env=env)

    def github_slug(self) -> str | None:
        """``owner/name`` of the GitHub repository ``origin`` points at, if any."""
        try:
            url = self.git("remote", "get-url", "origin").strip()
        except RepositoryError:
            return None
        match = _GITHUB_REMOTE.search(url)
        return match.group("slug") if match else None


def _run(
    argv: list[str], cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(  # noqa: S603 - argv built from literals and our paths
            argv,
            cwd=cwd,
            env=env,
            capture_output=True,
            encoding="utf-8",
            errors="replace",  # old commit messages are not always UTF-8
            check=False,
        )
    except FileNotFoundError as exc:
        raise RepositoryError(f"{argv[0]} is not installed") from exc
    if completed.returncode != 0:
        raise RepositoryError(f"{' '.join(argv[:3])} failed: {completed.stderr.strip()[:400]}")
    return completed
