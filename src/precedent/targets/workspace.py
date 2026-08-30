"""Checkout, isolate and exercise a target repository.

This module is the foundation the ground truth rests on. If a test run here is
not hermetic, every downstream number - mutation kill-rate, the ablation delta -
inherits the contamination. Three specific hazards are handled explicitly:

* **Stale bytecode.** Mutation works by rewriting a ``.py`` file in place. A
  cached ``.pyc`` from the pre-mutation source would let the original code run
  and the mutant would be scored "survived" without ever executing. Bytecode
  writing is disabled for every run.
* **Repo-level pytest options.** Targets put coverage gates in ``addopts``
  (arrow uses ``--cov-fail-under=99``). Left in place, a mutant could be scored
  "killed" because coverage dipped rather than because a test caught it. All
  inherited ``addopts`` are cleared.
* **Ambient interpreter state.** ``PYTHONPATH`` and hash randomisation leak the
  host environment into results, so both are pinned.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from precedent.logging import get_logger
from precedent.paths import workspaces_dir
from precedent.targets.registry import TargetRepo

log = get_logger(__name__)

Outcome = Literal["passed", "failed", "error", "timeout", "no_tests"]

# pytest's documented exit codes.
_EXIT_OK = 0
_EXIT_TESTS_FAILED = 1
_EXIT_INTERRUPTED = 2
_EXIT_INTERNAL_ERROR = 3
_EXIT_USAGE_ERROR = 4
_EXIT_NO_TESTS = 5

_COUNT_PATTERN = re.compile(r"(\d+) (passed|failed|error|errors|skipped|xfailed|xpassed)")


class WorkspaceError(RuntimeError):
    """Raised when a workspace cannot be prepared."""


@dataclass(frozen=True, slots=True)
class SuiteResult:
    """The outcome of one pytest invocation against a target checkout."""

    exit_code: int
    passed: int
    failed: int
    errors: int
    skipped: int
    duration_seconds: float
    timed_out: bool
    stdout: str
    stderr: str

    @property
    def outcome(self) -> Outcome:
        if self.timed_out:
            return "timeout"
        if self.exit_code == _EXIT_OK:
            return "passed"
        if self.exit_code == _EXIT_TESTS_FAILED:
            return "failed"
        if self.exit_code == _EXIT_NO_TESTS:
            return "no_tests"
        return "error"

    @property
    def is_green(self) -> bool:
        """True only when the suite ran and every test passed."""
        return self.outcome == "passed"

    def summary(self) -> str:
        return (
            f"{self.outcome} in {self.duration_seconds:.2f}s "
            f"({self.passed} passed, {self.failed} failed, "
            f"{self.errors} errors, {self.skipped} skipped)"
        )


def _parse_counts(output: str) -> dict[str, int]:
    """Extract test counts from pytest's terminal summary.

    Reading the summary line is deliberate: ``--junit-xml`` would write a file
    into the checkout on every one of thousands of mutant runs.
    """
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    for number, label in _COUNT_PATTERN.findall(output):
        key = "errors" if label.startswith("error") else label
        if key in counts:
            counts[key] += int(number)
    return counts


def _resolve(executable: str) -> str:
    """Absolute path to a required executable, or a clear error naming it.

    Resolving up front means a missing tool fails immediately with a usable
    message, rather than as an opaque ``FileNotFoundError`` mid-run.
    """
    path = shutil.which(executable)
    if path is None:
        raise WorkspaceError(f"{executable!r} is required but was not found on PATH.")
    return path


def _uv_available() -> bool:
    return shutil.which("uv") is not None


class TargetWorkspace:
    """An isolated checkout of one :class:`TargetRepo`, with its own virtualenv."""

    def __init__(self, target: TargetRepo, root: Path | None = None) -> None:
        self.target = target
        self.root = (root or workspaces_dir()) / target.slug

    # --- locations -----------------------------------------------------------

    @property
    def repo_dir(self) -> Path:
        return self.root / "repo"

    @property
    def venv_dir(self) -> Path:
        return self.root / "venv"

    @property
    def python(self) -> Path:
        return self.venv_dir / "bin" / "python"

    @property
    def source_dir(self) -> Path:
        """Absolute path to the target's importable package."""
        return self.repo_dir / self.target.source_root

    @property
    def test_dir(self) -> Path:
        return self.repo_dir / self.target.test_root

    def is_ready(self) -> bool:
        return self.python.exists() and (self.repo_dir / ".git").exists()

    # --- preparation ---------------------------------------------------------

    def ensure(self, *, force: bool = False) -> None:
        """Clone at the pinned commit and build the virtualenv, if not already done."""
        if force and self.root.exists():
            log.info("Removing existing workspace at %s", self.root)
            shutil.rmtree(self.root)
        if self.is_ready():
            log.debug("Workspace for %s already prepared", self.target.slug)
            return
        self.root.mkdir(parents=True, exist_ok=True)
        self._clone()
        self._create_venv()
        self._install()

    def _clone(self) -> None:
        """Clone the full history, then detach onto the pinned SHA.

        History is kept rather than shallow-cloned because phase 3 mines this
        repo's own bugfix commits and reverts locally, which avoids spending
        GitHub API quota on data git already has.
        """
        if (self.repo_dir / ".git").exists():
            log.debug("Reusing clone at %s", self.repo_dir)
        else:
            log.info("Cloning %s", self.target.full_name)
            self._run(
                [_resolve("git"), "clone", "--quiet", self.target.clone_url, str(self.repo_dir)],
                cwd=self.root,
                what="git clone",
            )
        log.info("Checking out %s @ %s", self.target.ref, self.target.short_commit)
        self._run(
            [_resolve("git"), "checkout", "--quiet", "--force", self.target.commit],
            cwd=self.repo_dir,
            what="git checkout",
        )

    def _create_venv(self) -> None:
        log.info("Creating virtualenv for %s", self.target.slug)
        if _uv_available():
            self._run(
                [_resolve("uv"), "venv", "--python", "3.12", str(self.venv_dir)],
                cwd=self.root,
                what="uv venv",
            )
        else:
            self._run(
                [_resolve("python3"), "-m", "venv", str(self.venv_dir)],
                cwd=self.root,
                what="python -m venv",
            )

    def _install(self) -> None:
        """Install the target in editable mode plus whatever its suite imports."""
        packages = ["-e", str(self.repo_dir), *self.target.test_dependencies]
        log.info(
            "Installing %s and %d test dependencies",
            self.target.slug,
            len(self.target.test_dependencies),
        )
        if _uv_available():
            cmd = [
                _resolve("uv"),
                "pip",
                "install",
                "--quiet",
                "--python",
                str(self.python),
                *packages,
            ]
        else:
            cmd = [str(self.python), "-m", "pip", "install", "--quiet", *packages]
        self._run(cmd, cwd=self.root, what="dependency install")

    # --- execution -----------------------------------------------------------

    def test_env(self) -> dict[str, str]:
        """A cleaned environment for running the target's suite.

        Bytecode caching is off so that an in-place source mutation cannot be
        shadowed by a stale ``.pyc``; hash seed is pinned so set and dict
        ordering cannot make a run flaky.
        """
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        env.pop("PYTEST_ADDOPTS", None)
        env.update(
            {
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONHASHSEED": "0",
                "PYTHONUNBUFFERED": "1",
                "COLUMNS": "120",
                "NO_COLOR": "1",
            }
        )
        return env

    def pytest_command(
        self,
        *,
        selection: tuple[str, ...] = (),
        fail_fast: bool = False,
        extra_args: tuple[str, ...] = (),
    ) -> list[str]:
        """Build a pytest invocation that ignores the repo's own default options."""
        cmd = [
            str(self.python),
            "-m",
            "pytest",
            # Clear inherited addopts: coverage gates and junit output would
            # otherwise decide a mutant's fate instead of the tests themselves.
            "-o",
            "addopts=",
            "-p",
            "no:cacheprovider",
            "--no-header",
            "-q",
        ]
        if fail_fast:
            # For mutation scoring only the first failure matters, and stopping
            # there is the single largest speedup available.
            cmd.append("-x")
        cmd.extend(extra_args)
        cmd.extend(selection or (str(self.test_dir),))
        return cmd

    def run_tests(
        self,
        *,
        selection: tuple[str, ...] = (),
        fail_fast: bool = False,
        extra_args: tuple[str, ...] = (),
        timeout: int = 300,
    ) -> SuiteResult:
        """Run the target's suite and return a structured result.

        A timeout is reported, never raised: under mutation a hang is a normal
        outcome (an inverted loop condition), and it must be scored, not crash
        the run.
        """
        if not self.is_ready():
            raise WorkspaceError(
                f"Workspace for {self.target.slug!r} is not prepared. "
                f"Run `precedent targets prepare {self.target.slug}` first."
            )
        cmd = self.pytest_command(selection=selection, fail_fast=fail_fast, extra_args=extra_args)
        started = time.perf_counter()
        timed_out = False
        try:
            proc = subprocess.run(
                cmd,
                cwd=self.repo_dir,
                env=self.test_env(),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            exit_code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            exit_code = _EXIT_INTERRUPTED
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        duration = time.perf_counter() - started
        counts = _parse_counts(stdout)
        return SuiteResult(
            exit_code=exit_code,
            passed=counts["passed"],
            failed=counts["failed"],
            errors=counts["errors"],
            skipped=counts["skipped"],
            duration_seconds=duration,
            timed_out=timed_out,
            stdout=stdout,
            stderr=stderr,
        )

    def verify_baseline(self, timeout: int = 600) -> SuiteResult:
        """Confirm the unmodified checkout is green.

        Every later phase assumes a failing test means "the mutation broke
        something". That assumption is only valid if the suite passed first, so
        this gate runs before any mutation work is allowed to start.
        """
        log.info("Verifying baseline suite for %s", self.target.slug)
        result = self.run_tests(timeout=timeout)
        if not result.is_green:
            raise WorkspaceError(
                f"Baseline suite for {self.target.slug} is not green: {result.summary()}. "
                f"Mutation scoring would be meaningless against a red suite.\n"
                f"--- last 40 lines of output ---\n" + "\n".join(result.stdout.splitlines()[-40:])
            )
        log.info("Baseline green: %s", result.summary())
        return result

    # --- source manipulation -------------------------------------------------

    def source_files(self) -> list[Path]:
        """Every mutable ``.py`` file in the target's package, sorted."""
        return sorted(
            p
            for p in self.source_dir.rglob("*.py")
            if "__pycache__" not in p.parts and p.name != "__main__.py"
        )

    def relative(self, path: Path) -> str:
        """Express an absolute path inside the checkout as a repo-relative string."""
        return str(path.resolve().relative_to(self.repo_dir.resolve()))

    def read_source(self, relative_path: str) -> str:
        return (self.repo_dir / relative_path).read_text(encoding="utf-8")

    def write_source(self, relative_path: str, content: str) -> None:
        (self.repo_dir / relative_path).write_text(content, encoding="utf-8")

    def restore(self, relative_path: str) -> None:
        """Return one file to its pinned state."""
        self._run(
            [_resolve("git"), "checkout", "--quiet", "--", relative_path],
            cwd=self.repo_dir,
            what="git restore",
        )

    def restore_all(self) -> None:
        """Discard every working-tree modification in the checkout."""
        self._run(
            [_resolve("git"), "checkout", "--quiet", "--", "."],
            cwd=self.repo_dir,
            what="git restore",
        )
        self._run([_resolve("git"), "clean", "--quiet", "-fd"], cwd=self.repo_dir, what="git clean")

    def is_clean(self) -> bool:
        """Whether the checkout has no uncommitted modifications."""
        proc = subprocess.run(
            [_resolve("git"), "status", "--porcelain"],
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.returncode == 0 and not proc.stdout.strip()

    # --- internals -----------------------------------------------------------

    @staticmethod
    def _run(cmd: list[str], *, cwd: Path, what: str) -> None:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise WorkspaceError(
                f"{what} failed (exit {proc.returncode})\n"
                f"  command: {' '.join(cmd)}\n"
                f"  stderr: {proc.stderr.strip()[:2000]}"
            )
