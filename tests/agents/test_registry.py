"""Agents are created by name, including ones other packages provide."""

from __future__ import annotations

from typing import Any

import pytest

from bugbout.agents import Codex, CodingAgent, agent_types, create_agent, registry
from bugbout.errors import ConfigError


class TestCreateAgent:
    def test_a_built_in_agent_by_name(self) -> None:
        agent = create_agent("codex", model="gpt-5.1-codex", reasoning_effort="low")
        assert isinstance(agent, Codex) and agent.reasoning_effort == "low"

    def test_an_unknown_type_lists_the_choices(self) -> None:
        with pytest.raises(ConfigError, match=r"unknown agent type 'cursor'.*claude-code"):
            create_agent("cursor", model="x")

    def test_a_misspelled_option_is_a_configuration_error(self) -> None:
        with pytest.raises(ConfigError, match="invalid options for a codex agent"):
            create_agent("codex", model="gpt-5", reasoning_efort="low")


class _EntryPoint:
    def __init__(self, name: str, loaded: Any) -> None:
        self.name = name
        self._loaded = loaded

    def load(self) -> Any:
        if isinstance(self._loaded, Exception):
            raise self._loaded
        return self._loaded


def test_plugins_add_agents_without_replacing_built_ins(monkeypatch: pytest.MonkeyPatch) -> None:
    class Plugin(Codex):
        harness = "plugin"

    found = [
        _EntryPoint("plugin", Plugin),
        _EntryPoint("codex", Plugin),
        _EntryPoint("broken", ImportError("missing dependency")),
        _EntryPoint("not-an-agent", object),
    ]
    monkeypatch.setattr(registry, "entry_points", lambda group: found)
    types_ = agent_types()
    assert types_["plugin"] is Plugin and types_["codex"] is Codex
    assert "broken" not in types_ and "not-an-agent" not in types_
    assert issubclass(types_["plugin"], CodingAgent)
