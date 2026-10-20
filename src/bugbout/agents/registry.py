"""Agents by the names configuration files use.

Other packages can add agents without touching bugbout, by declaring an entry
point in the ``bugbout.agents`` group that names a :class:`CodingAgent` subclass::

    [project.entry-points."bugbout.agents"]
    my-agent = "my_package.agent:MyAgent"
"""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any

from bugbout.agents.base import CodingAgent
from bugbout.agents.claude_code import ClaudeCode
from bugbout.agents.codex import Codex
from bugbout.agents.command import CommandAgent
from bugbout.agents.gemini_cli import GeminiCli
from bugbout.agents.mini_swe import MiniSweAgent
from bugbout.errors import ConfigError
from bugbout.log import get_logger

log = get_logger(__name__)

ENTRY_POINT_GROUP = "bugbout.agents"
BUILTIN: dict[str, type[CodingAgent]] = {
    "codex": Codex,
    "claude-code": ClaudeCode,
    "gemini-cli": GeminiCli,
    "mini-swe-agent": MiniSweAgent,
    "command": CommandAgent,
}


def agent_types() -> dict[str, type[CodingAgent]]:
    """Every agent type by name: the built-in ones, then any that plugins provide."""
    found = dict(BUILTIN)
    for entry in entry_points(group=ENTRY_POINT_GROUP):
        if entry.name in found:
            log.warning("Ignoring plugin agent %r: the name is taken", entry.name)
            continue
        try:
            loaded = entry.load()
        except Exception as exc:  # a broken plugin must not take bugbout down with it
            log.warning("Could not load plugin agent %r: %s", entry.name, exc)
            continue
        if isinstance(loaded, type) and issubclass(loaded, CodingAgent):
            found[entry.name] = loaded
        else:
            log.warning("Ignoring plugin agent %r: not a CodingAgent subclass", entry.name)
    return found


def create_agent(kind: str, **options: Any) -> CodingAgent:
    """An agent of type ``kind``, configured with ``options``."""
    types = agent_types()
    if kind not in types:
        raise ConfigError(f"unknown agent type {kind!r}; available: {', '.join(sorted(types))}")
    try:
        return types[kind](**options)
    except TypeError as exc:
        raise ConfigError(f"invalid options for a {kind} agent: {exc}") from exc
