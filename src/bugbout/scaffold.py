"""``bugbout init``: a first ``bugbout.toml``, from what the repository already says."""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bugbout.agents.base import find_executable
from bugbout.agents.registry import BUILTIN
from bugbout.workspace.environment import distribution_name

_NOT_PACKAGES = frozenset(
    {"tests", "test", "docs", "doc", "examples", "example", "scripts", "benchmarks", "build"}
)
_TEST_GROUPS = ("test", "tests", "testing")
_AGENTS_WITH_DEFAULT_MODELS = ("codex", "claude-code", "gemini-cli")


@dataclass(frozen=True, slots=True)
class Layout:
    """Where a repository keeps its package and its tests, and what its tests need."""

    source: str | None
    tests: str | None
    test_dependencies: tuple[str, ...]


def detect_layout(repo: Path) -> Layout:
    source = _source(repo)
    tests = next((name for name in ("tests", "test") if (repo / name).is_dir()), None)
    if tests is None and source and (repo / source / "tests").is_dir():
        tests = f"{source}/tests"
    return Layout(source, tests, _test_dependencies(repo))


def installed_agents() -> list[str]:
    """Agents that are installed and can run on their CLI's default model."""
    return [
        name for name in _AGENTS_WITH_DEFAULT_MODELS if find_executable(BUILTIN[name].executable)
    ]


def render_config(layout: Layout, agents: list[str]) -> str:
    """A commented ``bugbout.toml`` for ``layout``, racing ``agents``."""
    contestants = "\n".join(f'[[contestants]]\nagent = "{name}"\n' for name in agents) or (
        '# [[contestants]]\n# agent = "codex"\n'
    )
    return f"""\
# bugbout.toml - which coding agent actually fixes bugs in this codebase?

[project]
source = {_string(layout.source or "")}          # the package directory
tests = {_string(layout.tests or "tests")}
test-dependencies = {_strings(layout.test_dependencies)}
# python = "3.12"
# pytest-args = []

[tasks]
limit = 20                  # how many past fixes to replay
# since = "2023-01-01"

[run]
timeout = 1800              # seconds per agent per task
# quotas = {{ codex = 2 }}    # runs at once per quota; 1 by default

[judge]
policy = "majority"         # majority, unanimous, strict or cross-vendor

# Contestants fix every task. Leave out `model` to use the CLI's default.
{contestants}
# [[contestants]]
# agent = "mini-swe-agent"
# model = "ollama/qwen3-coder:30b"

# [[contestants]]
# agent = "command"
# name = "aider"
# command = ["aider", "--yes-always", "--no-git", "--message-file", "{{prompt_file}}"]

# Examiners write tests from the issue alone, to cross-examine every fix.
# [[examiners]]
# agent = "codex"
# assignment = "reproduce"  # or "specify"
"""


def _source(repo: Path) -> str | None:
    for parent in ("src", "."):
        base = repo / parent
        if not base.is_dir():
            continue
        packages = sorted(
            path.name
            for path in base.iterdir()
            if path.is_dir()
            and (path / "__init__.py").exists()
            and path.name not in _NOT_PACKAGES
            and not path.name.startswith((".", "_"))
        )
        name = (distribution_name(repo) or "").replace("-", "_").lower()
        chosen = packages[0] if len(packages) == 1 else name if name in packages else None
        if chosen:
            return chosen if parent == "." else f"{parent}/{chosen}"
    return None


def _test_dependencies(repo: Path) -> tuple[str, ...]:
    """``pytest``, plus whatever the project's test extra or dependency group lists."""
    found = ["pytest"]
    pyproject = repo / "pyproject.toml"
    if not pyproject.exists():
        return tuple(found)
    try:
        data: dict[str, Any] = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return tuple(found)
    extras = data.get("project", {}).get("optional-dependencies", {})
    groups = data.get("dependency-groups", {})
    for key in _TEST_GROUPS:
        for requirement in (*extras.get(key, ()), *groups.get(key, ())):
            # A group may include another group by reference; only plain requirements count.
            if isinstance(requirement, str) and requirement not in found:
                found.append(requirement)
    return tuple(found)


def _string(value: str) -> str:
    return json.dumps(value)  # a JSON string is a valid TOML basic string


def _strings(values: tuple[str, ...]) -> str:
    return "[" + ", ".join(_string(value) for value in values) + "]"
