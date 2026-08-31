"""``precedent demo`` - curated examples that run offline."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from precedent.demo import curate

demo_app = typer.Typer(
    name="demo",
    help="Pre-cached examples. No API key, no network, no pipeline run.",
    no_args_is_help=True,
)
out = Console()


@demo_app.command("list")
def demo_list(
    slug: Annotated[str, typer.Argument(help="Target slug.")] = "marshmallow",
) -> None:
    """List the curated examples."""
    cases = curate(slug)
    if not cases:
        out.print(f"[yellow]No evaluation artifacts for {slug} yet.[/yellow]")
        raise typer.Exit(1)
    table = Table(title=f"precedent demo - {slug}", show_lines=False)
    table.add_column("#", justify="right")
    table.add_column("slot")
    table.add_column("what it shows", overflow="fold")
    table.add_column("verdict")
    for index, case in enumerate(cases, start=1):
        colour = "green" if case.case["caught"] else "yellow"
        table.add_row(
            str(index),
            case.slot,
            case.headline,
            f"[{colour}]{case.case['verdict']}[/{colour}]",
        )
    out.print(table)
    out.print(f"\n  precedent demo show {slug} 1    [dim]# walk one example[/dim]")


@demo_app.command("show")
def demo_show(
    slug: Annotated[str, typer.Argument(help="Target slug.")] = "marshmallow",
    number: Annotated[int, typer.Argument(help="Example number from `demo list`.")] = 1,
) -> None:
    """Walk one example end to end: diff, evidence, debate, verdict."""
    cases = curate(slug)
    if not cases:
        out.print(f"[yellow]No evaluation artifacts for {slug} yet.[/yellow]")
        raise typer.Exit(1)
    if not 1 <= number <= len(cases):
        out.print(f"[red]Pick a number between 1 and {len(cases)}.[/red]")
        raise typer.Exit(1)

    demo = cases[number - 1]
    case = demo.case
    out.print(f"\n[bold]{demo.headline}[/bold]")
    out.print(f"[dim]{demo.why_it_matters}[/dim]\n")
    out.print(
        f"  {demo.location}   mutant [cyan]{demo.mutant_id}[/cyan]   "
        f"{case['operator']}   arm: {case['arm']}"
    )

    if case.get("diff"):
        out.print(
            Panel(
                Syntax(case["diff"].strip(), "diff", theme="ansi_dark"),
                title="the change under review",
            )
        )

    retrieved = case.get("retrieved") or []
    if retrieved:
        lines = []
        cited = str(case.get("citation") or "")
        for item in retrieved:
            mark = "->" if cited and item["doc_id"] in cited else "  "
            lines.append(f"{mark} [{item['kind']}] {item['title'][:70]}")
            lines.append(f"     {item['why']}")
        out.print(Panel("\n".join(lines), title=f"retrieved evidence ({len(retrieved)} items)"))
        if case.get("used_evidence"):
            out.print(f"  [dim]Prosecutor's reading: {case['used_evidence'][:200]}[/dim]")

    if case.get("hypothesis"):
        out.print(Panel(case["hypothesis"], title="Prosecutor's hypothesis"))
    if case.get("test_code"):
        out.print(
            Panel(
                Syntax(case["test_code"].strip(), "python", theme="ansi_dark"),
                title="the test it wrote",
            )
        )

    colour = "green" if case["caught"] else "yellow"
    body = case.get("reason", "")
    if case.get("review_reason"):
        body += f"\n\n[dim]Skeptic review: {case['review_reason']}[/dim]"
    out.print(
        Panel(
            body,
            title=f"[{colour}]{case['verdict'].upper()}[/{colour}]",
            border_style=colour,
        )
    )
