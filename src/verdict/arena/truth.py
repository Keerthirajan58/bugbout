"""Ground truth for an issue task, defined exactly the way SWE-bench defines it.

The maintainer's real fix decides what "resolved" means, through two sets of
tests drawn from the test files that fix touched:

``FAIL_TO_PASS``  fail on the base commit, pass with the maintainer's fix.
                  A patch must make every one of them pass: it fixed the bug.
``PASS_TO_PASS``  pass on the base commit AND with the maintainer's fix.
                  A patch must keep every one of them passing: it broke nothing.

The second definition is the reason this module exists. The earlier study
counted any failure among the pre-existing tests as a regression - but in four
of the six tasks it flagged, the maintainers' own fixes edited or deleted those
very tests, because the fix *intentionally* changed what they asserted. A test
the real fix also fails cannot be evidence against anyone else's fix.

Tests whose outcome differs between two runs of the maintainer's tree are
recorded as unstable and excluded from both sets, so flakiness can never decide
a verdict.

None of this is shown to a contestant, an examiner or the judge. It exists only
to score them.
"""

from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from verdict.arena.patch import Patch, PatchError
from verdict.review.bench import TaskBench
from verdict.review.tasks import IssueTask


def read_junit(path: Path) -> dict[str, str]:
    """Each test's outcome from a pytest JUnit report, keyed ``class::name``."""
    if not path.exists():
        return {}
    # Our own pytest's output, written moments ago into our own scratch tree.
    root = ET.parse(path).getroot()  # noqa: S314
    outcomes: dict[str, str] = {}
    for case in root.iter("testcase"):
        key = f"{case.get('classname', '')}::{case.get('name', '')}"
        if case.find("failure") is not None:
            outcomes[key] = "failed"
        elif case.find("error") is not None:
            outcomes[key] = "error"
        elif case.find("skipped") is not None:
            outcomes[key] = "skipped"
        else:
            outcomes[key] = "passed"
    return outcomes


@dataclass(frozen=True, slots=True)
class Oracle:
    """What the maintainer's fix proves about one task."""

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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def unproduced(cls, task_id: str, entrant: str, oracle: Oracle) -> Resolution:
        """An attempt that left no fix behind: unresolved, by definition."""
        return cls(
            task_id,
            entrant,
            True,
            False,
            0,
            len(oracle.fail_to_pass),
            (),
            len(oracle.pass_to_pass),
            "no fix produced",
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Resolution:
        payload = {k: v for k, v in data.items() if k in set(cls.__slots__)}
        payload["pass_to_pass_failed"] = tuple(payload["pass_to_pass_failed"])
        return cls(**payload)


class Referee:
    """Builds oracles and scores patches in throwaway trees under ``scratch``."""

    def __init__(self, bench: TaskBench, scratch: Path) -> None:
        self.bench = bench
        self.scratch = scratch

    def _outcomes(self, tree: Path, task: IssueTask) -> dict[str, str]:
        junit = tree.with_name(f"{tree.name}.junit.xml")
        junit.unlink(missing_ok=True)
        self.bench.run_tests(tree, list(task.test_files), junit=junit)
        return read_junit(junit)

    def _fresh(self, *parts: str) -> Path:
        path = self.scratch.joinpath(*parts)
        if path.exists():
            shutil.rmtree(path)
        return path

    def oracle(self, task: IssueTask) -> Oracle:
        """Run the fix's test files before and after the maintainer's fix."""
        before = self._fresh(task.task_id, "before")
        self.bench.archive(task.base_commit, before)
        self.bench.install_oracle(task, before)
        after = self._fresh(task.task_id, "after")
        self.bench.archive(task.fix_commit, after)

        was = self._outcomes(before, task)
        first, second = self._outcomes(after, task), self._outcomes(after, task)
        unstable = {t for t in first.keys() | second.keys() if first.get(t) != second.get(t)}
        stable_pass = sorted(t for t, o in first.items() if o == "passed" and t not in unstable)

        f2p = tuple(t for t in stable_pass if was.get(t) != "passed")
        p2p = tuple(t for t in stable_pass if was.get(t) == "passed")
        note = (
            f"{len(f2p)} fail-to-pass, {len(p2p)} pass-to-pass, {len(unstable)} unstable"
            if f2p
            else "the maintainer's fix made no test go from failing to passing"
        )
        if not was:
            note += "; the test files did not run at the base commit (collection error)"
        for tree in (before, after):
            shutil.rmtree(tree, ignore_errors=True)
        return Oracle(task.task_id, f2p, p2p, tuple(sorted(unstable)), note)

    def evaluate(self, task: IssueTask, oracle: Oracle, patch: Patch, entrant: str) -> Resolution:
        """Score a patch: its source changes, then the maintainer's tests on top.

        Only the patch's changes outside the test directory are applied. Tests
        an entrant wrote are evidence for the judge, not part of the fix, and the
        maintainer's test files are installed over the tree regardless - exactly
        as SWE-bench applies its test patch after the model's patch.
        """
        tree = self._fresh(task.task_id, f"eval-{_safe(entrant)}")
        self.bench.archive(task.base_commit, tree)
        try:
            patch.only(under=self.bench.target.test_root, keep=False).apply(tree)
        except PatchError as exc:
            shutil.rmtree(tree, ignore_errors=True)
            return Resolution(
                task.task_id,
                entrant,
                False,
                False,
                0,
                len(oracle.fail_to_pass),
                (),
                len(oracle.pass_to_pass),
                f"patch did not apply: {exc}",
            )
        self.bench.install_oracle(task, tree)
        outcomes = self._outcomes(tree, task)
        shutil.rmtree(tree, ignore_errors=True)

        f2p_passed = sum(outcomes.get(t) == "passed" for t in oracle.fail_to_pass)
        p2p_failed = tuple(t for t in oracle.pass_to_pass if outcomes.get(t) != "passed")
        resolved = oracle.valid and f2p_passed == len(oracle.fail_to_pass) and not p2p_failed
        detail = (
            f"fail-to-pass {f2p_passed}/{len(oracle.fail_to_pass)}, "
            f"pass-to-pass broken {len(p2p_failed)}/{len(oracle.pass_to_pass)}"
        )
        return Resolution(
            task.task_id,
            entrant,
            True,
            resolved,
            f2p_passed,
            len(oracle.fail_to_pass),
            p2p_failed,
            len(oracle.pass_to_pass),
            detail,
        )


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
