"""Cross-examination: every exhibit, run against every candidate fix.

For one task the matrix has a row per tree - the untouched base, and each
contestant's fix applied to it - and a column per test. Three kinds of test
share it, and the judge treats each differently:

* **Contestants' own tests**, each run against everyone else's fix.
* **Examiners' tests**, written without seeing any patch.
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

from bugbout.arena.exhibits import KINDS, Exhibit, Kind
from bugbout.errors import PatchError
from bugbout.tasks.task import Task
from bugbout.workspace.junit import read_junit
from bugbout.workspace.patch import Patch
from bugbout.workspace.workspace import Workspace

BASE = "base"
EXHIBIT_PREFIX = "test_bugbout__"
SUITE_TIMEOUT = 900

# One exhibit that fails to import must not abort the session: by default pytest
# stops at a collection error, which would leave a whole tree without results.
_PYTEST_ARGS = ("-p", "no:xdist", "--continue-on-collection-errors")

_CODE = {"passed": "P", "failed": "F", "error": "E", "skipped": "S", "absent": "-"}
_OUTCOME = {code: outcome for outcome, code in _CODE.items()}


def module_name(author: str) -> str:
    return EXHIBIT_PREFIX + re.sub(r"[^a-z0-9]+", "_", author.lower()).strip("_")


@dataclass
class Matrix:
    """Every test's outcome on every tree, for one task."""

    task_id: str
    trees: list[str]
    exhibits: dict[str, str]
    """author -> the module its exhibit was installed as."""

    kinds: dict[str, Kind] = field(default_factory=dict)
    vendors: dict[str, str] = field(default_factory=dict)
    """Vendor of every exhibit author and every candidate."""

    outcomes: dict[str, dict[str, str]] = field(default_factory=dict)
    """tree -> test id -> passed / failed / error / skipped."""

    lines_changed: dict[str, int] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)

    @property
    def candidates(self) -> list[str]:
        """Contestants whose fix applied and produced results."""
        return [t for t in self.trees if t != BASE and self.outcomes.get(t)]

    def author_of(self, test_id: str) -> str | None:
        """Which exhibit a test belongs to; ``None`` for the repository's own tests."""
        # The class name is dotted - ``tests.test_bugbout__x`` or ``tests.test_bugbout__x.TestY``.
        parts = test_id.split("::", 1)[0].split(".")
        for author, name in self.exhibits.items():
            if name in parts:
                return author
        return None

    def passes(self, tree: str, test_id: str) -> bool:
        return self.outcomes.get(tree, {}).get(test_id) == "passed"

    def to_dict(self) -> dict[str, Any]:
        """Compact form: one list of test ids, then one character per test for each tree.

        A task's matrix can hold thousands of tests across several trees; as
        nested JSON that is megabytes, as a shared header it stays small.
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
        kinds = dict(data.get("kinds", {}))
        # An unknown kind would silently fall out of every policy's evidence.
        if unknown := {k for k in kinds.values() if k not in KINDS}:
            raise ValueError(f"matrix {data['task_id']}: unknown exhibit kind(s) {unknown}")
        tests: list[str] = data["tests"]
        return cls(
            task_id=data["task_id"],
            trees=list(data["trees"]),
            exhibits=dict(data["exhibits"]),
            kinds=kinds,
            vendors=dict(data.get("vendors", {})),
            outcomes={
                tree: {t: _OUTCOME[c] for t, c in zip(tests, row, strict=True) if c != "-"}
                for tree, row in data["rows"].items()
            },
            lines_changed=dict(data.get("lines_changed", {})),
            notes=dict(data.get("notes", {})),
        )


class CrossExaminer:
    """Builds a task's matrix in throwaway trees, several trees at once."""

    def __init__(self, workspace: Workspace, *, run_suite: bool = True, workers: int = 4) -> None:
        self.workspace = workspace
        self.run_suite = run_suite
        """Also run the repository's existing tests, so breakage shows. Slower on big suites."""
        self.workers = workers

    def build(
        self,
        task: Task,
        fixes: dict[str, Patch],
        exhibits: list[Exhibit],
        vendors: dict[str, str],
    ) -> Matrix:
        """``fixes`` maps each contestant to its fix; ``vendors`` names each one's vendor."""
        usable = [e for e in exhibits if e.usable]
        matrix = Matrix(
            task_id=task.task_id,
            trees=[BASE, *sorted(fixes)],
            exhibits={e.author: module_name(e.author) for e in usable},
            kinds={e.author: e.kind for e in usable},
            vendors={**{e.author: e.vendor for e in usable}, **vendors},
            lines_changed={name: fix.lines_changed() for name, fix in fixes.items()},
        )
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            for tree, outcomes, note in pool.map(
                lambda tree: self._run(task, tree, fixes.get(tree), usable), matrix.trees
            ):
                matrix.outcomes[tree] = outcomes
                if note:
                    matrix.notes[tree] = note
        return matrix

    def _run(
        self, task: Task, name: str, fix: Patch | None, exhibits: list[Exhibit]
    ) -> tuple[str, dict[str, str], str]:
        """One tree: the base, plus a fix if given, plus every exhibit, then the tests."""
        test_root = self.workspace.project.test_root.strip("/")
        tree = self.workspace.scratch / task.task_id / f"matrix-{module_name(name)}"
        self.workspace.checkout(task, tree)
        try:
            if fix is not None:
                try:
                    fix.apply(tree)
                except PatchError as exc:
                    return name, {}, f"fix did not apply: {exc}"
            installed = []
            for exhibit in exhibits:
                path = Path(test_root) / f"{module_name(exhibit.author)}.py"
                (tree / path).parent.mkdir(parents=True, exist_ok=True)
                (tree / path).write_text(exhibit.test_code, encoding="utf-8")
                installed.append(path.as_posix())
            selection = [test_root] if self.run_suite else installed
            if not selection:
                return name, {}, "nothing to run"
            junit = tree.with_name(f"{tree.name}.junit.xml")
            junit.unlink(missing_ok=True)
            result = self.workspace.environment.run(
                tree, selection, junit=junit, extra_args=_PYTEST_ARGS, timeout=SUITE_TIMEOUT
            )
            outcomes = read_junit(junit)
            junit.unlink(missing_ok=True)
            return name, outcomes, result.tail if result.outcome == "timeout" else ""
        finally:
            shutil.rmtree(tree, ignore_errors=True)
