"""``precedent`` command line interface."""

from __future__ import annotations

import shutil
import sys
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from precedent import __version__
from precedent.cli.demo import demo_app
from precedent.cli.evaluate import eval_app, verify
from precedent.cli.kb import kb_app
from precedent.cli.mutants import mutants_app
from precedent.cli.review import review_app
from precedent.cli.targets import targets_app
from precedent.config import load_settings
from precedent.logging import configure_logging
from precedent.paths import artifacts_dir, data_dir, project_root
from precedent.targets import list_targets
from precedent.targets.workspace import TargetWorkspace

app = typer.Typer(
    name="precedent",
    help="An adversarial, retrieval-grounded code verification agent.",
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(targets_app)
app.add_typer(mutants_app)
app.add_typer(kb_app)
app.add_typer(eval_app)
app.add_typer(review_app)
app.add_typer(demo_app)
app.command()(verify)

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
    row("python >= 3.12", py >= (3, 12), f"{py.major}.{py.minor}.{py.micro}")
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


@app.command()
def info() -> None:
    """Show resolved paths for this installation."""
    out.print(f"version      {__version__}")
    out.print(f"project root {project_root()}")
    out.print(f"data dir     {data_dir()}")
    out.print(f"artifacts    {artifacts_dir()}")


if __name__ == "__main__":  # pragma: no cover
    app()
