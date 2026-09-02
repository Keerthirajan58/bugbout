"""``precedent review`` - the real-diff study, from mining to scoring.

Four commands, run in order, each writing an artifact the next one reads:

``mine``     reconstruct bugfix tasks from repository history
``prepare``  materialise a history-free checkout per task and validate its oracle
``score``    run the oracle and the pre-existing tests against the agent's diff
``report``   print what the study actually measured
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from precedent.artifacts import ArtifactError
from precedent.config import load_settings
from precedent.paths import artifacts_dir, ensure_dirs
from precedent.review.bench import AgentDiff, TaskBench
from precedent.review.store import (
    load_diffs,
    load_ground_truth,
    load_tasks,
    save_diffs,
    save_ground_truth,
    save_tasks,
)
from precedent.review.tasks import GitHubText, IssueTask, mine_tasks
from precedent.targets import get_target
from precedent.targets.workspace import TargetWorkspace

review_app = typer.Typer(
    name="review",
    help="Audit diffs a coding agent wrote against real historical bugs.",
    no_args_is_help=True,
)
out = Console()

# Source layout differs per target, and the mining filter needs the real prefix
# or it silently finds nothing.
_SOURCE_PREFIX = {"arrow": "arrow/"}

# How far back to look. Older commits need older dependencies, and a task whose
# environment cannot be built is a task that has to be thrown away.
_SINCE = {"arrow": "2019-01-01"}


def _clone_for(slug: str) -> Path:
    workspace = TargetWorkspace(get_target(slug))
    if not (workspace.repo_dir / ".git").exists():
        out.print(f"[red]No clone for {slug}.[/red] Run `precedent targets prepare {slug}`.")
        raise typer.Exit(1)
    return workspace.repo_dir


@review_app.command("mine")
def review_mine(
    slugs: Annotated[list[str], typer.Argument(help="Targets to mine, e.g. marshmallow arrow.")],
    per_target: Annotated[
        int, typer.Option("--per-target", help="Cap on tasks taken from each target.")
    ] = 10,
) -> None:
    """Find merged fixes that can be replayed as tasks.

    A fix qualifies only if it shipped a *new* test function. That test, which
    the agent never sees, is the oracle - without one the task cannot be scored,
    so a fix without a test is not a task however interesting it looks.
    """
    ensure_dirs()
    settings = load_settings()
    github = GitHubText(
        settings.require("github_token") if settings.has("github_token") else None,
        artifacts_dir() / "review" / "github",
    )

    tasks: list[IssueTask] = []
    mining: dict[str, object] = {}
    for slug in slugs:
        target = get_target(slug)
        found, report = mine_tasks(
            _clone_for(slug),
            target_slug=slug,
            full_name=target.full_name,
            github=github,
            since=_SINCE.get(slug, "2021-01-01"),
            source_prefix=_SOURCE_PREFIX.get(slug, "src/"),
            limit=per_target,
        )
        tasks.extend(found)
        mining[slug] = {
            "commits_examined": report.examined,
            "fix_shaped_subjects": report.matched_subject,
            "usable": len(found),
            "rejected": report.rejected,
        }
        out.print(
            f"{slug}: {report.examined} commits examined, {report.matched_subject} fix-shaped, "
            f"[bold]{len(found)}[/bold] usable"
        )
        for reason, count in sorted(report.rejected.items(), key=lambda kv: -kv[1]):
            out.print(f"    [dim]dropped {count:3d}  {reason}[/dim]")

    path, digest = save_tasks(tasks, mining=mining)
    out.print(f"\n[bold]{len(tasks)}[/bold] tasks  ->  {path}  sha256 {digest[:16]}...")


@review_app.command("prepare")
def review_prepare(
    force: Annotated[bool, typer.Option("--force", help="Rebuild every checkout.")] = False,
) -> None:
    """Materialise each task and check its oracle fails before the fix exists.

    The checkout is produced with ``git archive``, so it contains no ``.git``
    directory: the agent physically cannot read the fix commit or the
    maintainer's test. Validation then runs the oracle against that untouched
    base - it must FAIL. An oracle that already passes would be satisfied by any
    diff at all, including an empty one.
    """
    tasks = _require_tasks()
    benches: dict[str, TaskBench] = {}
    table = Table(show_header=True)
    for column in ("Task", "Target", "Issue", "Oracle at base", "Workdir"):
        table.add_column(column)

    usable = 0
    for task in tasks:
        bench = benches.setdefault(
            task.target_slug,
            TaskBench(get_target(task.target_slug), _clone_for(task.target_slug)),
        )
        bench.ensure_env()
        if force or not bench.workdir(task).exists():
            bench.materialise(task, force=True)
        ok, detail = bench.validate(task)
        usable += ok
        table.add_row(
            task.task_id,
            task.target_slug,
            f"#{task.reference}",
            "[green]fails (usable)[/green]" if ok else f"[yellow]{detail}[/yellow]",
            str(bench.workdir(task).relative_to(Path.cwd())) if ok else "-",
        )
    out.print(table)
    out.print(f"\n[bold]{usable}/{len(tasks)}[/bold] tasks have a valid oracle")


@review_app.command("score")
def review_score() -> None:
    """Run the oracle and the pre-existing tests against each agent diff."""
    tasks = {t.task_id: t for t in _require_tasks()}
    try:
        diffs = load_diffs()
    except ArtifactError as exc:
        out.print(f"[red]{exc}[/red]  Generate the agent diffs first.")
        raise typer.Exit(1) from exc

    benches: dict[str, TaskBench] = {}
    truths = []
    for diff in diffs:
        task = tasks[diff.task_id]
        bench = benches.setdefault(
            task.target_slug,
            TaskBench(get_target(task.target_slug), _clone_for(task.target_slug)),
        )
        truths.append(bench.score(task, diff))

    path, digest = save_ground_truth(truths)
    resolved = sum(1 for t in truths if t.resolved)
    out.print(
        f"[bold]{resolved}/{len(truths)}[/bold] agent diffs actually resolved their issue\n"
        f"  saved {path}  sha256 {digest[:16]}..."
    )


@review_app.command("report")
def review_report() -> None:
    """Print the study exactly as measured, with no derived claims."""
    tasks = {t.task_id: t for t in _require_tasks()}
    try:
        diffs = {d.task_id: d for d in load_diffs()}
        truths = load_ground_truth()
    except ArtifactError as exc:
        out.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    table = Table(title="agent diffs against real historical bugs", show_header=True)
    for column in ("Task", "Target", "Issue", "Files", "Oracle", "Existing tests", "Resolved"):
        table.add_column(column)
    for truth in truths:
        task, diff = tasks[truth.task_id], diffs[truth.task_id]
        table.add_row(
            truth.task_id,
            task.target_slug,
            f"#{task.reference}",
            str(len(diff.changed_files)),
            truth.oracle,
            truth.regression,
            "[green]yes[/green]" if truth.resolved else "[red]no[/red]",
        )
    out.print(table)
    resolved = sum(1 for t in truths if t.resolved)
    out.print(f"\n  resolved {resolved}/{len(truths)}  |  unresolved {len(truths) - resolved}")


def _require_tasks() -> list[IssueTask]:
    try:
        return load_tasks()
    except ArtifactError as exc:
        out.print(f"[red]{exc}[/red]  Run `precedent review mine` first.")
        raise typer.Exit(1) from exc


__all__ = ["AgentDiff", "review_app", "save_diffs"]
