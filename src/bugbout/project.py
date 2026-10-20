"""A configured project: its settings, where its results go, and the objects they describe.

Results live with the project, in ``.bugbout/`` beside ``bugbout.toml``; heavy,
disposable state (environments, checkouts, agents' working copies) lives in a
cache directory of its own, so it never lands in anyone's repository.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from bugbout.agents.base import CodingAgent
from bugbout.agents.registry import create_agent
from bugbout.arena.briefs import ASSIGNMENTS, Assignment
from bugbout.config import CONFIG_FILE, AgentSettings, Config, load_config
from bugbout.errors import ConfigError
from bugbout.home import bugbout_home
from bugbout.naming import slug
from bugbout.tasks.mining import MiningRules
from bugbout.workspace.environment import PythonProject
from bugbout.workspace.repository import Repository

RESULTS_DIR = ".bugbout"
_URL = re.compile(r"^(?:[a-z][a-z0-9+.-]*://|git@)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Examiner:
    """An agent, and the examiner's brief it works to."""

    agent: CodingAgent
    assignment: Assignment

    @property
    def author(self) -> str:
        return f"{self.assignment.name}/{self.agent.name}"


class Project:
    def __init__(self, config: Config, root: Path) -> None:
        self.config = config
        self.root = root
        """The directory that holds ``bugbout.toml``."""

    @classmethod
    def load(cls, path: Path | str = ".") -> Project:
        """The project configured by ``path`` - a ``bugbout.toml``, or a directory holding one."""
        location = Path(path)
        file = location / CONFIG_FILE if location.is_dir() else location
        return cls(load_config(file), file.resolve().parent)

    # --- where things live --------------------------------------------------------------

    @property
    def name(self) -> str:
        return self.config.project.github or self.root.name

    @property
    def results_dir(self) -> Path:
        return self.root / RESULTS_DIR

    @property
    def cache_dir(self) -> Path:
        """This project's own directory in the cache, stable for as long as it stays put."""
        digest = hashlib.sha256(str(self.root).encode()).hexdigest()[:10]
        return bugbout_home() / f"{slug(self.root.name)}-{digest}"

    @property
    def repository_url(self) -> str | None:
        """The URL to clone, if the repository is remote rather than a local path."""
        location = self.config.project.repository
        return location if _URL.match(location) else None

    def open_repository(self) -> Repository:
        """The local repository, or a clone of the remote one kept in the cache."""
        if url := self.repository_url:
            return Repository.clone(url, self.cache_dir / "repository")
        return Repository.open((self.root / self.config.project.repository).resolve())

    # --- what the settings describe ---------------------------------------------------

    def python_project(self) -> PythonProject:
        settings = self.config.project
        return PythonProject(
            source_root=settings.source,
            test_root=settings.tests,
            python=settings.python,
            dependencies=settings.test_dependencies,
            pytest_args=settings.pytest_args,
            env=dict(settings.env),
            install_project=settings.install_project,
            interpreter=settings.interpreter,
        )

    def mining_rules(self) -> MiningRules:
        return MiningRules(
            source_root=self.config.project.source,
            test_root=self.config.project.tests,
            since=self.config.tasks.since,
            limit=self.config.tasks.limit,
            max_source_files=self.config.tasks.max_source_files,
        )

    def contestants(self) -> list[CodingAgent]:
        agents = [_agent(settings) for settings in self.config.contestants]
        _require_unique([agent.name for agent in agents], "contestants")
        return agents

    def examiners(self) -> list[Examiner]:
        examiners = [
            Examiner(_agent(settings), ASSIGNMENTS[settings.assignment])
            for settings in self.config.examiners
        ]
        _require_unique([examiner.author for examiner in examiners], "examiners")
        return examiners


def _agent(settings: AgentSettings) -> CodingAgent:
    return create_agent(settings.agent, **settings.options())


def _require_unique(names: list[str], role: str) -> None:
    if duplicates := sorted({name for name in names if names.count(name) > 1}):
        raise ConfigError(
            f"two {role} are both called {duplicates[0]!r}; give one of them a `name`"
        )
