"""A report's headline numbers, as tables in the terminal."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from bugbout.report.model import Report
from bugbout.report.text import duration, gate_summary, share


def render_terminal(report: Report, console: Console) -> None:
    console.print(
        f"[bold]{report.project}[/]: {report.tasks_scorable} real bugs, scored by the "
        "maintainers' own tests"
    )
    if not report.agents:
        console.print("No contestant has run yet.")
        return
    agents = Table(title="Agents", title_justify="left")
    agents.add_column("Agent", overflow="fold")
    for column in ("Resolved", "Broke a test", "Wrong fix", "No fix", "Median time"):
        agents.add_column(column, justify="right")
    for a in report.agents:
        agents.add_row(
            a.name,
            share(a.resolved, a.tasks),
            str(a.fixed_but_broke),
            str(a.wrong),
            str(a.no_fix),
            duration(a.median_seconds),
        )
    console.print(agents)

    s, g = report.selection, report.gate
    console.print(
        f"Judge ({report.policy}): {s.judge_resolved} resolved from {s.judge_picks} picks, "
        f"abstained on {s.judge_abstained}; best single agent {s.best_agent_resolved}, "
        f"any agent {s.any_agent_resolved}, of {s.tasks}."
    )
    if g.correct + g.wrong:
        console.print(f"As a merge gate: {gate_summary(g)}.")
