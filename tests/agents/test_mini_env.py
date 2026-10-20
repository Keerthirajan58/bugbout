"""mini-swe-agent's environment plugin runs every command inside bugbout's sandbox."""

from __future__ import annotations

import importlib
import json
import sys
import types
from typing import Any

import pytest

from bugbout.agents.mini_swe import PLUGINS, SANDBOX_PREFIX_ENV


def test_the_mini_swe_environment_wraps_every_command(monkeypatch: pytest.MonkeyPatch) -> None:
    executed: list[str] = []

    class LocalEnvironment:
        def execute(
            self, action: dict[str, Any], cwd: str = "", *, timeout: int | None = None
        ) -> Any:
            executed.append(action["command"])
            return {}

    local = types.ModuleType("minisweagent.environments.local")
    local.LocalEnvironment = LocalEnvironment  # type: ignore[attr-defined]
    for name in ("minisweagent", "minisweagent.environments"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "minisweagent.environments.local", local)
    monkeypatch.delitem(sys.modules, "bugbout_mini_env", raising=False)
    monkeypatch.syspath_prepend(str(PLUGINS))
    plugin = importlib.import_module("bugbout_mini_env")

    monkeypatch.setenv(SANDBOX_PREFIX_ENV, json.dumps(["sandbox-exec", "-p", "(version 1)"]))
    plugin.SandboxedEnvironment().execute({"command": "ls -la"})
    assert executed == ["sandbox-exec -p '(version 1)' /bin/bash -c 'ls -la'"]

    monkeypatch.delenv(SANDBOX_PREFIX_ENV)
    with pytest.raises(RuntimeError, match="refusing to run unsandboxed"):
        plugin.SandboxedEnvironment().execute({"command": "ls"})
