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
from precedent.eval.harness import EvalHarness
from precedent.llm.factory import (
    GROQ_MIN_INTERVAL_SECONDS,
    GROQ_RETRIEVAL_MIN_INTERVAL_SECONDS,
    NoProvidersConfiguredError,
    build_agent_pool,
)
from precedent.paths import artifacts_dir, ensure_dirs
from precedent.retrieval.builder import kb_path
from precedent.retrieval.index import KnowledgeBase
from precedent.retrieval.provider import KnowledgeBaseRetrieval
from precedent.review.audit import AuditOutcome, audit_task
from precedent.review.bench import AgentDiff, TaskBench
from precedent.review.store import (
    load_diffs,
    load_ground_truth,
    load_tasks,
    save_audit,
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


@review_app.command("collect")
def review_collect(
    agent: Annotated[
        str, typer.Option("--agent", help="Which coding agent produced these.")
    ] = "claude-code",
) -> None:
    """Capture what each agent left in its task checkout.

    The diff is measured against the pristine archive rather than taken from
    the agent's own account of what it did, so an edit it did not mention is
    still audited.
    """
    tasks = _require_tasks()
    benches: dict[str, TaskBench] = {}
    diffs: list[AgentDiff] = []
    for task in tasks:
        bench = benches.setdefault(
            task.target_slug,
            TaskBench(get_target(task.target_slug), _clone_for(task.target_slug)),
        )
        if not bench.workdir(task).exists():
            continue
        patch, changed, touched_tests = bench.diff_of(task)
        diffs.append(
            AgentDiff(
                task_id=task.task_id,
                agent=agent,
                patch=patch,
                changed_files=changed,
                touched_tests=touched_tests,
                note="no change produced" if not patch.strip() else "",
            )
        )

    path, digest = save_diffs(diffs)
    empty = sum(1 for d in diffs if d.empty)
    tests = sum(1 for d in diffs if d.touched_tests)
    out.print(
        f"[bold]{len(diffs)}[/bold] diffs collected  ({empty} empty, {tests} touched tests)\n"
        f"  saved {path}  sha256 {digest[:16]}..."
    )


@review_app.command("audit")
def review_audit(
    retrieval: Annotated[
        bool, typer.Option("--retrieval/--no-retrieval", help="Give the Prosecutor mined history.")
    ] = True,
    review: Annotated[
        bool, typer.Option("--review/--no-review", help="Run the Skeptic's judgement pass.")
    ] = True,
) -> None:
    """Run Precedent's auditor over the agent diffs.

    The same `EvalHarness.deliberate` the mutation ablation calls, so a
    difference in the result cannot come from a difference in the procedure.
    """
    tasks = {t.task_id: t for t in _require_tasks()}
    try:
        diffs = load_diffs()
    except ArtifactError as exc:
        out.print(f"[red]{exc}[/red]  Run `precedent review collect` first.")
        raise typer.Exit(1) from exc

    settings = load_settings()
    try:
        pool = build_agent_pool(
            settings,
            review=review,
            min_interval=(
                GROQ_RETRIEVAL_MIN_INTERVAL_SECONDS if retrieval else GROQ_MIN_INTERVAL_SECONDS
            ),
            allow_fallback=False,
        )
    except NoProvidersConfiguredError as exc:
        out.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    out.print(f"Model lanes: {', '.join(pool.models)}")

    benches: dict[str, TaskBench] = {}
    retrievers: dict[str, KnowledgeBaseRetrieval | None] = {}
    outcomes: list[AuditOutcome] = []

    for index, diff in enumerate(diffs):
        task = tasks[diff.task_id]
        bench = benches.setdefault(
            task.target_slug,
            TaskBench(get_target(task.target_slug), _clone_for(task.target_slug)),
        )
        if task.target_slug not in retrievers:
            provider = None
            if retrieval:
                directory = kb_path(task.target_slug)
                if KnowledgeBase.exists(directory):
                    provider = KnowledgeBaseRetrieval(
                        KnowledgeBase.load(directory), top_k=settings.retrieval_top_k
                    )
                else:
                    out.print(
                        f"[yellow]No knowledge base for {task.target_slug}; "
                        f"its tasks are audited without retrieval.[/yellow]"
                    )
            retrievers[task.target_slug] = provider

        prosecutor, skeptic = pool.pairs[index % len(pool.pairs)]
        harness = EvalHarness(
            prosecutor, skeptic, retrieval=retrievers[task.target_slug], pool=pool
        )
        outcome = audit_task(task, diff, bench, harness, prosecutor, skeptic)
        outcomes.append(outcome)
        colour = "red" if outcome.flagged else "dim"
        out.print(
            f"  [{colour}]{outcome.task_id}  {task.target_slug:12s} #{task.reference:<6} "
            f"{outcome.verdict}[/{colour}]"
        )

    flagged = sum(1 for o in outcomes if o.flagged)
    path, digest = save_audit(
        {
            "arm": "with_retrieval" if retrieval else "no_retrieval",
            "model": " + ".join(pool.models),
            "flagged": flagged,
            "total": len(outcomes),
            "outcomes": [o.to_dict() for o in outcomes],
        }
    )
    out.print(
        f"\n[bold]{flagged}/{len(outcomes)}[/bold] diffs flagged as regressions\n"
        f"  saved {path}  sha256 {digest[:16]}..."
    )


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
