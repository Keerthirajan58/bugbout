"""``precedent`` command line interface."""

from __future__ import annotations

import shutil
import sys
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from precedent import __version__
from precedent.config import load_settings
from precedent.logging import configure_logging
from precedent.paths import data_dir, ensure_dirs, project_root
from precedent.targets import get_target, list_targets
from precedent.targets.workspace import TargetWorkspace, WorkspaceError

app = typer.Typer(
    name="precedent",
    help="An adversarial, retrieval-grounded code verification agent.",
    no_args_is_help=True,
    add_completion=False,
)
targets_app = typer.Typer(
    name="targets", help="Manage target repository checkouts.", no_args_is_help=True
)
app.add_typer(targets_app)

out = Console()


@app.callback()
def main(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Enable debug logging.")] = False,
) -> None:
    """Configure logging before any subcommand runs."""
    configure_logging(verbose=verbose)


@app.command()
def version() -> None:
    """Print the installed version."""
    out.print(f"precedent {__version__}")


@app.command()
def doctor() -> None:
    """Check that the environment can run each phase, and say what is missing."""
    settings = load_settings()
    table = Table(title="precedent doctor", show_lines=False)
    table.add_column("Check", style="bold")
    table.add_column("Status")
    table.add_column("Detail", overflow="fold")

    def row(name: str, ok: bool, detail: str, *, optional: bool = False) -> None:
        if ok:
            status = "[green]ok[/green]"
        else:
            status = "[yellow]missing[/yellow]" if optional else "[red]FAIL[/red]"
        table.add_row(name, status, detail)

    py = sys.version_info
    row(
        "python >= 3.12",
        py >= (3, 12),
        f"{py.major}.{py.minor}.{py.micro}",
    )
    row("git", shutil.which("git") is not None, shutil.which("git") or "not on PATH")
    row(
        "uv (fast installs)",
        shutil.which("uv") is not None,
        shutil.which("uv") or "optional - falls back to python -m venv",
        optional=True,
    )
    row(
        "docker (CI sandbox)",
        shutil.which("docker") is not None,
        shutil.which("docker") or "optional - the subprocess sandbox is the local default",
        optional=True,
    )
    row("data dir", data_dir().exists(), str(data_dir()))

    # Credentials are per-phase, so name the phase each one unblocks.
    for field, phase in (
        ("gemini_api_key", "phase 2 (agents)"),
        ("groq_api_key", "phase 2 (agent fallback)"),
        ("github_token", "phase 3 (corpus mining)"),
        ("database_url", "phase 3 (vector store)"),
    ):
        present = settings.has(field)
        row(
            f"PRECEDENT_{field.upper()}",
            present,
            "set" if present else f"not set - needed for {phase}",
            optional=True,
        )

    out.print(table)
    for target in list_targets():
        ws = TargetWorkspace(target)
        state = "[green]prepared[/green]" if ws.is_ready() else "[yellow]not prepared[/yellow]"
        out.print(f"  target {target.slug:<14} {state}  ({target.full_name} @ {target.ref})")


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


@app.command()
def info() -> None:
    """Show resolved paths for this installation."""
    out.print(f"version      {__version__}")
    out.print(f"project root {project_root()}")
    out.print(f"data dir     {data_dir()}")


if __name__ == "__main__":  # pragma: no cover
    app()
