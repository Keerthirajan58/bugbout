"""Render measured results as markdown, for the README and the write-ups.

Every number a reader sees is produced here from artifacts - never typed. The
README keeps a block between two markers, and ``verdict arena results --into``
rewrites only that block, so prose and numbers can never drift apart.
"""

from __future__ import annotations

from pathlib import Path

from verdict.arena.analysis import PolicyResult

BEGIN = "<!-- results:begin -->"
END = "<!-- results:end -->"


def selection_table(result: PolicyResult) -> str:
    board = result.board
    rows = [
        "| Selector | Resolved |",
        "|---|---|",
        *(
            f"| {name} alone | {stats['resolved']}/{board.tasks} |"
            for name, stats in sorted(board.per_entrant.items(), key=lambda kv: -kv[1]["resolved"])
        ),
        f"| random pick among the patches | {board.random_pick:.1f}/{board.tasks} |",
        f"| **Verdict** (policy `{result.policy}`) | **{board.verdict_resolved}/{board.tasks}** |",
        f"| best possible (any agent solved it) | {board.best_possible}/{board.tasks} |",
    ]
    picks = (
        f"Verdict picked a patch on {board.verdict_picks} tasks ({board.wrong_picks} wrong) and "
        f"abstained on {board.verdict_abstained} ({board.abstained_correctly} of them tasks no "
        f"agent solved)."
    )
    return "\n".join(rows) + "\n\n" + picks


def gate_table(result: PolicyResult) -> str:
    g = result.gate
    approved = g.approved_correct + g.approved_wrong
    rejected = g.rejected_correct + g.rejected_wrong
    unverified = g.unverified_correct + g.unverified_wrong
    total = g.correct + g.wrong
    return "\n".join(
        [
            "| Patches | Correct | Wrong |",
            "|---|---|---|",
            f"| approved ({approved}) | {g.approved_correct} | {g.approved_wrong} |",
            f"| rejected ({rejected}) | {g.rejected_correct} | {g.rejected_wrong} |",
            f"| unverified ({unverified}) | {g.unverified_correct} | {g.unverified_wrong} |",
            f"| **all** ({total}) | {g.correct} | {g.wrong} |",
            "",
            f"Precision of an approval: **{g.precision:.0%}**, against {g.base_rate:.0%} for "
            f"merging every patch an agent reported done. Wrong patches caught: "
            f"**{g.rejected_wrong}/{g.wrong}**. Correct patches rejected: "
            f"{g.rejected_correct}/{g.correct}.",
        ]
    )


def results_block(sections: list[tuple[str, PolicyResult]]) -> str:
    parts = [BEGIN, ""]
    for title, result in sections:
        parts += [
            f"### {title}",
            "",
            "**Choosing a patch** - several agents fixed the same issue; which one ships?",
            "",
            selection_table(result),
            "",
            "**Gating a patch** - an agent says it is done; may it merge?",
            "",
            gate_table(result),
            "",
        ]
    return "\n".join([*parts, END])


def splice(document: Path, block: str) -> None:
    """Replace the generated block in ``document``, leaving every other line alone."""
    text = document.read_text(encoding="utf-8")
    if BEGIN not in text or END not in text:
        raise ValueError(f"{document} has no {BEGIN} ... {END} block to fill")
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    document.write_text(head + block + tail, encoding="utf-8")
