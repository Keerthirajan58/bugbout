"""A mini-swe-agent environment that runs every command inside a seatbelt sandbox.

Loaded by mini-swe-agent's own interpreter (via ``PYTHONPATH``), not by
Verdict's, so it depends on nothing but mini-swe-agent itself. The agent's
model calls stay in the mini-swe-agent process; each shell command it issues is
wrapped in ``sandbox-exec`` with the profile the arena wrote for the attempt.
"""

from __future__ import annotations

import os
import shlex
from typing import Any

from minisweagent.environments.local import LocalEnvironment  # type: ignore[import-not-found]


class SeatbeltEnvironment(LocalEnvironment):  # type: ignore[misc]
    def execute(self, action: dict[str, Any], cwd: str = "", *, timeout: int | None = None) -> Any:
        profile = os.environ["VERDICT_SANDBOX_PROFILE"]
        command = str(action.get("command", ""))
        wrapped = (
            f"/usr/bin/sandbox-exec -f {shlex.quote(profile)} /bin/bash -c {shlex.quote(command)}"
        )
        return super().execute({**action, "command": wrapped}, cwd, timeout=timeout)
