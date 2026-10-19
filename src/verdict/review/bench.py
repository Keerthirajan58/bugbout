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

from verdict.logging import get_logger
from verdict.paths import workspaces_dir
from verdict.review.tasks import IssueTask
from verdict.targets.registry import TargetRepo

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

    def __init__(self, target: TargetRepo, source_clone: Path, root: Path | None = None) -> None:
        self.target = target
        self.source_clone = source_clone
        self.root = root or review_root() / target.slug

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
                    *self._packages(),
                ],
                self.root,
                "uv pip install",
            )
        else:
            _run(["python3", "-m", "venv", str(self.venv)], self.root, "venv")
            _run(
                [str(self.python), "-m", "pip", "install", "-q", *self._packages()],
                self.root,
                "pip install",
            )

    def _packages(self) -> tuple[str, ...]:
        """What the suite needs across the whole history, not just at the pin.

        Two of the four tasks first rejected for a broken oracle turned out to
        need only a package the pinned checkout had dropped.
        """
        return (*self.target.test_dependencies, *self.target.historical_test_dependencies)

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

    def archive(self, commit: str, destination: Path) -> None:
        """Lay down the tree at ``commit`` with no history attached."""
        destination.mkdir(parents=True, exist_ok=True)
        self._archive(commit, destination)

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
        self,
        tree: Path,
        selection: list[str],
        *,
        timeout: int = TEST_TIMEOUT,
        junit: Path | None = None,
    ) -> tuple[Outcome, str]:
        """Run a specific selection of tests inside a task checkout.

        ``junit`` additionally records every test's individual outcome, which is
        what the arena's FAIL_TO_PASS / PASS_TO_PASS bookkeeping is built on.
        """
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
            *([f"--junitxml={junit}", "-o", "junit_family=xunit1"] if junit else []),
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

    def restore_tests(self, task: IssueTask, tree: Path) -> None:
        """Put the test suite back as it stood at the base commit.

        `install_oracle` overwrites test files in place, and without this they
        stay there: a later `diff_of` reads the maintainer's own tests as though
        the agent had written them, and a later regression run executes the
        post-fix suite against the pre-fix code. Both happened.
        """
        clean = self.pristine(task)
        for relative in task.test_files:
            source = clean / relative
            destination = tree / relative
            if source.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            else:
                destination.unlink(missing_ok=True)

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
                "--exclude=.hypothesis",
                "--exclude=.coverage*",
                "--exclude=*.egg-info",
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
        return (
            patch,
            self.changed_files(task, patch),
            any(f.startswith(f"{self.target.test_root}/") for f in self.changed_files(task, patch)),
        )

    def changed_files(self, task: IssueTask, patch: str) -> tuple[str, ...]:
        """Relative paths named by a patch's ``+++`` headers.

        The prefix is stripped by exact string match, never by splitting on
        whitespace: this project's own directory name contains a space, and a
        whitespace split silently truncates every path at it - which is how an
        earlier version of this method recorded paths that matched nothing.
        """
        prefix = f"{self.workdir(task)}/"
        names = {
            line[4:].split("\t")[0].rstrip("\n")[len(prefix) :]
            for line in patch.splitlines()
            if line.startswith(f"+++ {prefix}")
        }
        return tuple(sorted(n for n in names if n.endswith(".py")))

    def restore(self, task: IssueTask, diff: AgentDiff) -> tuple[bool, str]:
        """Put a previously-captured agent diff back into a fresh checkout.

        The study has to survive its own checkouts being rebuilt, and `patch`
        cannot be used here: it splits header paths on whitespace, and this
        project's own directory contains a space, so every hunk is silently
        skipped. Applying the hunks directly also lets the result be *verified* -
        the restored tree is re-diffed against pristine and must reproduce the
        same set of files.
        """
        self.materialise(task, force=True)
        work, clean = self.workdir(task), self.pristine(task)
        wanted = set(diff.changed_files)
        for relative, hunks in parse_patch(diff.patch, str(clean)).items():
            # Only the files the record names. A test run leaves artefacts
            # behind - .hypothesis, coverage data - and those are noise, not
            # part of what the agent decided to change.
            if relative not in wanted:
                continue
            before = clean / relative
            source = (
                before.read_text(encoding="utf-8").splitlines(keepends=True)
                if before.exists()
                else []
            )
            (work / relative).parent.mkdir(parents=True, exist_ok=True)
            (work / relative).write_text("".join(apply_hunks(source, hunks)), encoding="utf-8")

        _, changed, _ = self.diff_of(task)
        if set(changed) != set(diff.changed_files):
            return False, f"restored {sorted(changed)}, expected {sorted(diff.changed_files)}"
        return True, "restored and verified against the recorded file list"

    def score(self, task: IssueTask, diff: AgentDiff) -> GroundTruth:
        """Run the oracle and the pre-existing tests against the agent's tree."""
        tree = self.workdir(task)
        if diff.empty:
            return GroundTruth(task.task_id, "absent", "absent", False, "the agent changed nothing")

        # Pre-existing tests first, as the agent left the tree - before the
        # oracle files overwrite them.
        self.restore_tests(task, tree)
        regression, reg_detail = self.run_tests(tree, list(task.test_files))
        self.install_oracle(task, tree)
        try:
            oracle, oracle_detail = self.run_tests(tree, self.oracle_selection(task))
        finally:
            # Unconditional: scoring must not change the tree it scored.
            self.restore_tests(task, tree)

        resolved = oracle == "passed" and regression == "passed"
        detail = f"oracle={oracle} regression={regression}"
        if not resolved:
            failing = oracle_detail if oracle != "passed" else reg_detail
            detail = f"{detail} | {failing.splitlines()[-1][:200] if failing else ''}"
        return GroundTruth(task.task_id, oracle, regression, resolved, detail)


# --- unified-diff application ------------------------------------------------
#
# Written by hand rather than shelled out to `patch` or `git apply`, both of
# which parse the `---`/`+++` header paths by splitting on whitespace. This
# project's directory name contains a space, so both silently skip every hunk
# and report success on an empty result - the worst possible failure for a
# measurement pipeline. Here the prefix is stripped by exact string match.

Hunk = tuple[int, int, list[str]]


def parse_patch(patch: str, pristine_prefix: str) -> dict[str, list[Hunk]]:
    """Group a ``diff -ruN`` patch into hunks per file, keyed by relative path."""
    files: dict[str, list[Hunk]] = {}
    relative: str | None = None
    start = length = 0
    body: list[str] = []

    def flush() -> None:
        if relative is not None and body:
            files.setdefault(relative, []).append((start, length, list(body)))

    for line in patch.splitlines(keepends=True):
        if line.startswith("--- "):
            flush()
            body = []
            path = line[4:].split("\t")[0].rstrip("\n")
            relative = (
                path[len(pristine_prefix) :].lstrip("/")
                if path.startswith(pristine_prefix)
                else None
            )
        elif line.startswith("+++ ") or line.startswith("diff -"):
            continue
        elif line.startswith("@@"):
            flush()
            body = []
            span = line.split("@@")[1].strip().split(" ")[0]  # e.g. "-251,18"
            numbers = span[1:].split(",")
            start = int(numbers[0])
            length = int(numbers[1]) if len(numbers) > 1 else 1
        elif relative is not None and line[:1] in (" ", "+", "-", "\\"):
            body.append(line)
    flush()
    return files


def apply_hunks(source: list[str], hunks: list[Hunk]) -> list[str]:
    """Rebuild a file from its original lines and the hunks that changed it."""
    result: list[str] = []
    cursor = 0  # 0-indexed position in `source`
    for start, length, body in sorted(hunks):
        begin = max(start - 1, 0)
        result.extend(source[cursor:begin])
        result.extend(line[1:] for line in body if line[:1] in (" ", "+"))
        cursor = begin + length
    result.extend(source[cursor:])
    return result
