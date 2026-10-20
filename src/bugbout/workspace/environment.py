"""An isolated Python environment that runs the project's tests on any checkout.

One virtualenv per project serves every historical checkout: it holds the test
dependencies and the project's runtime dependencies, but not the project itself.
Each checkout's own code is put first on ``PYTHONPATH``, so the code under test is
always the tree being tested - an agent's patch, the base, or the maintainers' fix.
"""

from __future__ import annotations

import os
import shlex
import shutil
import stat
import subprocess
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from bugbout.errors import EnvironmentSetupError
from bugbout.log import get_logger

log = get_logger(__name__)

Outcome = Literal["passed", "failed", "error", "timeout"]

DEFAULT_TIMEOUT = 900
_PYTEST_FLAGS = ("-o", "addopts=", "-p", "no:cacheprovider", "-p", "no:randomly")
_DETERMINISM = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0", "TZ": "UTC"}
# The only variables a test run inherits. Everything else in the caller's
# environment - API keys in particular - stays out of reach of the code under test.
_INHERITED = (
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM", "TMPDIR", "TEMP", "TMP",
    "LANG", "LC_ALL", "LC_CTYPE", "SYSTEMROOT",
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class TestRun:
    __test__ = False  # not a pytest test class, despite the name

    outcome: Outcome
    tail: str
    """The last lines of pytest's output, for diagnosis."""


@dataclass(frozen=True, slots=True)
class PythonProject:
    """What the environment needs to know about the project under test."""

    source_root: str
    """The package directory, relative to the repository root (e.g. ``src/mypkg``)."""

    test_root: str
    python: str = "3.12"
    dependencies: tuple[str, ...] = ("pytest",)
    pytest_args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    """Extra variables the project's tests need, such as a database URL."""

    install_project: bool = True
    """Install the project once to pull in its runtime dependencies, then remove it."""

    interpreter: str | None = None
    """An existing Python with pytest and the project's dependencies, used as is
    instead of building an environment."""


class TestEnvironment:
    __test__ = False  # not a pytest test class, despite the name

    def __init__(self, root: Path, project: PythonProject) -> None:
        self.root = root
        self.project = project

    @property
    def python(self) -> Path:
        if self.project.interpreter:
            return Path(self.project.interpreter)
        return self.root / "venv" / "bin" / "python"

    # --- creation ---------------------------------------------------------------

    def ensure(self, project_dir: Path) -> None:
        """Create the environment if it does not exist yet."""
        if self.project.interpreter:
            if not self.python.exists():
                raise EnvironmentSetupError(f"interpreter {self.python} does not exist")
            return
        marker = self.root / ".ready"
        if marker.exists() and self.python.exists():
            return
        self.root.mkdir(parents=True, exist_ok=True)
        uv = shutil.which("uv")
        if uv is None:
            raise EnvironmentSetupError(
                "uv is required to build test environments: https://docs.astral.sh/uv/"
            )
        log.info("Building the test environment (Python %s)", self.project.python)
        python = str(self.python)
        self._uv(uv, "venv", "--quiet", "--python", self.project.python, str(self.root / "venv"))
        self._uv(uv, "pip", "install", "--quiet", "--python", python, *self.project.dependencies)
        if self.project.install_project:
            self._install_runtime_dependencies(uv, project_dir)
        marker.touch()

    def _install_runtime_dependencies(self, uv: str, project_dir: Path) -> None:
        name = distribution_name(project_dir)
        if name is None:
            log.warning("No [project] name found; skipping the project's runtime dependencies")
            return
        python = str(self.python)
        self._uv(uv, "pip", "install", "--quiet", "--python", python, str(project_dir))
        self._uv(uv, "pip", "uninstall", "--quiet", "--python", python, name)

    def _uv(self, uv: str, *args: str) -> None:
        completed = subprocess.run(  # noqa: S603 - the resolved uv binary and our arguments
            [uv, *args], cwd=self.root, capture_output=True, text=True, check=False
        )
        if completed.returncode != 0:
            raise EnvironmentSetupError(f"uv {args[0]} failed: {completed.stderr.strip()[:600]}")

    # --- running ----------------------------------------------------------------

    def import_root(self, tree: Path) -> Path:
        """The directory that contains the importable package, for ``PYTHONPATH``."""
        return (tree / self.project.source_root).parent

    def env(self, tree: Path) -> dict[str, str]:
        inherited = {key: os.environ[key] for key in _INHERITED if key in os.environ}
        return {
            **inherited,
            **self.project.env,
            **_DETERMINISM,
            "PYTHONPATH": str(self.import_root(tree)),
        }

    def run(
        self,
        tree: Path,
        selection: Sequence[str],
        *,
        junit: Path | None = None,
        extra_args: Sequence[str] = (),
        timeout: int = DEFAULT_TIMEOUT,
    ) -> TestRun:
        """Run ``selection`` in ``tree``; ``junit`` also records every test's outcome."""
        cmd = [
            str(self.python),
            "-m",
            "pytest",
            *_PYTEST_FLAGS,
            "-q",
            "--no-header",
            "--tb=short",
            *self.project.pytest_args,
            *extra_args,
            *([f"--junitxml={junit}", "-o", "junit_family=xunit1"] if junit else []),
            *selection,
        ]
        try:
            proc = subprocess.run(  # noqa: S603 - our environment's python and pytest arguments
                cmd,
                cwd=tree,
                env=self.env(tree),
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return TestRun("timeout", f"exceeded {timeout}s")
        tail = "\n".join(f"{proc.stdout}\n{proc.stderr}".strip().splitlines()[-25:])
        return TestRun(_outcome(proc.returncode), tail)

    def write_runner(self, tree: Path, destination: Path) -> Path:
        """A ``run-tests`` script bound to ``tree``, for an agent to call.

        The path is absolute, so it stays right wherever in the tree the agent
        ``cd``s, and it needs no knowledge of the environment's layout.
        """
        destination.parent.mkdir(parents=True, exist_ok=True)
        variables = {**self.project.env, **_DETERMINISM, "PYTHONPATH": str(self.import_root(tree))}
        exports = " ".join(f"{key}={shlex.quote(value)}" for key, value in variables.items())
        flags = " ".join(shlex.quote(arg) for arg in (*_PYTEST_FLAGS, *self.project.pytest_args))
        destination.write_text(
            "#!/bin/sh\n"
            f"# Runs the project's tests against the checkout at {tree}\n"
            f"export {exports}\n"
            f'exec {shlex.quote(str(self.python))} -m pytest {flags} "$@"\n',
            encoding="utf-8",
        )
        mode = destination.stat().st_mode
        destination.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return destination


def _outcome(exit_code: int) -> Outcome:
    """pytest's exit code: 0 all passed, 1 some failed, anything else did not run."""
    if exit_code == 0:
        return "passed"
    if exit_code == 1:
        return "failed"
    return "error"


def distribution_name(project_dir: Path) -> str | None:
    """The project's distribution name from ``pyproject.toml``, if it declares one."""
    pyproject = project_dir / "pyproject.toml"
    if not pyproject.exists():
        return None
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return None
    name = data.get("project", {}).get("name")
    return name if isinstance(name, str) else None
