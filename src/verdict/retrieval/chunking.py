"""AST-aware code chunking.

Chunks are cut at **function and class boundaries**, never at a fixed character
or line count. The difference is not cosmetic. A fixed-size window splits a
function in half, so a chunk can contain a loop body with no signature, no
docstring and no return statement - it retrieves as a bag of keywords that
describes nothing. An AST chunk is always a complete, nameable unit of
behaviour, which means:

* it can be **labelled** with its own qualified name, so retrieval can be scoped
  to the symbol a diff actually touches;
* it embeds as a coherent idea rather than as a fragment;
* what the Prosecutor is shown is always readable code, never a severed body.

Methods are chunked individually but keep their class in their qualified name,
so ``Length.__call__`` is distinguishable from ``Range.__call__``.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from verdict.logging import get_logger
from verdict.retrieval.documents import Document
from verdict.targets.workspace import TargetWorkspace

log = get_logger(__name__)

# A class whose body is only methods adds nothing beyond its own chunks, but a
# very long function is still one unit and is kept whole rather than split.
MAX_CHUNK_LINES = 200


@dataclass(frozen=True, slots=True)
class CodeChunk:
    """One complete, nameable unit of source."""

    module_path: str
    qualified_name: str
    kind: str
    start_line: int
    end_line: int
    source: str
    docstring: str | None

    @property
    def symbol(self) -> str:
        """The bare name, which is what a diff's ``function`` field carries."""
        return self.qualified_name.rsplit(".", 1)[-1]


def chunk_module(source: str, module_path: str) -> list[CodeChunk]:
    """Split one module into AST-bounded chunks."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        log.warning("Skipping %s: unparseable", module_path)
        return []

    lines = source.splitlines()
    chunks: list[CodeChunk] = []

    def emit(node: ast.AST, qualified: str, kind: str) -> None:
        start = getattr(node, "lineno", 1)
        end = getattr(node, "end_lineno", start) or start
        if end - start > MAX_CHUNK_LINES:
            end = start + MAX_CHUNK_LINES
        chunks.append(
            CodeChunk(
                module_path=module_path,
                qualified_name=qualified,
                kind=kind,
                start_line=start,
                end_line=end,
                source="\n".join(lines[start - 1 : end]),
                docstring=ast.get_docstring(node) if isinstance(node, _DOCSTRING_NODES) else None,
            )
        )

    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            emit(node, node.name, "function")
        elif isinstance(node, ast.ClassDef):
            methods = [
                child
                for child in node.body
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
            ]
            # The class itself is worth a chunk only for what is not in its
            # methods: the docstring and class-level attributes.
            if ast.get_docstring(node) or len(methods) != len(node.body):
                emit(node, node.name, "class")
            for method in methods:
                emit(method, f"{node.name}.{method.name}", "method")
    return chunks


_DOCSTRING_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)


def chunk_workspace(workspace: TargetWorkspace) -> list[Document]:
    """AST-chunk every source file in a target into retrievable documents."""
    slug = workspace.target.slug
    documents: list[Document] = []
    for path in workspace.source_files():
        module_path = workspace.relative(path)
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for chunk in chunk_module(source, module_path):
            summary = chunk.docstring.strip().splitlines()[0] if chunk.docstring else ""
            documents.append(
                Document(
                    doc_id=Document.make_id("code_chunk", slug, module_path, chunk.qualified_name),
                    kind="code_chunk",
                    target_slug=slug,
                    title=f"{module_path}:{chunk.qualified_name}",
                    body="\n".join(p for p in (summary, chunk.source) if p),
                    module_path=module_path,
                    symbols=(chunk.symbol, chunk.qualified_name),
                    metadata={
                        "qualified_name": chunk.qualified_name,
                        "chunk_kind": chunk.kind,
                        "start_line": chunk.start_line,
                        "end_line": chunk.end_line,
                    },
                )
            )
    log.info("Chunked %s into %d AST chunks", slug, len(documents))
    return documents
