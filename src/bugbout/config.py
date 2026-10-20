"""``bugbout.toml``: what a project's bout runs on, and with which agents.

Every setting has a default except the package directory, so a minimal file is::

    [project]
    source = "src/mypkg"

    [[contestants]]
    agent = "codex"

Keys are written with dashes, as in ``pyproject.toml``; an agent's extra keys are
passed to its adapter, so ``reasoning-effort = "high"`` reaches ``Codex`` as
``reasoning_effort``.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bugbout.arena.judge import DEFAULT_POLICY
from bugbout.errors import ConfigError

CONFIG_FILE = "bugbout.toml"


def _dashed(name: str) -> str:
    return name.replace("_", "-")


class _Section(BaseModel):
    model_config = ConfigDict(
        extra="forbid", alias_generator=_dashed, populate_by_name=True, frozen=True
    )


class ProjectSettings(_Section):
    """``[project]``: the code under test and how to run its tests."""

    source: str
    """The package directory, e.g. ``src/mypkg``."""

    tests: str = "tests"
    repository: str = "."
    """A path relative to this file, or a git URL to clone."""

    github: str | None = None
    """``owner/name`` to read issues from, when ``origin`` is not on GitHub."""

    python: str = "3.12"
    test_dependencies: tuple[str, ...] = ("pytest",)
    pytest_args: tuple[str, ...] = ()
    env: dict[str, str] = Field(default_factory=dict)
    install_project: bool = True
    interpreter: str | None = None
    """An existing Python, with pytest and the project's dependencies, to run tests
    with instead of an environment bugbout builds."""


class TaskSettings(_Section):
    """``[tasks]``: which fixes from the history become tasks."""

    since: str | None = None
    limit: int = Field(default=20, ge=1)
    max_source_files: int = Field(default=2, ge=1)


class RunSettings(_Section):
    """``[run]``: how agents are run."""

    sandbox: str = "auto"
    timeout: int = Field(default=1800, ge=60)
    """Seconds one agent may spend on one task."""

    quotas: dict[str, int] = Field(default_factory=dict)
    """How many runs may share each quota at once; one by default."""


class JudgeSettings(_Section):
    """``[judge]``: how cross-examination picks a patch."""

    policy: str = DEFAULT_POLICY
    run_suite: bool = True
    """Also run the repository's own tests against every fix, so breakage shows."""


class AgentSettings(BaseModel):
    """One ``[[contestants]]`` entry: an agent type, its model, and adapter options."""

    model_config = ConfigDict(
        extra="allow", alias_generator=_dashed, populate_by_name=True, frozen=True
    )

    agent: str
    model: str | None = None
    name: str | None = None
    vendor: str | None = None
    quota: str | None = None

    def options(self) -> dict[str, Any]:
        """The adapter's keyword arguments, with dashes turned into underscores."""
        extra = {key.replace("-", "_"): value for key, value in (self.model_extra or {}).items()}
        identity = {
            "model": self.model,
            "name": self.name,
            "vendor": self.vendor,
            "quota": self.quota,
        }
        return {**extra, **{key: value for key, value in identity.items() if value is not None}}


class ExaminerSettings(AgentSettings):
    """One ``[[examiners]]`` entry: an agent that writes tests from the issue alone."""

    assignment: Literal["reproduce", "specify"] = "reproduce"


class Config(_Section):
    project: ProjectSettings
    tasks: TaskSettings = TaskSettings()
    run: RunSettings = RunSettings()
    judge: JudgeSettings = JudgeSettings()
    contestants: tuple[AgentSettings, ...] = ()
    examiners: tuple[ExaminerSettings, ...] = ()


def load_config(path: Path) -> Config:
    """Read and validate ``path``; every problem is reported in one :class:`ConfigError`."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"{path} not found; run `bugbout init` to create one") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc
    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        problems = "\n".join(
            f"  {'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise ConfigError(f"{path} has problems:\n{problems}") from exc
