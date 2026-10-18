"""`verdict targets` - manage target repository checkouts."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console

from verdict.paths import ensure_dirs
from verdict.targets import get_target, list_targets
from verdict.targets.workspace import TargetWorkspace, WorkspaceError

targets_app = typer.Typer(
    name="targets", help="Manage target repository checkouts.", no_args_is_help=True
)
out = Console()


@targets_app.command("list")
def targets_list() -> None:
    """Show the registered target repositories and why each was chosen."""
    for target in list_targets():
        ws = TargetWorkspace(target)
        out.print(f"\n[bold cyan]{target.slug}[/bold cyan]  {target.full_name}")
        out.print(f"  pinned    {target.ref} @ {target.short_commit}")
        out.print(f"  license   {target.spdx_license}")
        out.print(f"  source    {target.source_root}   tests: {target.test_root}")
        out.print(f"  prepared  {'yes' if ws.is_ready() else 'no'}")
        out.print(f"  why       {target.selection_rationale}")


@targets_app.command("prepare")
def targets_prepare(
    slug: Annotated[str, typer.Argument(help="Target slug, e.g. 'marshmallow'.")],
    force: Annotated[
        bool, typer.Option("--force", help="Delete and rebuild the workspace.")
    ] = False,
) -> None:
    """Clone a target at its pinned commit and build its isolated virtualenv."""
    ensure_dirs()
    target = get_target(slug)
    ws = TargetWorkspace(target)
    try:
        ws.ensure(force=force)
    except WorkspaceError as exc:
        out.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    out.print(f"[green]Prepared[/green] {target.full_name} @ {target.short_commit}")
    out.print(f"  checkout {ws.repo_dir}")
    out.print(f"  venv     {ws.venv_dir}")


@targets_app.command("verify")
def targets_verify(
    slug: Annotated[str, typer.Argument(help="Target slug, e.g. 'marshmallow'.")],
    timeout: Annotated[int, typer.Option(help="Seconds before the suite is abandoned.")] = 600,
) -> None:
    """Run a target's unmodified suite and require it to be green.

    This is the gate for every later phase: mutation scoring only means
    something if the suite passes before anything is mutated.
    """
    target = get_target(slug)
    ws = TargetWorkspace(target)
    try:
        result = ws.verify_baseline(timeout=timeout)
    except WorkspaceError as exc:
        out.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    out.print(f"[green]Baseline green[/green] for {target.slug}: {result.summary()}")


@targets_app.command("clean")
def targets_clean(
    slug: Annotated[str, typer.Argument(help="Target slug, e.g. 'marshmallow'.")],
) -> None:
    """Discard any working-tree changes in a target checkout."""
    ws = TargetWorkspace(get_target(slug))
    if not ws.is_ready():
        out.print(f"[yellow]Workspace for {slug} is not prepared.[/yellow]")
        raise typer.Exit(1)
    ws.restore_all()
    out.print(f"[green]Restored[/green] {slug} to its pinned state.")
