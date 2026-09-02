"""Materialise a reconstructed task and score what an agent did with it.

Three properties this module exists to guarantee, because the study is
worthless without them:

**The agent cannot see the future.** A task checkout is produced with
``git archive``, so it has no ``.git`` directory at all. The fix commit, the
maintainer's test, and every later commit are simply absent from the filesystem
the agent works in. Instructing an agent not to look would not be evidence;
removing the history is.

**The oracle is not satisfiable by accident.** Before a task is used, its
oracle test is run against the untouched base checkout and must FAIL there.
A test that already passes proves nothing about the agent's diff. This is the
same fail-to-pass validation SWE-bench applies.

**The agent's diff is measured, not described.** The patch is computed by
diffing the working tree against a pristine copy of the same archive, so
whatever the agent actually changed is what gets audited - including changes it
did not mention.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from precedent.logging import get_logger
from precedent.paths import workspaces_dir
from precedent.review.tasks import IssueTask
from precedent.targets.registry import TargetRepo

log = get_logger(__name__)

Outcome = Literal["passed", "failed", "error", "timeout", "absent"]

# A generated fix that needs longer than this is not a bugfix.
TEST_TIMEOUT = 300


def review_root() -> Path:
    return workspaces_dir() / "review"


@dataclass(frozen=True, slots=True)
class AgentDiff:
    """What a coding agent produced for one task."""

    task_id: str
    agent: str
    patch: str
    changed_files: tuple[str, ...]
    touched_tests: bool
    """True if the agent edited the test suite. Those diffs are excluded from
    scoring: an agent that edits tests can make any oracle pass."""

    note: str = ""

    @property
    def empty(self) -> bool:
        return not self.patch.strip()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentDiff:
        known = set(cls.__slots__)
        payload = {k: v for k, v in data.items() if k in known}
        payload["changed_files"] = tuple(payload.get("changed_files") or ())
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class GroundTruth:
    """Whether the agent's diff actually resolved the issue.

    Two independent conditions, because a diff can fail in two different ways
    and the distinction is the whole point of auditing:

    ``oracle`` - the maintainer's own regression test, which the agent never
    saw. Failing means the diff did not fix the reported bug.

    ``regression`` - the module's pre-existing tests, exactly as they stood at
    the base commit. Failing means the diff broke something that worked.
    """

    task_id: str
    oracle: Outcome
    regression: Outcome
    resolved: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GroundTruth:
        return cls(**{k: v for k, v in data.items() if k in set(cls.__slots__)})


def _run(cmd: list[str], cwd: Path, what: str) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"{what} failed: {proc.stderr.strip()[:500]}")
    return proc


class TaskBench:
    """Builds task checkouts and runs tests in them.

    One virtualenv per target, holding the suite's dependencies but **not** the
    target package itself. The package is supplied per task through
    ``PYTHONPATH``, pointing at that task's own checkout - which is what lets a
    single environment serve twenty different historical commits without
    reinstalling anything.
    """

    def __init__(self, target: TargetRepo, source_clone: Path) -> None:
        self.target = target
        self.source_clone = source_clone
        self.root = review_root() / target.slug

    # --- environment ---------------------------------------------------------

    @property
    def venv(self) -> Path:
        return self.root / "venv"

    @property
    def python(self) -> Path:
        return self.venv / "bin" / "python"

    def ensure_env(self) -> None:
        if self.python.exists():
            return
        self.root.mkdir(parents=True, exist_ok=True)
        log.info("Creating review virtualenv for %s", self.target.slug)
        uv = shutil.which("uv") or str(Path.home() / ".local/bin/uv")
        if Path(uv).exists():
            _run([uv, "venv", "--python", "3.12", str(self.venv)], self.root, "uv venv")
            _run(
                [
                    uv,
                    "pip",
                    "install",
                    "--python",
                    str(self.python),
                    *self.target.test_dependencies,
                ],
                self.root,
                "uv pip install",
            )
        else:
            _run(["python3", "-m", "venv", str(self.venv)], self.root, "venv")
            _run(
                [str(self.python), "-m", "pip", "install", "-q", *self.target.test_dependencies],
                self.root,
                "pip install",
            )

    # --- task checkouts ------------------------------------------------------

    def task_dir(self, task: IssueTask) -> Path:
        return self.root / "tasks" / task.task_id

    def workdir(self, task: IssueTask) -> Path:
        """Where the agent works. No ``.git``, so no way to read the future."""
        return self.task_dir(task) / "repo"

    def pristine(self, task: IssueTask) -> Path:
        """An untouched copy of the same archive, used only to compute the diff."""
        return self.task_dir(task) / "pristine"

    def materialise(self, task: IssueTask, *, force: bool = False) -> Path:
        """Lay down the repository as it stood immediately before the fix."""
        work, clean = self.workdir(task), self.pristine(task)
        if work.exists() and not force:
            return work
        for path in (work, clean):
            if path.exists():
                shutil.rmtree(path)
            path.mkdir(parents=True)
            self._archive(task.base_commit, path)
        return work

    def _archive(self, commit: str, destination: Path) -> None:
        archive = subprocess.run(
            ["git", "archive", "--format=tar", commit],
            cwd=self.source_clone,
            capture_output=True,
            check=False,
        )
        if archive.returncode != 0:
            raise RuntimeError(f"git archive {commit[:8]} failed: {archive.stderr[:300]!r}")
        extract = subprocess.run(
            ["tar", "-x", "-C", str(destination)],
            input=archive.stdout,
            capture_output=True,
            check=False,
        )
        if extract.returncode != 0:
            raise RuntimeError(f"tar extract failed: {extract.stderr[:300]!r}")

    # --- running tests -------------------------------------------------------

    def _env(self, tree: Path) -> dict[str, str]:
        """Hermetic, and pointing imports at this task's checkout."""
        # The directory that CONTAINS the importable package: `tree/src` for a
        # src-layout target, `tree` for a flat one. Deriving it from the pinned
        # source root keeps both layouts working without a per-target special case.
        env = {
            **os.environ,
            "PYTHONPATH": str((tree / self.target.source_root).parent),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "TZ": "UTC",
            "LC_ALL": "C",
        }
        env.pop("PYTEST_ADDOPTS", None)
        return env

    def run_tests(
        self, tree: Path, selection: list[str], *, timeout: int = TEST_TIMEOUT
    ) -> tuple[Outcome, str]:
        """Run a specific selection of tests inside a task checkout."""
        cmd = [
            str(self.python),
            "-m",
            "pytest",
            "-o",
            "addopts=",
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:randomly",
            "--tb=short",
            *selection,
        ]
        try:
            proc = subprocess.run(
                cmd,
                cwd=tree,
                env=self._env(tree),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return "timeout", f"exceeded {timeout}s"
        output = f"{proc.stdout}\n{proc.stderr}".strip()
        tail = "\n".join(output.splitlines()[-25:])
        if proc.returncode == 0:
            return "passed", tail
        if proc.returncode == 1:
            return "failed", tail
        # 2 = interrupted, 4 = usage error, 5 = no tests collected.
        return "error", tail

    def install_oracle(self, task: IssueTask, tree: Path) -> None:
        """Copy the maintainer's post-fix test files into a task checkout.

        Done only when scoring, never before the agent runs. This is the test
        patch: the source stays as the agent left it, the tests come from the
        real fix.
        """
        for relative in task.test_files:
            blob = subprocess.run(
                ["git", "show", f"{task.fix_commit}:{relative}"],
                cwd=self.source_clone,
                capture_output=True,
                text=True,
                check=False,
            )
            if blob.returncode != 0:
                continue
            destination = tree / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(blob.stdout, encoding="utf-8")

    def oracle_selection(self, task: IssueTask) -> list[str]:
        """Address the added tests by name, so unrelated tests cannot mask them."""
        expression = " or ".join(task.oracle_tests)
        return [*task.test_files, "-k", expression]

    # --- validation and scoring ---------------------------------------------

    def validate(self, task: IssueTask) -> tuple[bool, str]:
        """A task is usable only if its oracle fails before the fix exists."""
        tree = self.materialise(task, force=True)
        self.install_oracle(task, tree)
        outcome, detail = self.run_tests(tree, self.oracle_selection(task))
        # Reset: the oracle test files must not be present when the agent works.
        self.materialise(task, force=True)
        if outcome == "failed":
            return True, "oracle fails at base, as required"
        return (
            False,
            f"oracle {outcome} at base ({detail.splitlines()[-1][:160] if detail else ''})",
        )

    def diff_of(self, task: IssueTask) -> tuple[str, tuple[str, ...], bool]:
        """What the agent changed, measured against the pristine archive."""
        proc = subprocess.run(
            [
                "diff",
                "-ruN",
                "--exclude=__pycache__",
                "--exclude=*.pyc",
                "--exclude=.pytest_cache",
                str(self.pristine(task)),
                str(self.workdir(task)),
            ],
            capture_output=True,
            check=False,
        )
        # Repositories carry binary fixtures, and diff emits their bytes
        # verbatim. Decoding leniently stops one non-UTF-8 test asset from
        # discarding an entire agent diff.
        patch = proc.stdout.decode("utf-8", errors="replace")
        changed = tuple(
            sorted(
                {
                    line.split("\t")[0].split(" ")[-1].replace(f"{self.workdir(task)}/", "")
                    for line in patch.splitlines()
                    if line.startswith("+++ ")
                }
            )
        )
        changed = tuple(f for f in changed if f.endswith(".py"))
        return patch, changed, any(f.startswith(f"{self.target.test_root}/") for f in changed)

    def score(self, task: IssueTask, diff: AgentDiff) -> GroundTruth:
        """Run the oracle and the pre-existing tests against the agent's tree."""
        tree = self.workdir(task)
        if diff.empty:
            return GroundTruth(task.task_id, "absent", "absent", False, "the agent changed nothing")

        # Pre-existing tests first, as the agent left the tree - before the
        # oracle files overwrite them.
        regression, reg_detail = self.run_tests(tree, list(task.test_files))
        self.install_oracle(task, tree)
        oracle, oracle_detail = self.run_tests(tree, self.oracle_selection(task))

        resolved = oracle == "passed" and regression == "passed"
        detail = f"oracle={oracle} regression={regression}"
        if not resolved:
            failing = oracle_detail if oracle != "passed" else reg_detail
            detail = f"{detail} | {failing.splitlines()[-1][:200] if failing else ''}"
        return GroundTruth(task.task_id, oracle, regression, resolved, detail)
