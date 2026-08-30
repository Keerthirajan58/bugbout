"""Run one generated test inside a target workspace.

The isolation story, stated plainly rather than overclaimed:

* the test runs in the target's **own virtualenv**, as a **separate process**,
  under a **hard timeout**, with the hermetic environment from phase 0;
* generated code is **statically screened** first (see :mod:`guards`), which is
  what stops a test from reading source instead of exercising behaviour;
* the test file is written into the checkout and **removed in a ``finally``**,
  so a crashed run cannot leave it behind to pollute a later suite run.

This is process isolation, **not a security boundary**. It contains accidents -
a runaway loop, an accidental file write - not a determined attacker. That is a
reasonable trade when the code under audit is a well-known open-source library
and the generated tests are screened first. The Docker backend, used in CI where
Docker is already present, adds the real boundary.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from precedent.logging import get_logger
from precedent.sandbox.guards import inspect_test_code
from precedent.targets.workspace import TargetWorkspace

log = get_logger(__name__)

Outcome = Literal["passed", "failed", "error", "timeout", "rejected"]


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """What happened when a generated test was run."""

    outcome: Outcome
    duration_seconds: float
    stdout: str
    stderr: str
    rejection_reason: str | None = None

    @property
    def passed(self) -> bool:
        return self.outcome == "passed"

    @property
    def failed(self) -> bool:
        """The test ran and its assertions did not hold.

        Distinct from ``error``: a test that fails to import or raises during
        setup proves nothing about behaviour, so the two must not be conflated.
        """
        return self.outcome == "failed"

    def tail(self, lines: int = 25) -> str:
        """The end of the output, which is where pytest puts the reason."""
        combined = f"{self.stdout}\n{self.stderr}".strip()
        return "\n".join(combined.splitlines()[-lines:])


class SubprocessSandbox:
    """Executes a generated test against a prepared workspace."""

    name = "subprocess"

    def __init__(self, *, timeout: int = 60) -> None:
        self.timeout = timeout

    def run(self, workspace: TargetWorkspace, test_code: str) -> ExecutionResult:
        """Screen, then execute, a single generated test."""
        verdict = inspect_test_code(test_code)
        if not verdict.allowed:
            log.debug("Rejected generated test: %s", verdict.reason)
            return ExecutionResult(
                outcome="rejected",
                duration_seconds=0.0,
                stdout="",
                stderr="",
                rejection_reason=verdict.reason,
            )

        # Written inside the target's test directory so that its conftest and
        # import paths apply exactly as they would for a real test. The unique
        # name prevents collisions between parallel workers.
        test_file = workspace.test_dir / f"test_precedent_{uuid.uuid4().hex[:12]}.py"
        try:
            test_file.write_text(test_code, encoding="utf-8")
            run = workspace.run_tests(
                selection=(str(test_file),),
                extra_args=("--tb=short", "-p", "no:randomly"),
                timeout=self.timeout,
            )
        finally:
            test_file.unlink(missing_ok=True)

        return ExecutionResult(
            outcome=_map_outcome(run.outcome),
            duration_seconds=run.duration_seconds,
            stdout=run.stdout,
            stderr=run.stderr,
        )


def _map_outcome(suite_outcome: str) -> Outcome:
    match suite_outcome:
        case "passed":
            return "passed"
        case "failed":
            return "failed"
        case "timeout":
            return "timeout"
        case _:
            # Collection errors, usage errors, or a file pytest could not even
            # load. The test never ran, so it says nothing either way.
            return "error"
