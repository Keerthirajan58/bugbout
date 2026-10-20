"""A mini-swe-agent environment that runs every command inside bugbout's sandbox.

Loaded by mini-swe-agent's own interpreter (through ``PYTHONPATH``), not by
bugbout's, so it depends on nothing but mini-swe-agent and the standard library.
bugbout passes the sandbox as a ready-made argv prefix in an environment variable.
"""

from __future__ import annotations

import json
import os
import shlex
from typing import Any

from minisweagent.environments.local import LocalEnvironment  # type: ignore[import-not-found]

SANDBOX_PREFIX_ENV = "BUGBOUT_SANDBOX_PREFIX"


class SandboxedEnvironment(LocalEnvironment):  # type: ignore[misc]
    def execute(self, action: dict[str, Any], cwd: str = "", *, timeout: int | None = None) -> Any:
        if SANDBOX_PREFIX_ENV not in os.environ:
            raise RuntimeError(f"{SANDBOX_PREFIX_ENV} is not set; refusing to run unsandboxed")
        prefix: list[str] = json.loads(os.environ[SANDBOX_PREFIX_ENV])
        command = str(action.get("command", ""))
        wrapped = shlex.join([*prefix, "/bin/bash", "-c", command]) if prefix else command
        return super().execute({**action, "command": wrapped}, cwd, timeout=timeout)
