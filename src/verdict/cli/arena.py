"""``verdict arena`` - race coding agents on real bugs, cross-examine, and judge.

Every command takes ``--suite``: ``dev`` is where the system is designed and
tuned; ``test`` is held out until the design is frozen. Run in order; each
stage writes an artifact the next one reads:

``mine``             reconstruct tasks from repositories' history (new suites)
``oracles``          derive FAIL_TO_PASS / PASS_TO_PASS from each maintainer fix
``import-recorded``  admit the earlier study's Claude Code patches (dev only)
``race``             coding agents fix every task, concurrently and resumably
``score``            grade every entrant's fix against the hidden oracles
``examine``          blind examiners write reproduction tests, seeing no patch
``cross``            run every exhibit against every fix and the untouched base
``judge``            pick a patch per task from its matrix, or abstain
``report``           the judge's picks beside every baseline
``export``           bundle a suite for the dashboard

This module only parses options and renders results; the stages themselves
live in :mod:`verdict.arena.workflow`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from verdict.arena import markdown
from verdict.arena.judge import POLICIES
from verdict.arena.workflow import ArenaWorkflow
from verdict.targets import get_target

arena_app = typer.Typer(
    name="arena",
    help="Race coding agents on real bugs, cross-examine their patches, and judge by execution.",
    no_args_is_help=True,
)
out = Console()

Suite = Annotated[str, typer.Option("--suite", "-s", help="dev (tuning) or test (held out).")]
Only = Annotated[
    list[str] | None, typer.Option("--task", "-t", help="Task id or issue number; repeatable.")
]
Policy = Annotated[str, typer.Option("--policy", help=f"Judge policy: {', '.join(POLICIES)}.")]


def _workflow(suite: str) -> ArenaWorkflow:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    return ArenaWorkflow(suite)


@arena_app.command("mine")
def mine(
    targets: Annotated[list[str], typer.Argument(help="Registered target slugs.")],
    suite: Suite = "test",
    since: Annotated[str, typer.Option(help="Oldest commit date to consider.")] = "2019-01-01",
    per_target: Annotated[int, typer.Option(help="Cap on tasks per target.")] = 40,
) -> None:
    """Reconstruct bugfix tasks from each target's own history."""
    sources = {slug: (get_target(slug).source_root.split("/")[0] + "/", since) for slug in targets}
    tasks = _workflow(suite).mine(sources, per_target)
    out.print(f"[bold]{len(tasks)}[/bold] tasks mined into suite {suite!r}")


@arena_app.command("oracles")
def oracles(suite: Suite = "dev") -> None:
    """Run each maintainer fix's tests before and after it: FAIL_TO_PASS and PASS_TO_PASS."""
    results = _workflow(suite).build_oracles()
    for task, oracle in results:
        colour = "green" if oracle.valid else "yellow"
        out.print(
            f"  {task.target_slug:12s} #{task.reference:<6} [{colour}]{oracle.note}[/{colour}]"
        )
    usable = sum(o.valid for _, o in results)
    out.print(f"\n[bold]{usable}/{len(results)}[/bold] tasks scorable")


@arena_app.command("import-recorded")
def import_recorded(suite: Suite = "dev") -> None:
    """Admit the earlier study's Claude Code patches, re-measured and round-tripped."""
    out.print(f"recorded patches admitted  ->  {_workflow(suite).import_recorded()}")


@arena_app.command("race")
def race(
    entrants: Annotated[
        list[str], typer.Option("--entrant", "-e", help="Roster name; repeatable.")
    ],
    suite: Suite = "dev",
    only: Only = None,
    timeout: Annotated[int, typer.Option(help="Seconds before an attempt is killed.")] = 1800,
    chatgpt_slots: Annotated[int, typer.Option(help="Concurrent Codex attempts.")] = 2,
    retry_failed: Annotated[
        bool, typer.Option(help="Re-run attempts that failed without producing anything.")
    ] = False,
) -> None:
    """Coding agents fix every selected task. Re-running the same suite resumes it."""
    results = _workflow(suite).race(
        entrants, only=only, timeout=timeout, chatgpt_slots=chatgpt_slots, retry_failed=retry_failed
    )
    for agent, attempts in sorted(results.items()):
        fixed = sum(1 for a in attempts if a.source_files)
        out.print(f"  {agent:32s} {fixed}/{len(attempts)} produced a fix")


@arena_app.command("score")
def score(suite: Suite = "dev") -> None:
    """Grade every entrant's fix against the hidden maintainer tests."""
    workflow = _workflow(suite)
    resolutions = workflow.score()
    tasks = len(workflow.tasks())
    table = Table(title=f"suite {suite!r}: resolved per entrant ({tasks} tasks)")
    for column in ("Entrant", "Resolved", "Fixed the bug", "Broke P2P"):
        table.add_column(column)
    for entrant in sorted({r.entrant for r in resolutions}):
        mine_ = [r for r in resolutions if r.entrant == entrant]
        table.add_row(
            entrant,
            f"{sum(r.resolved for r in mine_)}/{tasks}",
            str(sum(r.fixed_bug for r in mine_)),
            str(sum(r.broke_something for r in mine_)),
        )
    out.print(table)


@arena_app.command("examine")
def examine(
    examiners: Annotated[
        list[str], typer.Option("--examiner", "-x", help="Roster name; repeatable.")
    ],
    suite: Suite = "dev",
    only: Only = None,
) -> None:
    """Blind examiners write reproduction tests from each issue, never seeing a patch."""
    exhibits = _workflow(suite).examine(examiners, only=only)
    written = sum(e.usable for e in exhibits)
    for exhibit in exhibits:
        if not exhibit.usable:
            out.print(
                f"  [yellow]{exhibit.author}  {exhibit.task_id}  {exhibit.error[:100]}[/yellow]"
            )
    out.print(f"\n[bold]{written}/{len(exhibits)}[/bold] exhibits written")


@arena_app.command("cross")
def cross(suite: Suite = "dev", only: Only = None) -> None:
    """Run every exhibit against every fix and the untouched base."""
    for matrix in _workflow(suite).cross(only=only):
        sizes = ", ".join(f"{tree}={len(row)}" for tree, row in matrix.outcomes.items())
        out.print(f"  {matrix.task_id}  {len(matrix.exhibits)} exhibits  {sizes}")


@arena_app.command("judge")
def judge(suite: Suite = "dev", policy: Policy = "v1") -> None:
    """Pick a patch per task from its matrix alone - or abstain."""
    for verdict in _workflow(suite).judge(policy):
        colour = "green" if verdict.chosen else "yellow"
        out.print(
            f"  {verdict.task_id}  [{colour}]{verdict.chosen or 'abstain'}[/{colour}]  {verdict.reason}"
        )


@arena_app.command("report")
def report(suite: Suite = "dev", policy: Policy = "v1") -> None:
    """The judge's picks, scored by the hidden oracles, beside every baseline."""
    board = _workflow(suite).scoreboard(policy)
    table = Table(title=f"suite {suite!r}, policy {policy!r}: {board.tasks} real bugs")
    for column in ("Selector", "Resolved", "Note"):
        table.add_column(column)
    for entrant, stats in sorted(board.per_entrant.items(), key=lambda kv: -kv[1]["resolved"]):
        table.add_row(
            entrant, f"{stats['resolved']}/{board.tasks}", f"broke tests on {stats['broke_tests']}"
        )
    table.add_row("random pick among patches", f"{board.random_pick:.1f}/{board.tasks}", "expected")
    table.add_row(
        "[bold]Verdict[/bold]",
        f"[bold]{board.verdict_resolved}/{board.tasks}[/bold]",
        f"{board.verdict_picks} picks, {board.wrong_picks} wrong, {board.verdict_abstained} "
        f"abstained ({board.abstained_correctly} correctly); precision {board.precision:.0%}",
    )
    table.add_row("best possible (any agent)", f"{board.best_possible}/{board.tasks}", "ceiling")
    out.print(table)


@arena_app.command("analyze")
def analyze(
    suite: Suite = "dev",
    policies: Annotated[
        list[str] | None, typer.Option("--policy", help="Repeatable; default: every policy.")
    ] = None,
    entrants: Annotated[
        list[str] | None,
        typer.Option("--entrant", "-e", help="Restrict the field to these entrants; repeatable."),
    ] = None,
) -> None:
    """Compare judge policies on a suite: selection per task, gating per patch."""
    results = _workflow(suite).analyze(
        policies or list(POLICIES), set(entrants) if entrants else None
    )
    field_ = ", ".join(entrants) if entrants else "every entrant"
    table = Table(title=f"suite {suite!r}, field: {field_}")
    for column in (
        "Policy",
        "Selection",
        "Wrong picks",
        "Abstained",
        "Gate precision",
        "Base rate",
        "Catches",
        "False alarms",
        "Unverified",
    ):
        table.add_column(column)
    for r in results:
        g, b = r.gate, r.board
        table.add_row(
            r.policy,
            f"{b.verdict_resolved}/{b.tasks}",
            str(b.wrong_picks),
            f"{b.verdict_abstained} ({b.abstained_correctly} right)",
            f"{g.precision:.0%} ({g.approved_correct}/{g.approved_correct + g.approved_wrong})",
            f"{g.base_rate:.0%}",
            f"{g.rejected_wrong}/{g.wrong}",
            f"{g.rejected_correct}/{g.correct}",
            str(g.unverified_correct + g.unverified_wrong),
        )
    out.print(table)
    if results:
        board = results[0].board
        best, count = board.best_single
        out.print(
            f"  best single agent: {best} {count}/{board.tasks}   best possible: "
            f"{board.best_possible}/{board.tasks}   random: {board.random_pick:.1f}"
        )


@arena_app.command("evidence")
def evidence(suite: Suite = "dev") -> None:
    """How well each evidence source separates correct from wrong patches (AUC)."""
    table = Table(title=f"suite {suite!r}: signal per evidence source (0.5 = coin flip)")
    for column in ("Source", "AUC", "Correct patches", "Wrong patches"):
        table.add_column(column)
    for signal in _workflow(suite).evidence_signal():
        table.add_row(signal.source, f"{signal.auc:.2f}", str(signal.correct), str(signal.wrong))
    out.print(table)


@arena_app.command("results")
def results(
    sections: Annotated[
        list[str],
        typer.Option(
            "--section",
            help="suite:policy[:entrant,entrant] - one table pair per section; repeatable.",
        ),
    ],
    into: Annotated[
        Path | None, typer.Option(help="Rewrite the generated block in this markdown file.")
    ] = None,
) -> None:
    """Render measured results as markdown - into the README, or to stdout."""
    rendered = []
    for spec in sections:
        suite, policy, *field_ = spec.split(":")
        entrants = set(field_[0].split(",")) if field_ else None
        [result] = _workflow(suite).analyze([policy], entrants)
        title = f"{suite} suite - {result.board.tasks} real bugs"
        if entrants:
            title += f", field: {', '.join(sorted(entrants))}"
        rendered.append((title, result))
    block = markdown.results_block(rendered)
    if into is None:
        out.print(block, markup=False, highlight=False)
        return
    markdown.splice(into, block)
    out.print(f"results block rewritten in {into}")


@arena_app.command("export")
def export(
    suite: Suite = "dev",
    policy: Policy = "v1",
    out_path: Annotated[Path | None, typer.Option("--out", help="Bundle destination.")] = None,
) -> None:
    """Bundle a suite's artifacts into the JSON the dashboard reads."""
    destination = out_path or Path("dashboard/data") / f"arena-{suite}.json"
    path = _workflow(suite).export(destination, policy)
    out.print(f"exported  ->  {path}  ({path.stat().st_size / 1024:.0f} KB)")
