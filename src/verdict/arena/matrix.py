"""Cross-examination: every exhibit, run against every candidate fix.

For one task the matrix has a row per tree - the untouched base, and each
contestant's fix applied to it - and a column per test. Three kinds of test
share it, and the judge treats each differently:

* **Contestants' own tests** (``self``), each run against everyone else's fix.
* **Blind examiners' tests** (``blind``), written without seeing any patch.
* **The repository's existing suite**, so a fix that breaks something it should
  not is visible without anyone having to predict it.

Only the fix part of a patch is applied - never a contestant's edits to the
suite - and every exhibit is installed under its own module name, so no
author's tests can shadow another's. Nothing here consults a model: the matrix
is execution, start to finish.
"""

from __future__ import annotations

import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from verdict.arena.assignments import REGRESSION_TEST
from verdict.arena.examiners.exhibit import Exhibit, Kind
from verdict.arena.patch import Patch, PatchError
from verdict.arena.truth import read_junit
from verdict.arena.vendors import vendor_of
from verdict.review.bench import TaskBench
from verdict.review.tasks import IssueTask

BASE = "base"
EXHIBIT_PREFIX = "test_verdict__"
SUITE_TIMEOUT = 900

_PYTEST_ARGS = (
    "-p",
    "no:xdist",
    # One exhibit that fails to import must not abort the session: pytest's
    # default is to stop at a collection error, which on two tasks reduced a
    # 1,300-test matrix to a handful of cells.
    "--continue-on-collection-errors",
    # Nose-era suites name files `*_tests.py`, which pytest's default discovery
    # skips; arrow's 2019 history is one of them.
    "-o",
    "python_files=test_*.py *_test.py *_tests.py",
)

_CODE = {"passed": "P", "failed": "F", "error": "E", "skipped": "S", "absent": "-"}
_OUTCOME = {v: k for k, v in _CODE.items()}


def module_name(author: str) -> str:
    return EXHIBIT_PREFIX + re.sub(r"[^a-z0-9]+", "_", author.lower()).strip("_")


def self_exhibits(task: IssueTask, patches: dict[str, Patch], test_root: str) -> list[Exhibit]:
    """Each contestant's own regression test, as an exhibit against the others."""
    exhibits = []
    for author, patch in sorted(patches.items()):
        created = patch.only(under=test_root, keep=True).created()
        code = created.get(f"{test_root}/{REGRESSION_TEST}") or next(
            (c for p, c in sorted(created.items()) if "test" in Path(p).name), ""
        )
        if code:
            exhibits.append(
                Exhibit(task.task_id, author, vendor_of(author), "self", code, model=author)
            )
    return exhibits


def fixes_only(patches: dict[str, Patch], test_root: str) -> dict[str, Patch]:
    """The part of each entrant's patch that is a fix, dropping empty ones."""
    fixes = {a: p.only(under=test_root, keep=False) for a, p in patches.items()}
    return {a: p for a, p in fixes.items() if not p.empty}


@dataclass
class Matrix:
    """Every test's outcome on every tree, for one task."""

    task_id: str
    trees: list[str]
    exhibits: dict[str, str]
    """author -> the module its exhibit was installed as."""

    kinds: dict[str, Kind] = field(default_factory=dict)
    """author -> ``blind`` or ``self``."""

    outcomes: dict[str, dict[str, str]] = field(default_factory=dict)
    """tree -> test id -> passed / failed / error / skipped."""

    lines_changed: dict[str, int] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)

    @property
    def candidates(self) -> list[str]:
        return [t for t in self.trees if t != BASE and self.outcomes.get(t)]

    def author_of(self, test_id: str) -> str | None:
        """Which exhibit a test belongs to; ``None`` for the repository's own tests."""
        module = test_id.split("::", 1)[0]
        for author, name in self.exhibits.items():
            if module.endswith(name):
                return author
        return None

    def passes(self, tree: str, test_id: str) -> bool:
        return self.outcomes.get(tree, {}).get(test_id) == "passed"

    def to_dict(self) -> dict[str, Any]:
        """Compact form: one table of test ids, one outcome string per tree.

        A task's matrix is ~1,500 tests across several trees. As nested JSON
        that is megabytes per task; as a shared header plus one character per
        cell it is small enough to commit as evidence.
        """
        tests = sorted({t for row in self.outcomes.values() for t in row})
        rows = {
            tree: "".join(_CODE[self.outcomes.get(tree, {}).get(t, "absent")] for t in tests)
            for tree in self.trees
        }
        data = asdict(self)
        del data["outcomes"]
        return {**data, "tests": tests, "rows": rows}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Matrix:
        tests: list[str] = data["tests"]
        return cls(
            task_id=data["task_id"],
            trees=list(data["trees"]),
            exhibits=dict(data["exhibits"]),
            kinds=dict(data.get("kinds", {})),
            outcomes={
                tree: {t: _OUTCOME[c] for t, c in zip(tests, row, strict=True) if c != "-"}
                for tree, row in data["rows"].items()
            },
            lines_changed=dict(data.get("lines_changed", {})),
            notes=dict(data.get("notes", {})),
        )


class CrossExaminer:
    """Builds the matrix for a task in throwaway trees, several trees at once."""

    def __init__(self, bench: TaskBench, scratch: Path, workers: int = 4) -> None:
        self.bench = bench
        self.scratch = scratch
        self.workers = workers

    def build(self, task: IssueTask, fixes: dict[str, Patch], exhibits: list[Exhibit]) -> Matrix:
        usable = [e for e in exhibits if e.usable]
        matrix = Matrix(
            task_id=task.task_id,
            trees=[BASE, *sorted(fixes)],
            exhibits={e.author: module_name(e.author) for e in usable},
            kinds={e.author: e.kind for e in usable},
            lines_changed={a: p.lines_changed() for a, p in fixes.items()},
        )
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            runs = pool.map(
                lambda tree: self._run(task, tree, fixes.get(tree), usable), matrix.trees
            )
            for tree, results, note in runs:
                matrix.outcomes[tree] = results
                if note:
                    matrix.notes[tree] = note
        return matrix

    def _run(
        self, task: IssueTask, tree_name: str, fix: Patch | None, exhibits: list[Exhibit]
    ) -> tuple[str, dict[str, str], str]:
        """One tree: the base, plus a fix if given, plus every exhibit; then the suite."""
        test_root = self.bench.target.test_root
        tree = self.scratch / task.task_id / f"matrix-{module_name(tree_name)}"
        shutil.rmtree(tree, ignore_errors=True)
        self.bench.archive(task.base_commit, tree)
        try:
            if fix is not None:
                try:
                    fix.apply(tree)
                except PatchError as exc:
                    return tree_name, {}, f"fix did not apply: {exc}"
            for exhibit in exhibits:
                path = tree / test_root / f"{module_name(exhibit.author)}.py"
                path.write_text(exhibit.test_code, encoding="utf-8")
            junit = tree.with_name(f"{tree.name}.junit.xml")
            junit.unlink(missing_ok=True)
            outcome, tail = self.bench.run_tests(
                tree, [test_root, *_PYTEST_ARGS], timeout=SUITE_TIMEOUT, junit=junit
            )
            return tree_name, read_junit(junit), tail if outcome == "timeout" else ""
        finally:
            shutil.rmtree(tree, ignore_errors=True)
