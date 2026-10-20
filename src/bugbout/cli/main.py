"""The ``bugbout`` command line: a thin layer over :class:`bugbout.Bout`."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from bugbout.__about__ import __version__
from bugbout.bout import Bout
from bugbout.config import CONFIG_FILE
from bugbout.doctor import diagnose
from bugbout.errors import BugboutError
from bugbout.log import configure_logging, console
from bugbout.project import Project
from bugbout.report import Report, render_markdown, render_terminal
from bugbout.scaffold import detect_layout, installed_agents, render_config

app = typer.Typer(
    name="bugbout",
    help="Find out which coding agent actually fixes bugs in your codebase.",
    no_args_is_help=True,
    add_completion=False,
)

ConfigOption = Annotated[
    Path, typer.Option("--config", "-c", help=f"A {CONFIG_FILE}, or the directory holding it.")
]


@app.callback()
def main(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show debug output.")] = False,
) -> None:
    configure_logging(verbose)


@contextmanager
def _reported() -> Iterator[None]:
    """Turn bugbout's own errors into one line, and an interrupt into a resumable stop."""
    try:
        yield
    except BugboutError as exc:
        console.print(f"[red]error:[/] {exc}")
        raise typer.Exit(1) from exc
    except KeyboardInterrupt as exc:
        console.print("\nInterrupted. Finished work is saved; run the same command to resume.")
        raise typer.Exit(130) from exc


@app.command()
def init(
    path: Annotated[Path, typer.Argument(help="The repository to set up.")] = Path(),
    source: Annotated[str | None, typer.Option(help="The package directory.")] = None,
    tests: Annotated[str | None, typer.Option(help="The test directory.")] = None,
    force: Annotated[bool, typer.Option(help=f"Overwrite an existing {CONFIG_FILE}.")] = False,
) -> None:
    """Write a bugbout.toml for a repository, from its layout and installed agents."""
    target = path / CONFIG_FILE
    if target.exists() and not force:
        console.print(f"[red]error:[/] {target} exists; pass --force to overwrite it")
        raise typer.Exit(1)
    layout = detect_layout(path)
    source, tests = source or layout.source, tests or layout.tests or "tests"
    if source is None:
        console.print("[red]error:[/] could not find the package; pass --source src/yourpkg")
        raise typer.Exit(1)
    agents = installed_agents()
    layout = dataclasses.replace(layout, source=source, tests=tests)
    target.write_text(render_config(layout, agents), encoding="utf-8")
    console.print(f"Wrote {target} for [bold]{source}[/], tested by [bold]{tests}[/].")
    if agents:
        console.print(f"Contestants: {', '.join(agents)}.")
    else:
        console.print("No agent CLI was found on PATH; add [[contestants]] entries by hand.")
    console.print("Next: `bugbout doctor`, then `bugbout run`.")


@app.command()
def doctor(config: ConfigOption = Path()) -> None:
    """Check everything a bout needs before any agent runs."""
    with _reported():
        checks = diagnose(Project.load(config))
    table = Table(show_header=False, box=None)
    for _ in range(3):
        table.add_column(overflow="fold")
    for check in checks:
        mark = "[green]✓[/]" if check.ok else "[red]✗[/]" if check.required else "[yellow]![/]"
        table.add_row(mark, check.name, check.detail)
    console.print(table)
    if any(not check.ok and check.required for check in checks):
        raise typer.Exit(1)


@app.command()
def tasks(
    config: ConfigOption = Path(),
    refresh: Annotated[bool, typer.Option(help="Mine the history again.")] = False,
) -> None:
    """Mine the project's history for fixed bugs, and establish their ground truth."""
    with _reported():
        bout = Bout.load(config)
        found = bout.mine(refresh=refresh)
        oracles = bout.ground_truth()
    table = Table("Task", "Issue", "Title", "Proof", title=f"{len(found)} tasks")
    for task in found:
        oracle = oracles[task.task_id]
        proof = f"{len(oracle.fail_to_pass)} failing test(s)" if oracle.valid else "[red]none[/]"
        table.add_row(task.task_id, f"#{task.reference}", task.title, proof)
    console.print(table)


@app.command()
def run(
    config: ConfigOption = Path(),
    retry_failed: Annotated[
        bool, typer.Option(help="Run again attempts that failed without producing anything.")
    ] = False,
) -> None:
    """Run the bout: race, examine, judge and score, resuming wherever it stopped."""
    with _reported():
        bout = Bout.load(config)
        result = bout.run(retry_failed=retry_failed)
        _save(bout, result)
    render_terminal(result, console)
    console.print(f"Full report: {bout.store.root / 'report.md'}")


@app.command()
def report(
    config: ConfigOption = Path(),
    output_format: Annotated[
        str, typer.Option("--format", "-f", help="terminal, markdown or json.")
    ] = "terminal",
) -> None:
    """Show the results of the last run, without running anything."""
    renderers: dict[str, Callable[[Report], None]] = {
        "terminal": lambda r: render_terminal(r, console),
        "markdown": lambda r: typer.echo(render_markdown(r)),
        "json": lambda r: typer.echo(json.dumps(r.to_dict(), indent=2)),
    }
    if output_format not in renderers:
        console.print(
            f"[red]error:[/] unknown format {output_format!r}; use terminal, markdown or json"
        )
        raise typer.Exit(1)
    with _reported():
        result = Bout.load(config).report()
    renderers[output_format](result)


@app.command()
def version() -> None:
    """Print bugbout's version."""
    typer.echo(f"bugbout {__version__}")


def _save(bout: Bout, result: Report) -> None:
    (bout.store.root / "report.md").write_text(render_markdown(result), encoding="utf-8")
    (bout.store.root / "report.json").write_text(
        json.dumps(result.to_dict(), indent=2) + "\n", encoding="utf-8"
    )
