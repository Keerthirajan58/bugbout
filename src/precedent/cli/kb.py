"""``precedent kb`` - build and inspect a target's knowledge base."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from precedent.paths import ensure_dirs
from precedent.retrieval.builder import build_knowledge_base, kb_path, mine_documents
from precedent.retrieval.embedding import Embedder
from precedent.retrieval.index import KnowledgeBase
from precedent.retrieval.provider import KnowledgeBaseRetrieval
from precedent.retrieval.risk import rank, score_symbols
from precedent.targets import get_target
from precedent.targets.workspace import TargetWorkspace

kb_app = typer.Typer(
    name="kb",
    help="Knowledge base: mine repository history and search it.",
    no_args_is_help=True,
)
out = Console()


def _workspace(slug: str) -> TargetWorkspace:
    ws = TargetWorkspace(get_target(slug))
    if not ws.is_ready():
        out.print(
            f"[red]Workspace for {slug} is not prepared.[/red] Run `precedent targets prepare {slug}`."
        )
        raise typer.Exit(1)
    return ws


def _load(slug: str) -> KnowledgeBase:
    directory = kb_path(slug)
    if not KnowledgeBase.exists(directory):
        out.print(f"[red]No knowledge base for {slug}.[/red] Run `precedent kb build {slug}`.")
        raise typer.Exit(1)
    return KnowledgeBase.load(directory)


@kb_app.command("build")
def kb_build(
    slug: Annotated[str, typer.Argument(help="Target slug, e.g. 'marshmallow'.")],
) -> None:
    """Mine, chunk, embed and index the repository's own history.

    Everything comes from the local clone, so this costs no API quota and works
    offline. Embeddings run locally too, which keeps the index deterministic.
    """
    ensure_dirs()
    ws = _workspace(slug)
    kb = build_knowledge_base(ws)
    counts: dict[str, int] = {}
    for document in kb.documents:
        counts[document.kind] = counts.get(document.kind, 0) + 1
    table = Table(title=f"knowledge base - {slug}")
    table.add_column("Document kind")
    table.add_column("Count", justify="right")
    for kind, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        table.add_row(kind, str(count))
    table.add_row("[bold]total[/bold]", f"[bold]{len(kb)}[/bold]")
    out.print(table)
    out.print(f"  saved to {kb_path(slug)}")


@kb_app.command("search")
def kb_search(
    slug: Annotated[str, typer.Argument(help="Target slug.")],
    query: Annotated[str, typer.Argument(help="What to look for.")],
    top_k: Annotated[int, typer.Option("--top-k", "-k")] = 6,
    symbol: Annotated[
        str, typer.Option("--symbol", help="Scope to a function or class name.")
    ] = "",
) -> None:
    """Search the knowledge base, showing why each result ranked where it did."""
    kb = _load(slug)
    embedder = Embedder()
    hits = kb.search(
        query,
        embedder.embed_one(query),
        top_k=top_k,
        symbols=(symbol,) if symbol else (),
    )
    if not hits:
        out.print("[yellow]No results.[/yellow]")
        return
    for hit in hits:
        document = hit.document
        out.print(f"\n[bold cyan]{document.cite()}[/bold cyan] {document.title}")
        out.print(f"  score {hit.score:.4f}   [dim]{hit.explain()}[/dim]")
        if document.module_path:
            out.print(f"  file  {document.module_path}")


@kb_app.command("risk")
def kb_risk(
    slug: Annotated[str, typer.Argument(help="Target slug.")],
    limit: Annotated[int, typer.Option("--limit", "-n")] = 15,
) -> None:
    """Rank the repository's most historically fragile symbols.

    Macro retrieval: what a reviewer with a limited budget should look at first.
    """
    kb = _load(slug)
    scores = score_symbols(kb.documents)
    table = Table(title=f"highest-risk symbols - {slug}")
    table.add_column("Symbol")
    table.add_column("Score", justify="right")
    table.add_column("Evidence", overflow="fold")
    for entry in rank(scores, limit):
        table.add_row(entry.key, f"{entry.score:.2f}", entry.explain())
    out.print(table)


@kb_app.command("stats")
def kb_stats(
    slug: Annotated[str, typer.Argument(help="Target slug.")],
) -> None:
    """Show what the knowledge base contains without rebuilding it."""
    ws = _workspace(slug)
    documents = mine_documents(ws)
    counts: dict[str, int] = {}
    for document in documents:
        counts[document.kind] = counts.get(document.kind, 0) + 1
    out.print(f"\n[bold]{slug}[/bold] would index {len(documents)} documents")
    for kind, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        out.print(f"  {kind:<12} {count}")
    with_symbols = sum(1 for d in documents if d.symbols)
    out.print(f"\n  {with_symbols} documents are linked to a specific symbol")


@kb_app.command("context")
def kb_context(
    slug: Annotated[str, typer.Argument(help="Target slug.")],
    mutant_id: Annotated[str, typer.Argument(help="Mutant id from `precedent mutants survivors`.")],
) -> None:
    """Show exactly what the Prosecutor would be given for one diff.

    The transparency command: it makes the difference between the two ablation
    arms concrete rather than asserted.
    """
    from precedent.mutation.store import load_survivors

    survivors = {s.spec.mutant_id: s for s in load_survivors(slug)}
    if mutant_id not in survivors:
        out.print(f"[red]No surviving mutant {mutant_id!r} for {slug}.[/red]")
        raise typer.Exit(1)
    from precedent.agents.change import CodeChange
    from precedent.targets import get_target
    from precedent.targets.workspace import TargetWorkspace

    result = survivors[mutant_id]
    change = CodeChange.from_mutant(TargetWorkspace(get_target(slug)), result.spec, result.diff)
    if change is None:
        out.print("[red]The change could not be reconstructed from its recipe.[/red]")
        raise typer.Exit(1)
    retrieval = KnowledgeBaseRetrieval(_load(slug))
    _, record = retrieval.block_for(change)
    if not record:
        out.print("[yellow]Nothing retrieved for this diff.[/yellow]")
        return
    for item in record:
        out.print(f"\n[bold cyan]{item['citation']}[/bold cyan] {item['title']}")
        out.print(f"  score {item['score']:.4f}   [dim]{item['why']}[/dim]")
        if item.get("url"):
            out.print(f"  {item['url']}")
