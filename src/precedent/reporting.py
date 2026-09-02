"""Generate the results write-up from artifacts, never from recollection.

Every number in the output of this module was read out of a file that a run
produced. Nothing is typed by hand, nothing is carried over from a previous
version of the document, and a measurement that has not been taken prints as
"not yet measured" rather than as a plausible placeholder.

That rule exists because the first version of this project got it wrong in
exactly the available way: targets written down before measurement, then
reported as though they had been measured.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from precedent.artifacts import ArtifactError
from precedent.eval.store import ARM_NO_RETRIEVAL, ARM_WITH_RETRIEVAL, load_eval
from precedent.mutation.store import load_run
from precedent.review.store import load_audit, load_diffs, load_ground_truth, load_tasks

MISSING = "_not yet measured_"


def _table(headers: Iterable[str], rows: Iterable[Iterable[Any]]) -> str:
    head = list(headers)
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join(["---"] * len(head)) + "|"]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def _gate_total(cases: list[dict[str, Any]]) -> str:
    """Gate rejections, or "-" when the run predates the counter.

    Reporting a bare 0 for an older artifact would read as "no candidate was
    ever wrong about the library", which is the opposite of what those runs
    showed.
    """
    if not any("gate_failures" in c for c in cases):
        return "-"
    return str(sum(c.get("gate_failures", 0) for c in cases))


def _pct(numerator: int, denominator: int) -> str:
    return f"{numerator / denominator:.1%}" if denominator else "-"


# --- the mutation ablation ---------------------------------------------------


def ablation_section(slug: str) -> str:
    """Both arms, the paired subset, and the grounding gate's own numbers."""
    try:
        control = load_eval(slug, ARM_NO_RETRIEVAL)
        treated = load_eval(slug, ARM_WITH_RETRIEVAL)
    except ArtifactError:
        return f"### Mutation ablation - {slug}\n\n{MISSING}: one or both arms have not been run.\n"

    if control["corpus"]["mutation_run_hash"] != treated["corpus"]["mutation_run_hash"]:
        return (
            f"### Mutation ablation - {slug}\n\n"
            "**Invalid**: the two arms were scored against different corpora, so the "
            "delta would be meaningless.\n"
        )

    parts = [f"### Mutation ablation - {slug}", ""]
    parts.append(
        _table(
            ["Arm", "Corpus", "Caught", "Catch-rate", "Gate rejections", "Wrong-about-library"],
            [
                [
                    label,
                    payload["stats"]["total"],
                    payload["stats"]["caught"],
                    f"{payload['stats']['catch_rate']:.1%}",
                    _gate_total(payload["cases"]),
                    payload["stats"]["by_verdict"].get("rejected_invalid_on_original", 0),
                ]
                for label, payload in (
                    ("without retrieval", control),
                    ("with retrieval", treated),
                )
            ],
        )
    )

    delta = treated["stats"]["catch_rate"] - control["stats"]["catch_rate"]
    parts += ["", f"**Whole-corpus delta: {delta:+.1%}**", ""]

    # The paired subset is the honest comparison: lane rotation means a mutant
    # can meet different models in the two arms, and a model swap must not be
    # allowed to masquerade as an effect of retrieval.
    prior = {c["mutant_id"]: c for c in control["cases"]}
    paired = [
        c
        for c in treated["cases"]
        if (p := prior.get(c["mutant_id"])) is not None and p.get("model") == c.get("model")
    ]
    if paired:
        before = sum(1 for c in paired if prior[c["mutant_id"]]["caught"])
        after = sum(1 for c in paired if c["caught"])
        gate_before = _gate_total([prior[c["mutant_id"]] for c in paired])
        gate_after = _gate_total(paired)
        parts += [
            f"Same-model paired subset ({len(paired)} of {len(treated['cases'])} mutants, "
            "where model choice cannot contribute to the difference):",
            "",
            _table(
                ["Arm", "Caught", "Catch-rate", "Candidates rejected at the gate"],
                [
                    [
                        "without retrieval",
                        f"{before}/{len(paired)}",
                        _pct(before, len(paired)),
                        gate_before,
                    ],
                    [
                        "with retrieval",
                        f"{after}/{len(paired)}",
                        _pct(after, len(paired)),
                        gate_after,
                    ],
                ],
            ),
            "",
            f"**Paired delta: {(after - before) / len(paired):+.1%}** "
            f"({after - before:+d} mutants)",
            "",
        ]

        gained = [c for c in paired if c["caught"] and not prior[c["mutant_id"]]["caught"]]
        lost = [c for c in paired if prior[c["mutant_id"]]["caught"] and not c["caught"]]
        parts.append(
            f"Per mutant: {len(gained)} caught only with retrieval, {len(lost)} lost to it."
        )
        parts.append("")

    return "\n".join(parts)


def corpus_section(slugs: Iterable[str]) -> str:
    """The mutation ground truth every agent number is scored against."""
    rows = []
    for slug in slugs:
        try:
            run = load_run(slug)
        except ArtifactError:
            continue
        stats = run["stats"]
        rows.append(
            [
                slug,
                stats["total"],
                stats["killed"],
                f"{stats['kill_rate']:.1%}",
                stats["survived"],
            ]
        )
    if not rows:
        return f"### Mutation corpus\n\n{MISSING}\n"
    total = sum(int(r[1]) for r in rows)
    survivors = sum(int(r[4]) for r in rows)
    return "\n".join(
        [
            "### Mutation corpus",
            "",
            _table(["Target", "Mutants", "Killed by the suite", "Kill-rate", "Survivors"], rows),
            "",
            f"**{total} mutants, {survivors} survivors.** Survivors are the corpus the agent "
            "pair is scored against: changes a mature library's own test suite does not catch.",
            "",
        ]
    )


# --- the real-diff study -----------------------------------------------------


def real_diff_section() -> str:
    """Agent-written diffs against real historical bugs."""
    try:
        tasks = {t.task_id: t for t in load_tasks()}
    except ArtifactError:
        return f"### Real agent-written diffs\n\n{MISSING}: no tasks have been mined.\n"

    valid = [t for t in tasks.values() if t.oracle_valid]
    unvalidated = [t for t in tasks.values() if t.oracle_valid is None]
    excluded = [t for t in tasks.values() if t.oracle_valid is False]
    parts = [
        "### Real agent-written diffs",
        "",
        f"{len(tasks)} tasks reconstructed from merged fixes. "
        + (
            f"{len(valid)} have a validated oracle - a test the maintainer added that "
            f"provably FAILS at the base commit. {len(excluded)} were excluded because their "
            "oracle could not be made to fail there (a dependency or collection problem in an "
            "older checkout), and are reported here rather than dropped from the denominator."
            if not unvalidated
            else f"{MISSING}: oracles have not been validated."
        ),
        "",
    ]

    try:
        diffs = {d.task_id: d for d in load_diffs()}
    except ArtifactError:
        parts.append(f"{MISSING}: no agent diffs have been collected.")
        return "\n".join(parts)

    produced = [d for d in diffs.values() if not d.empty]
    parts += [
        f"{len(produced)} of {len(diffs)} tasks produced a diff "
        f"({len(diffs) - len(produced)} left the code unchanged).",
        "",
    ]

    try:
        truths = {g.task_id: g for g in load_ground_truth()}
    except ArtifactError:
        parts.append(f"{MISSING}: agent diffs have not been scored against their oracles.")
        return "\n".join(parts)

    scored = [t for t in truths.values() if t.oracle != "absent"]
    resolved = [t for t in scored if t.resolved]
    parts += [
        _table(
            ["Outcome", "Count"],
            [
                ["diffs scored", len(scored)],
                ["resolved the issue (oracle passes, suite green)", len(resolved)],
                [
                    "did not fix the bug (oracle fails)",
                    sum(1 for t in scored if t.oracle != "passed"),
                ],
                [
                    "broke something that worked (pre-existing tests fail)",
                    sum(1 for t in scored if t.regression != "passed"),
                ],
            ],
        ),
        "",
    ]

    try:
        audit = load_audit()
    except ArtifactError:
        parts.append(f"{MISSING}: Precedent has not audited these diffs.")
        return "\n".join(parts)

    outcomes = {o["task_id"]: o for o in audit["outcomes"]}
    # Precedent proves regressions - a test green before the diff and red after.
    # It is therefore scored against the regression half of the ground truth,
    # not against "did the agent fix the bug", which it never claims to answer.
    judged = [
        (outcomes[t], truths[t])
        for t in outcomes
        if t in truths and outcomes[t]["verdict"] != "excluded" and truths[t].oracle != "absent"
    ]
    broke = [(o, g) for o, g in judged if g.regression != "passed"]
    clean = [(o, g) for o, g in judged if g.regression == "passed"]
    caught = sum(1 for o, _ in broke if o["flagged"])
    false_alarms = sum(1 for o, _ in clean if o["flagged"])

    parts += [
        f"Precedent's auditor, arm `{audit.get('arm', '?')}`, over {len(judged)} scored diffs:",
        "",
        _table(
            ["The diff", "Count", "Precedent flagged a regression"],
            [
                ["broke a pre-existing test", len(broke), f"{caught}/{len(broke)}"],
                ["left the suite green", len(clean), f"{false_alarms}/{len(clean)}"],
            ],
        ),
        "",
        f"Recall on diffs that really did break something: **{_pct(caught, len(broke))}**. "
        f"False-alarm rate on diffs that did not: **{_pct(false_alarms, len(clean))}**.",
        "",
    ]
    return "\n".join(parts)


def build_report(
    slug: str = "marshmallow",
    corpus_slugs: Iterable[str] = (),
    *,
    standalone: bool = True,
) -> str:
    """The whole results document, assembled from artifacts alone.

    ``standalone`` adds the title and preamble. It is off when the block is
    spliced into a report that already has both.
    """
    preamble = (
        [
            "# Precedent - measured results",
            "",
            "Every number below was read from an artifact a run produced. Anything not "
            "yet measured says so.",
            "",
        ]
        if standalone
        else []
    )
    return "\n".join(
        [
            *preamble,
            corpus_section(corpus_slugs or ("marshmallow", "arrow", "attrs")),
            ablation_section(slug),
            real_diff_section(),
        ]
    )


BEGIN = "<!-- results:begin -->"
END = "<!-- results:end -->"


def splice_into(document: Path, generated: str) -> None:
    """Replace the generated block inside a hand-written report.

    The narrative around it is written by a person; everything between the
    markers is regenerated from artifacts every time. That split is the point:
    prose can be argued with, but no number in the file was ever typed.
    """
    text = document.read_text(encoding="utf-8")
    if BEGIN not in text or END not in text:
        raise ValueError(f"{document} has no {BEGIN} ... {END} block to fill")
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    document.write_text(f"{head}{BEGIN}\n\n{generated.strip()}\n\n{END}{tail}", encoding="utf-8")
