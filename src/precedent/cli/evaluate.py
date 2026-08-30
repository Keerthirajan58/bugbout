"""``precedent eval`` and ``precedent verify`` - run the agent pair and score it."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.syntax import Syntax
from rich.table import Table

from precedent.agents.prosecutor import Prosecutor
from precedent.agents.skeptic import Skeptic
from precedent.artifacts import ArtifactError, is_frozen
from precedent.config import load_settings
from precedent.eval.case import CaseOutcome
from precedent.eval.harness import EvalHarness, stats_from
from precedent.eval.store import (
    ARM_NO_RETRIEVAL,
    ARM_WITH_RETRIEVAL,
    eval_path,
    journal_path,
    load_eval,
    save_eval,
)
from precedent.llm.factory import (
    NoProvidersConfiguredError,
    build_agent_pool,
    build_router,
)
from precedent.mutation.runner import prepare_workers
from precedent.mutation.store import load_survivors, run_path
from precedent.paths import ensure_dirs
from precedent.retrieval.builder import kb_path
from precedent.retrieval.index import KnowledgeBase
from precedent.retrieval.provider import KnowledgeBaseRetrieval
from precedent.targets import get_target
from precedent.targets.workspace import TargetWorkspace

eval_app = typer.Typer(
    name="eval",
    help="Run the Prosecutor/Skeptic pair over the surviving-mutant corpus.",
    no_args_is_help=True,
)
out = Console()

_ARMS = (ARM_NO_RETRIEVAL, ARM_WITH_RETRIEVAL)


def _require_workspace(slug: str) -> TargetWorkspace:
    ws = TargetWorkspace(get_target(slug))
    if not ws.is_ready():
        out.print(
            f"[red]Workspace for {slug} is not prepared.[/red] Run `precedent targets prepare {slug}`."
        )
        raise typer.Exit(1)
    return ws


def _build_agents(review: bool = True) -> tuple[Prosecutor, Skeptic]:
    settings = load_settings()
    try:
        router = build_router(settings)
    except NoProvidersConfiguredError as exc:
        out.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    prosecutor = Prosecutor(
        router,
        temperature=settings.llm_temperature,
        max_output_tokens=settings.llm_max_output_tokens,
    )
    skeptic = Skeptic(router, review_enabled=review)
    return prosecutor, skeptic


def verify(
    slug: Annotated[str, typer.Argument(help="Target slug, e.g. 'marshmallow'.")],
    mutant_id: Annotated[str, typer.Argument(help="Mutant id from `precedent mutants survivors`.")],
    review: Annotated[
        bool, typer.Option("--review/--no-review", help="Run the Skeptic's judgement pass.")
    ] = True,
) -> None:
    """Audit a single diff and show the full debate.

    The demo path: one diff in, one verdict out, with the Prosecutor's
    hypothesis, the test it wrote, and what happened when that test was executed
    against both versions of the code.
    """
    ws = _require_workspace(slug)
    survivors = {s.spec.mutant_id: s for s in load_survivors(slug)}
    if mutant_id not in survivors:
        out.print(f"[red]No surviving mutant {mutant_id!r} for {slug}.[/red]")
        raise typer.Exit(1)

    result = survivors[mutant_id]
    prosecutor, skeptic = _build_agents(review)
    harness = EvalHarness(prosecutor, skeptic)

    out.print(
        Panel(
            Syntax(result.diff.strip(), "diff", theme="ansi_dark"), title="the change under review"
        )
    )
    with out.status("Prosecutor is forming a hypothesis..."):
        outcome = harness.run_one(ws, result)

    if outcome.hypothesis:
        out.print(
            Panel(f"{outcome.hypothesis}\n\n[dim]{outcome.reasoning}[/dim]", title="Prosecutor")
        )
    if outcome.test_code:
        out.print(
            Panel(
                Syntax(outcome.test_code.strip(), "python", theme="ansi_dark"),
                title="candidate test",
            )
        )

    colour = "green" if outcome.caught else "yellow"
    body = outcome.reason
    if outcome.review_reason:
        body += f"\n\n[dim]Skeptic review: {outcome.review_reason}[/dim]"
    out.print(
        Panel(body, title=f"[{colour}]{outcome.verdict.upper()}[/{colour}]", border_style=colour)
    )


@eval_app.command("run")
def eval_run(
    slug: Annotated[str, typer.Argument(help="Target slug, e.g. 'marshmallow'.")],
    arm: Annotated[
        str, typer.Option("--arm", help=f"Which arm to run: {' | '.join(_ARMS)}.")
    ] = ARM_NO_RETRIEVAL,
    workers: Annotated[int, typer.Option("--workers", "-w", help="Parallel checkouts.")] = 3,
    limit: Annotated[
        int, typer.Option("--limit", help="Evaluate only the first N survivors (0 = all).")
    ] = 0,
    review: Annotated[
        bool, typer.Option("--review/--no-review", help="Run the Skeptic's judgement pass.")
    ] = True,
    freeze: Annotated[
        bool, typer.Option("--freeze", help="Protect the artifact from being overwritten.")
    ] = False,
    allow_refreeze: Annotated[
        bool, typer.Option("--allow-refreeze", help="Deliberately replace a frozen artifact.")
    ] = False,
    resume: Annotated[
        bool, typer.Option("--resume/--restart", help="Continue from the journal if one exists.")
    ] = True,
) -> None:
    """Run the agent pair across every surviving mutant and score the arm.

    `--arm no_retrieval` is the phase 2 control. Freeze it once it is real: the
    ablation cannot be recomputed later if the baseline is overwritten.
    """
    if arm not in _ARMS:
        out.print(f"[red]Unknown arm {arm!r}.[/red] Valid arms: {', '.join(_ARMS)}")
        raise typer.Exit(1)

    ensure_dirs()
    ws = _require_workspace(slug)
    target = ws.target

    destination = eval_path(slug, arm)
    if is_frozen(destination) and not allow_refreeze:
        out.print(
            f"[red]{destination.name} is frozen.[/red] It is the evidence behind a published "
            f"comparison; re-running would invalidate it. Pass --allow-refreeze to do it deliberately."
        )
        raise typer.Exit(1)

    try:
        survivors = load_survivors(slug)
    except ArtifactError as exc:
        out.print(f"[red]{exc}[/red]  Run `precedent mutants run {slug}` first.")
        raise typer.Exit(1) from exc
    if limit:
        survivors = survivors[:limit]

    try:
        pool = build_agent_pool(load_settings(), review=review)
    except NoProvidersConfiguredError as exc:
        out.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    out.print(f"Model lanes: {', '.join(pool.models)}")

    retrieval = None
    if arm == ARM_WITH_RETRIEVAL:
        directory = kb_path(slug)
        if not KnowledgeBase.exists(directory):
            out.print(
                f"[red]No knowledge base for {slug}.[/red] Run `precedent kb build {slug}` first."
            )
            raise typer.Exit(1)
        top_k = load_settings().retrieval_top_k
        retrieval = KnowledgeBaseRetrieval(KnowledgeBase.load(directory), top_k=top_k)
        out.print(f"Retrieval enabled: {len(retrieval.kb)} documents, top-{top_k}")

    harness = EvalHarness(*pool.pairs[0], retrieval=retrieval, pool=pool)
    journal = journal_path(slug, arm)
    if not resume:
        journal.unlink(missing_ok=True)

    checkouts = prepare_workers(ws, workers)
    out.print(
        f"Evaluating [bold]{len(survivors)}[/bold] survivors on arm [bold]{arm}[/bold] across {len(checkouts)} checkout(s)\n"
    )

    caught = {"n": 0}
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=out,
    ) as progress:
        task = progress.add_task("auditing diffs", total=len(survivors))

        def on_result(outcome: CaseOutcome) -> None:
            if outcome.caught:
                caught["n"] += 1
            progress.advance(task)
            progress.update(task, description=f"auditing ({caught['n']} confirmed)")

        outcomes = harness.run(checkouts, survivors, journal=journal, on_result=on_result)

    stats = stats_from(outcomes)
    corpus_hash = _corpus_hash(slug)
    path, digest = save_eval(
        target,
        arm,
        outcomes,
        mutation_run_hash=corpus_hash,
        model=" + ".join(pool.models),
        freeze=freeze,
        allow_refreeze=allow_refreeze,
    )
    out.print(f"\n[bold]{target.full_name}[/bold] arm={arm}")
    out.print(f"  {stats.summary()}")
    out.print(f"  lanes:\n    {pool.combined_stats()}")
    out.print(f"  saved   {path}")
    out.print(f"  sha256  {digest[:16]}...{'  [frozen]' if freeze else ''}")
    if not freeze and arm == ARM_NO_RETRIEVAL:
        out.print(
            "\n[yellow]This is the no-retrieval baseline.[/yellow] Re-run with --freeze once you are "
            "satisfied: phase 3 cannot recompute the ablation if it is overwritten."
        )


@eval_app.command("report")
def eval_report(
    slug: Annotated[str, typer.Argument(help="Target slug.")],
    arm: Annotated[str, typer.Option("--arm")] = ARM_NO_RETRIEVAL,
) -> None:
    """Show a saved evaluation run."""
    try:
        payload = load_eval(slug, arm)
    except ArtifactError as exc:
        out.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    stats = payload["stats"]
    out.print(
        f"\n[bold]{payload['target']['full_name']}[/bold] arm={payload['arm']} model={payload['model']}"
    )
    out.print(f"  corpus  {payload['corpus']['size']} survivors")
    out.print(f"  frozen  {'yes' if is_frozen(eval_path(slug, arm)) else 'no'}\n")
    table = Table(show_header=True)
    table.add_column("Verdict")
    table.add_column("Count", justify="right")
    for verdict, count in sorted(stats["by_verdict"].items(), key=lambda kv: -kv[1]):
        table.add_row(verdict, str(count))
    out.print(table)
    out.print(
        f"\n  [bold]catch-rate {stats['catch_rate']:.1%}[/bold]  ({stats['caught']}/{stats['total']})"
    )


@eval_app.command("ablation")
def eval_ablation(
    slug: Annotated[str, typer.Argument(help="Target slug.")],
) -> None:
    """Compare the two arms. This is the number the project exists to report."""
    try:
        without = load_eval(slug, ARM_NO_RETRIEVAL)
        with_ = load_eval(slug, ARM_WITH_RETRIEVAL)
    except ArtifactError as exc:
        out.print(f"[red]{exc}[/red]  Both arms must have been run.")
        raise typer.Exit(1) from exc

    a, b = without["stats"], with_["stats"]
    if without["corpus"]["mutation_run_hash"] != with_["corpus"]["mutation_run_hash"]:
        # Comparing arms scored against different survivor sets is meaningless.
        out.print(
            "[red]The two arms were scored against different corpora; the delta would be meaningless.[/red]"
        )
        raise typer.Exit(1)

    table = Table(title=f"ablation - {slug}", show_header=True)
    table.add_column("Arm")
    table.add_column("Caught", justify="right")
    table.add_column("Corpus", justify="right")
    table.add_column("Catch-rate", justify="right")
    table.add_row("without retrieval", str(a["caught"]), str(a["total"]), f"{a['catch_rate']:.1%}")
    table.add_row("with retrieval", str(b["caught"]), str(b["total"]), f"{b['catch_rate']:.1%}")
    out.print(table)
    delta = b["catch_rate"] - a["catch_rate"]
    out.print(f"\n  [bold]delta {delta:+.1%}[/bold]  ({b['caught'] - a['caught']:+d} mutants)")


@eval_app.command("export")
def eval_export(
    destination: Annotated[
        str, typer.Option("--out", help="Where to write the dashboard payload.")
    ] = "dashboard/data/results.json",
) -> None:
    """Write the static JSON the dashboard reads.

    The deployed page needs no API keys, no database and no pipeline run - every
    case record already carries the diff, the retrieved context, the debate and
    the verdict.
    """
    from precedent.eval.export import export

    path, count = export(Path(destination))
    size = path.stat().st_size / 1024
    out.print(f"[green]Exported[/green] {count} cases to {path} ({size:.0f} KB)")


def _corpus_hash(slug: str) -> str:
    """Hash of the mutation run this evaluation scored, so the two arms can be
    proven to have run against the same corpus."""
    from precedent.artifacts import content_hash, read_artifact

    return content_hash(read_artifact(run_path(slug)))[:16]
