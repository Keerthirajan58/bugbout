"""Build a call-site index for a target.

Where a function is called from is context a diff cannot supply on its own. If
``set_value`` is only ever reached through ``Schema.load``, then a change to it
is a change to deserialisation, and a test should go through that path rather
than poking the helper directly. Callers also reveal the argument shapes a
function is actually given, which is what makes a generated test realistic
instead of hypothetical.

Resolution is deliberately name-based rather than a full type analysis. Python's
dynamism makes exact call resolution undecidable in general, and a partial,
honestly-scoped index is far more useful here than an elaborate one that is
still wrong. Same-named methods on different classes are recorded together; the
retrieved snippet shows the caller's own code, so a reader can see which is
which.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass

from verdict.logging import get_logger
from verdict.retrieval.documents import Document
from verdict.targets.workspace import TargetWorkspace

log = get_logger(__name__)

MAX_SITES_PER_SYMBOL = 12


@dataclass(frozen=True, slots=True)
class CallSite:
    """One place a name is called."""

    callee: str
    caller_module: str
    caller_function: str | None
    line: int
    snippet: str


def _enclosing_function_map(tree: ast.Module) -> dict[int, str]:
    """Map every line in the module to the qualified function containing it."""
    mapping: dict[int, str] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                qualified = f"{prefix}{child.name}"
                start = child.lineno
                end = getattr(child, "end_lineno", start) or start
                for line in range(start, end + 1):
                    # Inner definitions overwrite outer ones, so the most
                    # specific enclosing function wins.
                    mapping[line] = qualified
                visit(child, f"{qualified}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return mapping


def _called_name(node: ast.Call) -> str | None:
    """The bare name being called, for ``f()``, ``obj.f()`` and ``a.b.f()``."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def collect_call_sites(workspace: TargetWorkspace) -> dict[str, list[CallSite]]:
    """Every intra-repository call, grouped by the name being called."""
    by_callee: dict[str, list[CallSite]] = defaultdict(list)
    for path in workspace.source_files():
        module_path = workspace.relative(path)
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):
            continue
        lines = source.splitlines()
        enclosing = _enclosing_function_map(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = _called_name(node)
            if callee is None or callee.startswith("__"):
                continue
            line = node.lineno
            low = max(0, line - 2)
            by_callee[callee].append(
                CallSite(
                    callee=callee,
                    caller_module=module_path,
                    caller_function=enclosing.get(line),
                    line=line,
                    snippet="\n".join(lines[low : line + 1]),
                )
            )
    return dict(by_callee)


def mine_call_sites(workspace: TargetWorkspace) -> list[Document]:
    """Turn the call-site index into one document per called symbol.

    Only symbols the repository actually defines are indexed. Calls to stdlib
    and third-party names are real calls but say nothing about *this* codebase.
    """
    slug = workspace.target.slug
    defined: set[str] = set()
    for path in workspace.source_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        defined |= {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        }

    documents: list[Document] = []
    for callee, sites in collect_call_sites(workspace).items():
        if callee not in defined:
            continue
        # A symbol called from exactly one place, itself, is not useful context.
        external = [s for s in sites if s.caller_function != callee]
        if not external:
            continue
        shown = external[:MAX_SITES_PER_SYMBOL]
        body_parts = [
            f"{site.caller_module}:{site.line} in {site.caller_function or '(module level)'}\n"
            f"```python\n{site.snippet}\n```"
            for site in shown
        ]
        summary = f"{callee} is called from {len(external)} place(s) in this repository"
        if len(external) > len(shown):
            summary += f" ({len(shown)} shown)"
        documents.append(
            Document(
                doc_id=Document.make_id("call_site", slug, callee),
                kind="call_site",
                target_slug=slug,
                title=summary,
                body="\n\n".join(body_parts),
                module_path=shown[0].caller_module,
                symbols=(callee,),
                metadata={
                    "callee": callee,
                    "call_count": len(external),
                    "caller_modules": sorted({s.caller_module for s in external}),
                },
            )
        )
    log.info("Indexed call sites for %d symbols in %s", len(documents), slug)
    return documents
