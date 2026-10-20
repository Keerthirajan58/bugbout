"""A report as Markdown: readable in a terminal, a pull request or a wiki."""

from __future__ import annotations

from bugbout.report.model import Report
from bugbout.report.text import auc, duration, gate_summary, share


def render_markdown(report: Report) -> str:
    sections = [
        _header(report),
        _agents(report),
        _selection(report),
        _gate(report),
        _evidence(report),
        _tasks(report),
        _mining(report),
        f"<sub>bugbout {report.version}, {report.generated}</sub>\n",
    ]
    return "\n".join(section for section in sections if section)


def _header(report: Report) -> str:
    return (
        f"# bugbout: {report.project}\n\n"
        f"{report.tasks_scorable} real bugs from the project's own history, each scored by "
        "the tests the maintainers wrote with the fix. No agent ever saw those tests.\n"
    )


def _agents(report: Report) -> str:
    if not report.agents:
        return "## Agents\n\nNo contestant has run yet.\n"
    rows = [
        f"| {a.name} | {share(a.resolved, a.tasks)} | {a.fixed_but_broke} | {a.wrong} | "
        f"{a.no_fix} | {a.failed_runs} | {duration(a.median_seconds)} |"
        for a in report.agents
    ]
    return "\n".join(
        [
            "## Agents\n",
            "| Agent | Resolved | Fixed but broke a test | Wrong fix | No fix | Failed runs "
            "| Median time |",
            "|---|---|---|---|---|---|---|",
            *rows,
            "",
        ]
    )


def _selection(report: Report) -> str:
    s = report.selection
    if not s.tasks or not report.agents:
        return ""
    best = (
        f"{s.best_agent}, {share(s.best_agent_resolved, s.tasks)}"
        if s.best_agent
        else "none resolved a bug"
    )
    return (
        f"## Picking one patch per bug (judge policy: {report.policy})\n\n"
        f"- Best single agent: {best}\n"
        f"- Any agent at all: {share(s.any_agent_resolved, s.tasks)} - the most any choice "
        "could reach\n"
        f"- Picking at random: {s.random_pick:g} of {s.tasks} expected\n"
        f"- The judge: {s.judge_resolved} resolved from {s.judge_picks} picks; abstained on "
        f"{s.judge_abstained}, {s.abstained_correctly} of them where no agent was right\n"
    )


def _gate(report: Report) -> str:
    g = report.gate
    if not g.correct + g.wrong:
        return ""
    return (
        "## Every patch through a merge gate\n\n"
        "| | Correct patch | Wrong patch |\n"
        "|---|---|---|\n"
        f"| Approved | {g.approved_correct} | {g.approved_wrong} |\n"
        f"| Rejected | {g.rejected_correct} | {g.rejected_wrong} |\n"
        f"| Unverified | {g.unverified_correct} | {g.unverified_wrong} |\n\n"
        f"{_capitalised(gate_summary(g))}.\n"
    )


def _evidence(report: Report) -> str:
    if not report.evidence:
        return ""
    rows = [
        f"| {e.source} | {e.kind} | {auc(e.auc)} | {e.correct} / {e.wrong} |"
        for e in report.evidence
    ]
    return "\n".join(
        [
            "## Which tests tell a right fix from a wrong one\n",
            "AUC is the chance that a correct patch scores above a wrong one on that source's "
            "tests: 0.50 is a coin flip, 1.00 a perfect separation.\n",
            "| Source | Kind | AUC | Correct / wrong patches |",
            "|---|---|---|---|",
            *rows,
            "",
        ]
    )


def _tasks(report: Report) -> str:
    if not report.tasks:
        return ""
    rows = []
    for t in report.tasks:
        pick = (
            "abstained" if t.chosen is None else f"{t.chosen} {'✓' if t.chosen_resolved else '✗'}"
        )
        resolved = ", ".join(t.resolved_by) or "-"
        rows.append(f"| `{t.task_id}` | #{t.reference} {_cell(t.title)} | {resolved} | {pick} |")
    return "\n".join(
        [
            "## Tasks\n",
            "| Task | Issue | Resolved by | Judge's pick |",
            "|---|---|---|---|",
            *rows,
            "",
        ]
    )


def _mining(report: Report) -> str:
    mining = report.mining
    if not mining:
        return ""
    dropped = "\n".join(
        f"- {reason}: {count}"
        for reason, count in sorted(mining.get("rejected", {}).items(), key=lambda kv: -kv[1])
    )
    return (
        "## How the tasks were found\n\n"
        f"{mining.get('examined', 0)} changes on the mainline were examined and "
        f"{mining.get('fix_shaped', 0)} looked like fixes; {report.tasks_mined} became tasks, "
        f"and {report.tasks_scorable} have tests that prove the bug.\n"
        + (f"\nFixes left out, and why:\n\n{dropped}\n" if dropped else "")
    )


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _capitalised(text: str) -> str:
    return text[:1].upper() + text[1:]
