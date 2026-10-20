"""Ground truth for a task, defined exactly the way SWE-bench defines it.

The maintainers' real fix decides what "resolved" means, through two sets of
tests drawn from the test files that fix touched:

``FAIL_TO_PASS``  fail before the maintainers' fix and pass after it. A patch
                  must make every one of them pass: it fixed the bug.
``PASS_TO_PASS``  pass both before and after the maintainers' fix. A patch
                  must keep every one of them passing: it broke nothing.

A test the maintainers' own fix changed is never held against anyone else's
fix, and tests whose outcome differs between two runs of the fixed tree are
excluded as unstable, so flakiness cannot decide a verdict. None of this is
ever shown to an agent; it exists only to score them.
"""

from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from bugbout.errors import PatchError
from bugbout.tasks.task import Task
from bugbout.workspace.junit import read_junit
from bugbout.workspace.patch import Patch
from bugbout.workspace.workspace import Workspace


@dataclass(frozen=True, slots=True)
class Oracle:
    task_id: str
    fail_to_pass: tuple[str, ...]
    pass_to_pass: tuple[str, ...]
    unstable: tuple[str, ...] = ()
    note: str = ""

    @property
    def valid(self) -> bool:
        """Scorable only if the fix made at least one test go from failing to passing."""
        return bool(self.fail_to_pass)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "valid": self.valid}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Oracle:
        return cls(
            task_id=data["task_id"],
            fail_to_pass=tuple(data["fail_to_pass"]),
            pass_to_pass=tuple(data["pass_to_pass"]),
            unstable=tuple(data.get("unstable", ())),
            note=data.get("note", ""),
        )


@dataclass(frozen=True, slots=True)
class Resolution:
    """How one patch fares against a task's oracle."""

    task_id: str
    entrant: str
    applied: bool
    resolved: bool
    fail_to_pass_passed: int
    fail_to_pass_total: int
    pass_to_pass_failed: tuple[str, ...]
    pass_to_pass_total: int
    detail: str = ""

    @property
    def fixed_bug(self) -> bool:
        return self.applied and self.fail_to_pass_passed == self.fail_to_pass_total > 0

    @property
    def broke_something(self) -> bool:
        return bool(self.pass_to_pass_failed)

    @classmethod
    def unresolved(cls, task_id: str, entrant: str, oracle: Oracle, detail: str) -> Resolution:
        """An attempt that left no usable fix: no patch, or one that does not apply."""
        return cls(
            task_id=task_id,
            entrant=entrant,
            applied=False,
            resolved=False,
            fail_to_pass_passed=0,
            fail_to_pass_total=len(oracle.fail_to_pass),
            pass_to_pass_failed=(),
            pass_to_pass_total=len(oracle.pass_to_pass),
            detail=detail,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Resolution:
        payload = {k: v for k, v in data.items() if k in set(cls.__slots__)}
        payload["pass_to_pass_failed"] = tuple(payload["pass_to_pass_failed"])
        return cls(**payload)


class Referee:
    """Builds oracles and scores patches, in throwaway trees."""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    def oracle(self, task: Task) -> Oracle:
        """Run the fix's test files before and after the maintainers' fix."""
        before = self._tree(task, "before", task.base_commit)
        self.workspace.install_fix_tests(task, before)
        after = self._tree(task, "after", task.fix_commit)
        try:
            was = self._outcomes(before, task)
            first, second = self._outcomes(after, task), self._outcomes(after, task)
        finally:
            for tree in (before, after):
                shutil.rmtree(tree, ignore_errors=True)

        unstable = {t for t in first.keys() | second.keys() if first.get(t) != second.get(t)}
        stable = sorted(t for t, o in first.items() if o == "passed" and t not in unstable)
        fail_to_pass = tuple(t for t in stable if was.get(t) != "passed")
        pass_to_pass = tuple(t for t in stable if was.get(t) == "passed")
        note = (
            f"{len(fail_to_pass)} fail-to-pass, {len(pass_to_pass)} pass-to-pass, "
            f"{len(unstable)} unstable"
            if fail_to_pass
            else "the maintainers' fix made no test go from failing to passing"
        )
        return Oracle(task.task_id, fail_to_pass, pass_to_pass, tuple(sorted(unstable)), note)

    def score(self, task: Task, oracle: Oracle, patch: Patch, entrant: str) -> Resolution:
        """Apply a patch's source changes, then the maintainers' tests, and run them.

        Tests an entrant wrote are evidence for the judge, not part of the fix, so
        only changes outside the test directory are applied - exactly as SWE-bench
        applies its test patch over the model's patch.
        """
        fix = patch.only(under=self.workspace.project.test_root, keep=False)
        if fix.empty:
            return Resolution.unresolved(task.task_id, entrant, oracle, "no fix produced")
        tree = self._tree(task, f"score-{_safe(entrant)}", task.base_commit)
        try:
            try:
                fix.apply(tree)
            except PatchError as exc:
                return Resolution.unresolved(task.task_id, entrant, oracle, f"did not apply: {exc}")
            self.workspace.install_fix_tests(task, tree)
            outcomes = self._outcomes(tree, task)
        finally:
            shutil.rmtree(tree, ignore_errors=True)

        passed = sum(outcomes.get(t) == "passed" for t in oracle.fail_to_pass)
        broken = tuple(t for t in oracle.pass_to_pass if outcomes.get(t) != "passed")
        return Resolution(
            task_id=task.task_id,
            entrant=entrant,
            applied=True,
            resolved=oracle.valid and passed == len(oracle.fail_to_pass) and not broken,
            fail_to_pass_passed=passed,
            fail_to_pass_total=len(oracle.fail_to_pass),
            pass_to_pass_failed=broken,
            pass_to_pass_total=len(oracle.pass_to_pass),
            detail=(
                f"fail-to-pass {passed}/{len(oracle.fail_to_pass)}, "
                f"pass-to-pass broken {len(broken)}/{len(oracle.pass_to_pass)}"
            ),
        )

    def _tree(self, task: Task, name: str, commit: str) -> Path:
        tree = self.workspace.scratch / task.task_id / name
        self.workspace.repository.archive(commit, tree)
        return tree

    def _outcomes(self, tree: Path, task: Task) -> dict[str, str]:
        if not task.test_modules:
            return {}  # never fall through to running the whole suite
        junit = tree.with_name(f"{tree.name}.junit.xml")
        junit.unlink(missing_ok=True)
        self.workspace.environment.run(tree, task.test_modules, junit=junit)
        outcomes = read_junit(junit)
        junit.unlink(missing_ok=True)
        return outcomes


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
