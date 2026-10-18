"""Cross-examination: every exhibit, run against every candidate patch.

For one task the matrix has a row per tree - the untouched base, and each
contestant's fix applied to it - and a column per test. The tests are of three
kinds, and the judge treats each differently:

* **Exhibits from contestants.** Each contestant was asked to write its own
  regression test. Here it is run against everyone *else's* fix as well.
* **Exhibits from blind examiners.** Written from the issue alone, never
  having seen a patch.
* **The repository's existing suite**, so a patch that breaks something it
  should not is visible without anyone having to predict it.

Only the fix part of a patch is applied - never a contestant's edits to the
test suite - and every exhibit is installed under its own file name, so no
author's tests can shadow another's.

Nothing here consults a model. The matrix is execution, start to finish.
"""

from __future__ import annotations

import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from verdict.arena.contestants import REGRESSION_TEST
from verdict.arena.examiners import Exhibit
from verdict.arena.patch import Patch, PatchError
from verdict.arena.truth import read_junit
from verdict.review.bench import TaskBench
from verdict.review.tasks import IssueTask

BASE = "base"
EXHIBIT_PREFIX = "test_verdict__"
SUITE_TIMEOUT = 900


def family_of(author: str) -> str:
    """The vendor behind an entrant or examiner. No vendor grades its own models."""
    name = author.lower()
    if "claude" in name:
        return "anthropic"
    if "gemini" in name or "gemma" in name:
        return "google"
    if "qwen" in name:
        return "alibaba"
    if "gpt" in name or "codex" in name or "openai" in name:
        return "openai"
    return name.split("/")[0]


def slug(author: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", author.lower()).strip("_")


def self_exhibits(task: IssueTask, entries: dict[str, Patch], test_root: str) -> list[Exhibit]:
    """Each contestant's own regression test, as an exhibit against the others."""
    exhibits = []
    for author, patch in sorted(entries.items()):
        created = patch.only(under=test_root, keep=True).created()
        code = created.get(f"{test_root}/{REGRESSION_TEST}")
        if code is None and created:
            # It wrote its test somewhere else under the test root; take the
            # first new test module rather than lose the evidence.
            code = next((c for p, c in sorted(created.items()) if "test" in Path(p).name), None)
        if code:
            exhibits.append(Exhibit(task.task_id, author, family_of(author), code, model=author))
    return exhibits


@dataclass
class Matrix:
    """Every test's outcome on every tree, for one task."""

    task_id: str
    trees: list[str]
    exhibits: dict[str, str]
    """author -> the test module name its exhibit was installed as."""

    outcomes: dict[str, dict[str, str]] = field(default_factory=dict)
    """tree -> test id -> passed / failed / error / skipped."""

    lines_changed: dict[str, int] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)

    def author_of(self, test_id: str) -> str | None:
        """Which exhibit a test belongs to; ``None`` for the repository's own tests."""
        module = test_id.split("::", 1)[0]
        for author, name in self.exhibits.items():
            if module.endswith(name):
                return author
        return None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Matrix:
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})


class CrossExaminer:
    """Builds the matrix for a task in throwaway trees, several trees at once."""

    def __init__(self, bench: TaskBench, scratch: Path, workers: int = 4) -> None:
        self.bench = bench
        self.scratch = scratch
        self.workers = workers

    def build(self, task: IssueTask, fixes: dict[str, Patch], exhibits: list[Exhibit]) -> Matrix:
        test_root = self.bench.target.test_root
        installed = {e.author: f"{EXHIBIT_PREFIX}{slug(e.author)}" for e in exhibits if e.test_code}
        matrix = Matrix(
            task.task_id,
            [BASE, *sorted(fixes)],
            installed,
            lines_changed={a: p.lines_changed() for a, p in fixes.items()},
        )

        def run(tree_name: str) -> tuple[str, dict[str, str], str]:
            tree = self.scratch / task.task_id / f"matrix-{slug(tree_name)}"
            shutil.rmtree(tree, ignore_errors=True)
            self.bench.archive(task.base_commit, tree)
            note = ""
            if tree_name != BASE:
                try:
                    fixes[tree_name].apply(tree)
                except PatchError as exc:
                    shutil.rmtree(tree, ignore_errors=True)
                    return tree_name, {}, f"fix did not apply: {exc}"
            for exhibit in exhibits:
                if exhibit.test_code:
                    path = tree / test_root / f"{installed[exhibit.author]}.py"
                    path.write_text(exhibit.test_code, encoding="utf-8")
            junit = tree.with_name(f"{tree.name}.junit.xml")
            junit.unlink(missing_ok=True)
            outcome, tail = self.bench.run_tests(
                tree, [test_root, "-p", "no:xdist"], timeout=SUITE_TIMEOUT, junit=junit
            )
            if outcome == "timeout":
                note = tail
            results = read_junit(junit)
            shutil.rmtree(tree, ignore_errors=True)
            return tree_name, results, note

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            for name, results, note in pool.map(run, matrix.trees):
                matrix.outcomes[name] = results
                if note:
                    matrix.notes[name] = note
        return matrix


def fixes_only(entries: dict[str, Patch], test_root: str) -> dict[str, Patch]:
    """The part of each entrant's patch that is a fix, dropping empty ones."""
    fixes = {a: p.only(under=test_root, keep=False) for a, p in entries.items()}
    return {a: p for a, p in fixes.items() if not p.empty}


__all__ = ["BASE", "CrossExaminer", "Matrix", "family_of", "fixes_only", "self_exhibits"]
