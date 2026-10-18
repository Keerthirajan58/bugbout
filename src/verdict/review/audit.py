"""Run Verdict's auditor against a diff a coding agent actually wrote.

This is the same apparatus the mutation ablation measures, pointed at a
different kind of change. It calls :meth:`EvalHarness.deliberate` - the exact
method the ablation calls - so a difference in the numbers cannot come from a
difference in the procedure.

What the auditor is asked is narrower than "is this diff correct", and saying so
matters. Verdict proves regressions: it writes a test that passes on the code
before the diff and fails after it. That answers "did this change break
something that worked", not "did it fix the reported bug". The study scores it
against the half of the ground truth it is built to detect, and reports the
other half separately rather than claiming credit for it.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from verdict.agents.change import CodeChange, FileVersion
from verdict.agents.prosecutor import Prosecutor
from verdict.agents.skeptic import Skeptic
from verdict.eval.harness import EvalHarness
from verdict.logging import get_logger
from verdict.review.bench import AgentDiff, TaskBench
from verdict.review.tasks import IssueTask
from verdict.targets.registry import TargetRepo
from verdict.targets.workspace import TargetWorkspace

log = get_logger(__name__)


class TaskWorkspace(TargetWorkspace):
    """Presents one task checkout as a workspace the sandbox can run tests in.

    The checkout has no ``.git`` and the package is not pip-installed - both
    deliberate, so the agent could not read the future and one virtualenv can
    serve twenty different historical commits. Imports are therefore resolved
    through ``PYTHONPATH``, which the base class strips.
    """

    def __init__(self, target: TargetRepo, repo_dir: Path, venv_dir: Path) -> None:
        super().__init__(target)
        self._repo_dir = repo_dir
        self._venv_dir = venv_dir

    @property
    def repo_dir(self) -> Path:
        return self._repo_dir

    @property
    def venv_dir(self) -> Path:
        return self._venv_dir

    def is_ready(self) -> bool:
        return self.python.exists() and self.repo_dir.exists()

    def test_env(self) -> dict[str, str]:
        env = super().test_env()
        env["PYTHONPATH"] = str((self.repo_dir / self.target.source_root).parent)
        return env

    def ensure_clean(self) -> bool:
        # There is no git checkout to compare against; the change object holds
        # both versions and reverts itself unconditionally.
        return True


@dataclass(frozen=True, slots=True)
class AuditOutcome:
    """What Verdict concluded about one agent-written diff."""

    task_id: str
    target_slug: str
    module_path: str
    function: str | None
    changed_files: int

    verdict: str
    flagged: bool
    """Whether the auditor proved a regression. The scored decision."""

    reason: str
    hypothesis: str = ""
    reasoning: str = ""
    test_name: str = ""
    test_code: str = ""
    original_behaviour: str = ""
    changed_behaviour: str = ""
    citation: str | None = None
    used_evidence: str = ""
    model: str = ""
    provider: str = ""
    repairs: int = 0
    gate_failures: int = 0
    review_reason: str | None = None
    retrieved: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def change_for(task: IssueTask, bench: TaskBench) -> CodeChange | None:
    """Build the change under review from what the agent left on disk.

    Measured, not described: the pristine archive against the working tree, so
    every edit the agent made is included whether or not it mentioned it.
    """
    work, clean = bench.workdir(task), bench.pristine(task)
    versions: list[FileVersion] = []
    for path in sorted(work.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = str(path.relative_to(work))
        if relative.startswith(f"{bench.target.test_root}{os.sep}"):
            continue
        before = clean / relative
        original = before.read_text(encoding="utf-8") if before.exists() else ""
        changed = path.read_text(encoding="utf-8")
        if original != changed:
            versions.append(FileVersion(relative, original, changed))
    return CodeChange.from_sources(versions)


def audit_task(
    task: IssueTask,
    diff: AgentDiff,
    bench: TaskBench,
    harness: EvalHarness,
    prosecutor: Prosecutor,
    skeptic: Skeptic,
) -> AuditOutcome:
    """Put one agent-written diff through the Prosecutor/Skeptic protocol."""
    if diff.touched_tests:
        # An agent that edited the suite can make any oracle pass, so the ground
        # truth for this task is no longer trustworthy. Excluded, and recorded
        # as excluded rather than dropped from the denominator quietly.
        return AuditOutcome(
            task_id=task.task_id,
            target_slug=task.target_slug,
            module_path="",
            function=None,
            changed_files=len(diff.changed_files),
            verdict="excluded",
            flagged=False,
            reason="the agent edited the test suite, so its ground truth is not trustworthy",
        )
    change = change_for(task, bench)
    if change is None:
        return AuditOutcome(
            task_id=task.task_id,
            target_slug=task.target_slug,
            module_path="",
            function=None,
            changed_files=0,
            verdict="no_change",
            flagged=False,
            reason="the agent changed nothing, so there is nothing to audit",
        )

    workspace = TaskWorkspace(bench.target, bench.workdir(task), bench.venv)
    # The protocol begins from the ORIGINAL code: the first execution must run
    # against the tree as it stood before the agent touched it.
    change.revert(workspace)
    try:
        result = harness.deliberate(workspace, change, prosecutor, skeptic)
    finally:
        # Leave the agent's diff in place, so scoring and re-auditing see the
        # same tree the agent produced.
        change.apply(workspace)

    accusation, verdict = result.accusation, result.verdict
    return AuditOutcome(
        task_id=task.task_id,
        target_slug=task.target_slug,
        module_path=change.module_path,
        function=change.function,
        changed_files=len(change.files),
        verdict=verdict.verdict,
        flagged=verdict.caught,
        reason=verdict.reason,
        hypothesis=accusation.hypothesis,
        reasoning=accusation.reasoning,
        test_name=accusation.test_name,
        test_code=accusation.test_code,
        original_behaviour=accusation.original_behaviour,
        changed_behaviour=accusation.changed_behaviour,
        citation=accusation.citation,
        used_evidence=accusation.used_evidence,
        model=accusation.model,
        provider=accusation.provider,
        repairs=result.repairs,
        gate_failures=result.gate_failures,
        review_reason=verdict.review_reason,
        retrieved=list(result.retrieved),
        error=accusation.error,
    )
