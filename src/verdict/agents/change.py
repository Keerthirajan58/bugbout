"""The change under review, whatever produced it.

Verdict's evaluation runs on mutants because they are objective ground truth,
but nothing about the Prosecutor or the Skeptic is mutation-specific: both only
ever need to know what the code said before and what it says after. This module
is that abstraction, and it is what lets the same audit run against a diff a
coding agent actually wrote.

A change carries every file it touches, so applying and reverting it is exact
rather than reconstructed. Reverting is unconditional: a leaked edit would
corrupt every verdict scored in the same workspace afterwards.
"""

from __future__ import annotations

import ast
import difflib
from collections.abc import Sequence
from dataclasses import dataclass

import cosmic_ray.plugins as plugins
from cosmic_ray.mutating import mutate_code

from verdict.mutation.mutant import MutantSpec
from verdict.targets.workspace import TargetWorkspace


@dataclass(frozen=True, slots=True)
class FileVersion:
    """One file, before and after."""

    relative_path: str
    original: str
    changed: str

    @property
    def modified(self) -> bool:
        return self.original != self.changed


@dataclass(frozen=True, slots=True)
class CodeChange:
    """A change under review, ready to be applied to a workspace and reverted."""

    files: tuple[FileVersion, ...]

    module_path: str
    """The file the hypothesis is about. For a multi-file diff, the one with the
    most changed lines - a test has to be written about somewhere."""

    function: str | None
    start_line: int
    """First changed line in ``module_path``, used to select enclosing context."""

    diff: str

    @property
    def primary(self) -> FileVersion:
        return next(f for f in self.files if f.relative_path == self.module_path)

    def apply(self, workspace: TargetWorkspace) -> None:
        for file in self.files:
            (workspace.repo_dir / file.relative_path).write_text(file.changed, encoding="utf-8")

    def revert(self, workspace: TargetWorkspace) -> None:
        for file in self.files:
            (workspace.repo_dir / file.relative_path).write_text(file.original, encoding="utf-8")

    @classmethod
    def from_mutant(
        cls, workspace: TargetWorkspace, spec: MutantSpec, diff: str
    ) -> CodeChange | None:
        """Rebuild a mutant from its recipe. ``None`` if it cannot be replayed."""
        path = workspace.repo_dir / spec.module_path
        original = path.read_text(encoding="utf-8")
        mutated = mutate_code(original, plugins.get_operator(spec.operator)(), spec.occurrence)
        if mutated is None:
            return None
        return cls(
            files=(FileVersion(spec.module_path, original, mutated),),
            module_path=spec.module_path,
            function=spec.function,
            start_line=spec.start_line,
            diff=diff,
        )

    @classmethod
    def from_sources(cls, files: list[FileVersion]) -> CodeChange | None:
        """Build a change from arbitrary before/after pairs, as a real diff is.

        The primary file is the one with the most changed lines: with nothing
        else to go on, that is where the behaviour most likely moved, and the
        Prosecutor needs a single place to point its hypothesis at.
        """
        touched = [f for f in files if f.modified]
        if not touched:
            return None
        primary = max(touched, key=lambda f: _changed_line_count(f))
        line = _first_changed_line(primary)
        return cls(
            files=tuple(touched),
            module_path=primary.relative_path,
            function=_enclosing_name(primary.changed, line),
            start_line=line,
            diff="\n".join(_unified(f) for f in touched),
        )


def _unified(file: FileVersion) -> str:
    return "".join(
        difflib.unified_diff(
            file.original.splitlines(keepends=True),
            file.changed.splitlines(keepends=True),
            fromfile=f"a/{file.relative_path}",
            tofile=f"b/{file.relative_path}",
            n=3,
        )
    )


def _opcodes(file: FileVersion) -> Sequence[tuple[str, int, int, int, int]]:
    return difflib.SequenceMatcher(
        None, file.original.splitlines(), file.changed.splitlines(), autojunk=False
    ).get_opcodes()


def _changed_line_count(file: FileVersion) -> int:
    return sum(
        (end_a - start_a) + (end_b - start_b)
        for tag, start_a, end_a, start_b, end_b in _opcodes(file)
        if tag != "equal"
    )


def _first_changed_line(file: FileVersion) -> int:
    """1-indexed, in the *changed* file, which is what context is read from."""
    for tag, _sa, _ea, start_b, _eb in _opcodes(file):
        if tag != "equal":
            return start_b + 1
    return 1


def _enclosing_name(source: str, line: int) -> str | None:
    """The smallest function or class containing ``line``, by name."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    best: str | None = None
    best_span = len(source.splitlines()) + 1
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        end = getattr(node, "end_lineno", None) or node.lineno
        if node.lineno <= line <= end and (end - node.lineno) < best_span:
            best, best_span = node.name, end - node.lineno
    return best
