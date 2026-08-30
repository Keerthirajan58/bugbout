"""``precedent`` command line interface."""

from __future__ import annotations

import shutil
import sys
from typing import Annotated

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from precedent import __version__
from precedent.artifacts import ArtifactError, is_frozen
from precedent.config import load_settings
from precedent.logging import configure_logging
from precedent.mutation.generate import generate_mutants, summarize
from precedent.mutation.mutant import MutantResult
from precedent.mutation.operators import DEFAULT_OPERATORS
from precedent.mutation.runner import cleanup_workers, prepare_workers, run_parallel, stats_from
from precedent.mutation.store import load_run, load_survivors, run_path, save_run
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

mutants_app = typer.Typer(
    name="mutants",
    help="Mutation testing: generate mutants and measure what the suite catches.",
    no_args_is_help=True,
)
app.add_typer(mutants_app)

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


@mutants_app.command("generate")
def mutants_generate(
    slug: Annotated[str, typer.Argument(help="Target slug, e.g. 'marshmallow'.")],
) -> None:
    """Enumerate the mutant population without running anything.

    Pure static analysis, so this is fast and safe to run repeatedly - useful
    for sizing a run before committing to it.
    """
    target = get_target(slug)
    ws = TargetWorkspace(target)
    if not ws.is_ready():
        out.print(
            f"[red]Workspace for {slug} is not prepared.[/red] Run `precedent targets prepare {slug}`."
        )
        raise typer.Exit(1)

    mutants = generate_mutants(ws)
    out.print(
        f"\n[bold]{len(mutants)}[/bold] mutants for {target.full_name} @ {target.short_commit}"
    )
    out.print(
        f"  from {len(DEFAULT_OPERATORS)} operators across {len(ws.source_files())} source files\n"
    )
    table = Table(show_header=True)
    table.add_column("Operator family")
    table.add_column("Mutants", justify="right")
    for family, count in summarize(mutants).items():
        table.add_row(family, str(count))
    out.print(table)


@mutants_app.command("run")
def mutants_run(
    slug: Annotated[str, typer.Argument(help="Target slug, e.g. 'marshmallow'.")],
    workers: Annotated[
        int, typer.Option("--workers", "-w", help="Parallel checkouts to score with.")
    ] = 4,
    limit: Annotated[
        int, typer.Option("--limit", help="Score only the first N mutants (0 = all).")
    ] = 0,
    timeout: Annotated[int, typer.Option(help="Per-mutant test timeout in seconds.")] = 120,
    freeze: Annotated[
        bool, typer.Option("--freeze", help="Write a hash sidecar and protect the artifact.")
    ] = False,
    allow_refreeze: Annotated[
        bool, typer.Option("--allow-refreeze", help="Deliberately replace a frozen artifact.")
    ] = False,
) -> None:
    """Score every mutant against the target's suite and save the results.

    This produces the ground truth: which mutants the existing test suite
    catches, and - more importantly - which ones slip through. The survivors
    are the corpus every later phase is measured on.
    """
    ensure_dirs()
    target = get_target(slug)
    ws = TargetWorkspace(target)
    if not ws.is_ready():
        out.print(
            f"[red]Workspace for {slug} is not prepared.[/red] Run `precedent targets prepare {slug}`."
        )
        raise typer.Exit(1)

    destination = run_path(slug)
    if is_frozen(destination) and not allow_refreeze:
        out.print(
            f"[red]{destination} is frozen.[/red] Re-running would overwrite evidence "
            f"for a published comparison. Pass --allow-refreeze to do it deliberately."
        )
        raise typer.Exit(1)

    # The suite must be green before anything is mutated, or a failing test
    # tells us nothing about the mutation.
    try:
        ws.verify_baseline()
    except WorkspaceError as exc:
        out.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    mutants = generate_mutants(ws)
    if limit:
        mutants = mutants[:limit]

    pool = prepare_workers(ws, workers)
    out.print(
        f"Scoring [bold]{len(mutants)}[/bold] mutants across {len(pool)} worker checkout(s)\n"
    )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=out,
    ) as progress:
        task = progress.add_task("scoring mutants", total=len(mutants))
        state = {"survived": 0}

        def on_result(done: int, total: int, result: MutantResult) -> None:  # noqa: ARG001
            if result.survived:
                state["survived"] += 1
            progress.update(
                task, completed=done, description=f"scoring ({state['survived']} survivors)"
            )

        results = run_parallel(pool, mutants, timeout=timeout, on_result=on_result)

    stats = stats_from(results)
    path, digest = save_run(
        target, results, DEFAULT_OPERATORS, freeze=freeze, allow_refreeze=allow_refreeze
    )

    out.print(f"\n[bold]{target.full_name} @ {target.short_commit}[/bold]")
    out.print(f"  {stats.summary()}")
    out.print(f"  saved   {path}")
    out.print(f"  sha256  {digest[:16]}...{' [frozen]' if freeze else ''}")
    if stats.survived:
        out.print(
            f"\n[yellow]{stats.survived} surviving mutants[/yellow] - each is a change that passes the entire suite and is still wrong."
        )


@mutants_app.command("survivors")
def mutants_survivors(
    slug: Annotated[str, typer.Argument(help="Target slug, e.g. 'marshmallow'.")],
    show: Annotated[int, typer.Option("--show", help="How many to list.")] = 15,
) -> None:
    """List surviving mutants from the saved run."""
    try:
        survivors = load_survivors(slug)
    except ArtifactError as exc:
        out.print(f"[red]{exc}[/red]  Run `precedent mutants run {slug}` first.")
        raise typer.Exit(1) from exc

    out.print(f"\n[bold]{len(survivors)}[/bold] surviving mutants for {slug}\n")
    table = Table(show_header=True)
    table.add_column("id")
    table.add_column("operator")
    table.add_column("location", overflow="fold")
    table.add_column("function")
    for survivor in survivors[:show]:
        spec = survivor.spec
        table.add_row(
            spec.mutant_id,
            spec.operator.split("/")[-1],
            f"{spec.module_path}:{spec.start_line}",
            spec.function or "-",
        )
    out.print(table)
    if len(survivors) > show:
        out.print(f"  ... and {len(survivors) - show} more")


@mutants_app.command("report")
def mutants_report(
    slug: Annotated[str, typer.Argument(help="Target slug, e.g. 'marshmallow'.")],
) -> None:
    """Show the saved mutation run summary for a target."""
    try:
        payload = load_run(slug)
    except ArtifactError as exc:
        out.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    stats = payload["stats"]
    target = payload["target"]
    out.print(f"\n[bold]{target['full_name']}[/bold] @ {target['commit'][:12]} ({target['ref']})")
    out.print(
        f"  operators   {payload['operators']['count']} (set hash {payload['operators']['hash']})"
    )
    out.print(f"  frozen      {'yes' if is_frozen(run_path(slug)) else 'no'}\n")
    table = Table(show_header=True)
    table.add_column("Outcome")
    table.add_column("Count", justify="right")
    for key in ("killed", "killed_import", "survived", "timeout", "incompetent", "no_mutation"):
        if key not in stats:
            continue
        table.add_row(key, str(stats[key]))
    table.add_row("[bold]scored[/bold]", f"[bold]{stats['scored']}[/bold]")
    out.print(table)
    out.print(f"\n  [bold]kill-rate {stats['kill_rate']:.1%}[/bold]  detected / scored")
    if "test_kill_rate" in stats:
        out.print(
            f"  test-only kill-rate {stats['test_kill_rate']:.1%}  (excludes mutants caught at import)"
        )


@mutants_app.command("clean-workers")
def mutants_clean_workers(
    slug: Annotated[str, typer.Argument(help="Target slug, e.g. 'marshmallow'.")],
) -> None:
    """Delete parallel worker checkouts. They are fully reproducible."""
    cleanup_workers(slug)
    out.print(f"[green]Removed[/green] worker checkouts for {slug}.")


@app.command()
def info() -> None:
    """Show resolved paths for this installation."""
    out.print(f"version      {__version__}")
    out.print(f"project root {project_root()}")
    out.print(f"data dir     {data_dir()}")


if __name__ == "__main__":  # pragma: no cover
    app()
