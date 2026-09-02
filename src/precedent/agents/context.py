"""Assemble the code context a diff is judged against.

A unified diff alone is not enough to reason about. Three changed lines with no
surrounding function tell you nothing about what the function is for, what it
returns, or how a caller would notice. The Prosecutor is given the enclosing
definition so its hypothesis can be about *behaviour* rather than about syntax.

Deliberately excluded here: anything mined from the repository's history. That
is exactly what phase 3 adds, and the whole point of the ablation is that phase
2's context is only what a reader could see in the diff itself.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from precedent.agents.change import CodeChange
from precedent.logging import get_logger

log = get_logger(__name__)

# Enough to show a whole function without flooding a free-tier context window.
MAX_CONTEXT_LINES = 120


@dataclass(frozen=True, slots=True)
class DiffContext:
    """Everything the Prosecutor sees about a change."""

    module_path: str
    function: str | None
    diff: str
    enclosing_source: str
    """Source of the smallest def or class containing the change."""

    public_names: tuple[str, ...]
    """Top-level names the module exports, so the test can import legitimately
    rather than reaching into private internals."""

    def render(self) -> str:
        """Format the context as the user half of the prompt."""
        parts = [
            f"FILE: {self.module_path}",
            f"FUNCTION: {self.function or '(module level)'}",
            "",
            "THE CHANGE UNDER REVIEW (unified diff):",
            "```diff",
            self.diff.strip(),
            "```",
            "",
            "THE ENCLOSING CODE, AS IT IS AFTER THE CHANGE:",
            "```python",
            self.enclosing_source.strip(),
            "```",
        ]
        if self.public_names:
            parts += [
                "",
                f"PUBLIC NAMES IMPORTABLE FROM THIS MODULE: {', '.join(self.public_names)}",
            ]
        return "\n".join(parts)


def _enclosing_definition(source: str, line: int) -> str:
    """The smallest function or class containing ``line``.

    Falls back to a window around the line when the change sits at module level
    or the file cannot be parsed, so context is always produced.
    """
    lines = source.splitlines()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return _window(lines, line)

    best: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | None = None
    best_span = len(lines) + 1
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        start = node.lineno
        end = getattr(node, "end_lineno", None) or start
        if start <= line <= end and (end - start) < best_span:
            best, best_span = node, end - start

    if best is None:
        return _window(lines, line)

    start = best.lineno - 1
    end = getattr(best, "end_lineno", best.lineno)
    snippet = lines[start:end]
    if len(snippet) > MAX_CONTEXT_LINES:
        # Keep the signature and the neighbourhood of the change rather than
        # truncating to the first N lines, which would usually cut the change out.
        relative = line - best.lineno
        head = snippet[:8]
        low = max(8, relative - MAX_CONTEXT_LINES // 2)
        tail = snippet[low : low + MAX_CONTEXT_LINES - 8]
        snippet = [*head, "    # ... elided ...", *tail]
    return "\n".join(snippet)


def _window(lines: list[str], line: int, radius: int = 25) -> str:
    low = max(0, line - radius)
    return "\n".join(lines[low : line + radius])


def _public_names(source: str) -> tuple[str, ...]:
    """Top-level names a test could legitimately import from the module."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ()
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            if not node.name.startswith("_"):
                names.append(node.name)
        elif isinstance(node, ast.Assign):
            names.extend(
                target.id
                for target in node.targets
                if isinstance(target, ast.Name) and not target.id.startswith("_")
            )
    return tuple(names[:40])


def build_context(change: CodeChange) -> DiffContext:
    """Build the Prosecutor's view of one change.

    The enclosing source shown is the code **after** the change, which is what a
    reviewer looking at a pull request would actually be reading. The prompt
    says so explicitly, because a model given only the post-change body under a
    long prompt will otherwise describe it as the original.
    """
    changed_source = change.primary.changed
    return DiffContext(
        module_path=change.module_path,
        function=change.function,
        diff=change.diff,
        enclosing_source=_enclosing_definition(changed_source, change.start_line),
        public_names=_public_names(changed_source),
    )
