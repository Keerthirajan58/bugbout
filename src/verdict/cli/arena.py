"""``verdict arena`` - race coding agents on real bugs and score what they produce.

Run in order; each command writes an artifact the next one reads:

``oracles``          derive FAIL_TO_PASS / PASS_TO_PASS from each maintainer fix
``import-recorded``  admit the earlier study's Claude Code patches as an entrant
``race``             run coding agents on every task, concurrently and resumably
``score``            grade every entrant's patches against the hidden oracles
``examine``          blind examiners write reproduction tests from each issue alone
``cross``            run every exhibit against every fix and the untouched base
``judge``            pick a patch per task from its matrix, or abstain
``report``           score the judge's picks beside every baseline
``export``           bundle everything the dashboard shows into one JSON file
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from contextlib import nullcontext
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from verdict.arena import recorded, scoreboard
from verdict.arena.contestants import (
    CodexContestant,
    Contestant,
    GeminiContestant,
    MiniSweAgentContestant,
    OllamaServer,
)
from verdict.arena.examiners import Examiner, Exhibit, source_context
from verdict.arena.export import build_bundle
from verdict.arena.judge import judge
from verdict.arena.matrix import CrossExaminer, Matrix, fixes_only, self_exhibits
from verdict.arena.patch import Patch
from verdict.arena.race import Race
from verdict.arena.store import (
    arena_dir,
    list_entrants,
    load_entries,
    load_exhibits,
    load_matrices,
    load_oracles,
    load_resolutions,
    load_verdicts,
    save_entries,
    save_exhibits,
    save_matrices,
    save_oracles,
    save_resolutions,
    save_verdicts,
)
from verdict.arena.truth import Referee, Resolution
from verdict.artifacts import ArtifactError, payload_of, read_artifact
from verdict.config import load_settings
from verdict.llm.providers import GeminiProvider, GroqProvider, OllamaProvider
from verdict.paths import verdict_home
from verdict.review.bench import TaskBench
from verdict.review.store import load_diffs, load_tasks
from verdict.review.tasks import IssueTask
from verdict.targets import get_target
from verdict.targets.workspace import TargetWorkspace

arena_app = typer.Typer(
    name="arena",
    help="Race coding agents on real historical bugs and score their patches.",
    no_args_is_help=True,
)
out = Console()

# The contestants this project can drive, by the short name the CLI accepts.
ENTRANTS = ("codex", "codex-mini", "oss", "codex-oss", "gemini")


def contestant(name: str) -> Contestant:
    if name == "codex":
        return CodexContestant("gpt-6.1-sol")
    if name == "codex-mini":
        return CodexContestant("gpt-5.6-luna")
    if name == "oss":
        return MiniSweAgentContestant("gpt-oss:20b")
    if name == "codex-oss":
        return CodexContestant("gpt-oss:20b", local=True)
    if name == "gemini":
        return GeminiContestant("gemini-3.8-flash", load_settings().require("gemini_api_key"))
    raise typer.BadParameter(f"unknown entrant {name!r}; choose from {', '.join(ENTRANTS)}")


def arena_bench(slug: str) -> TaskBench:
    """The test environment for a target, kept outside the repository."""
    target = get_target(slug)
    bench = TaskBench(target, TargetWorkspace(target).repo_dir, root=verdict_home() / "envs" / slug)
    bench.ensure_env()
    return bench


def _tasks(selection: list[str] | None = None) -> list[IssueTask]:
    try:
        tasks = load_tasks()
    except ArtifactError as exc:
        out.print(f"[red]{exc}[/red]  Run `verdict review mine` first.")
        raise typer.Exit(1) from exc
    try:
        valid = {tid for tid, o in load_oracles().items() if o.valid}
        tasks = [t for t in tasks if t.task_id in valid]
    except ArtifactError:
        pass
    if selection:
        wanted = set(selection)
        tasks = [t for t in tasks if t.task_id in wanted or str(t.reference) in wanted]
    return tasks


@arena_app.command("oracles")
def arena_oracles() -> None:
    """Run each maintainer fix's test files before and after the fix.

    FAIL_TO_PASS tests prove a patch fixed the bug; PASS_TO_PASS tests prove it
    broke nothing the real fix kept working. Tests that flip between two runs of
    the fixed tree are excluded as unstable.
    """
    benches: dict[str, Referee] = {}
    oracles = []
    for task in load_tasks():
        if task.target_slug not in benches:
            benches[task.target_slug] = Referee(
                arena_bench(task.target_slug), verdict_home() / "scratch"
            )
        oracle = benches[task.target_slug].oracle(task)
        oracles.append(oracle)
        colour = "green" if oracle.valid else "yellow"
        out.print(
            f"  {task.target_slug:12s} #{task.reference:<6} [{colour}]{oracle.note}[/{colour}]"
        )
    path, digest = save_oracles(oracles)
    usable = sum(o.valid for o in oracles)
    out.print(
        f"\n[bold]{usable}/{len(oracles)}[/bold] tasks scorable  ->  {path}  {digest[:16]}..."
    )


@arena_app.command("import-recorded")
def arena_import_recorded() -> None:
    """Admit the earlier study's Claude Code patches, re-measured and round-tripped."""
    tasks = {t.task_id: t for t in load_tasks()}
    benches: dict[str, tuple[TaskBench, TaskBench]] = {}
    entries = []
    for diff in load_diffs():
        task = tasks[diff.task_id]
        if task.target_slug not in benches:
            target = get_target(task.target_slug)
            benches[task.target_slug] = (
                TaskBench(target, TargetWorkspace(target).repo_dir),
                arena_bench(task.target_slug),
            )
        old, new = benches[task.target_slug]
        patch = recorded.convert(task, diff, old, new, verdict_home() / "scratch")
        entries.append(recorded.entry(task, patch, new.target.test_root))
    path = save_entries(
        recorded.RECORDED_ENTRANT,
        entries,
        {
            "harness": "claude-code",
            "source": "recorded by the earlier real-diff study (2026-09-01)",
            "note": "not re-run: prompt was not the arena's standard prompt",
        },
    )
    out.print(f"[bold]{len(entries)}[/bold] recorded patches admitted  ->  {path}")


@arena_app.command("race")
def arena_race(
    entrants: Annotated[
        list[str], typer.Option("--entrant", "-e", help=f"One of {', '.join(ENTRANTS)}.")
    ],
    run: Annotated[str, typer.Option("--run", help="Name of this race; reuse it to resume.")] = (
        "main"
    ),
    only: Annotated[
        list[str] | None, typer.Option("--task", "-t", help="Task id or issue number.")
    ] = None,
    timeout: Annotated[int, typer.Option(help="Seconds before an attempt is killed.")] = 1800,
    chatgpt_slots: Annotated[int, typer.Option(help="Concurrent Codex attempts.")] = 2,
    retry_failed: Annotated[
        bool, typer.Option(help="Re-run attempts that failed without producing a fix.")
    ] = False,
) -> None:
    """Every selected task against every selected coding agent.

    Each attempt journals its result, so re-running the same --run skips work
    already done instead of spending an agent's quota on it twice.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    tasks = _tasks(only)
    contestants = [contestant(e) for e in entrants]
    benches = {slug: arena_bench(slug) for slug in {t.target_slug for t in tasks}}
    race = Race(
        verdict_home() / "runs" / run,
        verdict_home() / "pristine",
        benches,
        timeout=timeout,
        lane_slots={"chatgpt": chatgpt_slots, "gemini": 1, "ollama": 1},
        retry_failed=retry_failed,
    )
    out.print(
        f"Racing {len(contestants)} agent(s) on {len(tasks)} task(s): "
        f"{', '.join(c.name for c in contestants)}"
    )
    needs_local = any(c.lane == "ollama" for c in contestants)
    with OllamaServer() if needs_local else nullcontext():
        entries = race.run(tasks, contestants)

    by_entrant: dict[str, list[dict[str, object]]] = defaultdict(list)
    for entry in entries:
        by_entrant[str(entry["entrant"])].append(entry)
    for c in contestants:
        path = save_entries(c.name, by_entrant[c.name], c.describe())
        done = sum(1 for e in by_entrant[c.name] if e["fix_files"])
        out.print(f"  {c.name:28s} {done}/{len(tasks)} produced a fix  ->  {path.name}")


@arena_app.command("score")
def arena_score() -> None:
    """Grade every entrant's patches against the hidden maintainer tests."""
    tasks = {t.task_id: t for t in _tasks()}
    oracles = load_oracles()
    referees: dict[str, Referee] = {}
    resolutions: list[Resolution] = []
    table = Table(title="resolved / attempted, per entrant", show_header=True)
    for column in ("Entrant", "Resolved", "Fixed bug", "Broke P2P", "No patch"):
        table.add_column(column)

    for entrant in list_entrants():
        payload = load_entries(entrant)
        resolved = fixed = broke = missing = 0
        for entry in payload["entries"]:
            task = tasks.get(entry["task_id"])
            if task is None:
                continue
            if task.target_slug not in referees:
                referees[task.target_slug] = Referee(
                    arena_bench(task.target_slug), verdict_home() / "scratch"
                )
            patch = Patch.from_dict(entry["patch"])
            if not entry["fix_files"]:
                missing += 1
                resolutions.append(
                    Resolution(
                        task.task_id,
                        entrant,
                        True,
                        False,
                        0,
                        len(oracles[task.task_id].fail_to_pass),
                        (),
                        len(oracles[task.task_id].pass_to_pass),
                        "no fix produced",
                    )
                )
                continue
            result = referees[task.target_slug].evaluate(
                task, oracles[task.task_id], patch, entrant
            )
            resolutions.append(result)
            resolved += result.resolved
            fixed += result.fixed_bug
            broke += result.broke_something
        total = len(tasks)
        table.add_row(entrant, f"{resolved}/{total}", str(fixed), str(broke), str(missing))
    out.print(table)
    path, digest = save_resolutions(resolutions)
    out.print(f"  saved {path}  {digest[:16]}...")


def _entry_patches(task_ids: set[str]) -> dict[str, dict[str, Patch]]:
    """task -> entrant -> that entrant's whole patch, for every recorded entrant."""
    patches: dict[str, dict[str, Patch]] = defaultdict(dict)
    for entrant in list_entrants():
        for entry in load_entries(entrant)["entries"]:
            if entry["task_id"] in task_ids and entry.get("patch", {}).get("diff"):
                patches[entry["task_id"]][entrant] = Patch.from_dict(entry["patch"])
    return patches


def _examiners(local: bool) -> list[Examiner]:
    settings = load_settings()
    examiners = [
        Examiner(
            "examiner/gemini-flash",
            "google",
            [
                GeminiProvider(settings.require("gemini_api_key"), model=m, thinking="low")
                for m in ("gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash")
            ],
            context_budget=40_000,
            max_output_tokens=16_384,
        ),
        # Groq books prompt + max_tokens against 8,000 tokens a minute, so the
        # context is held to what fits beside a 4,096-token answer.
        Examiner(
            "examiner/qwen3.8-27b",
            "alibaba",
            [
                GroqProvider(settings.require("groq_api_key"), model=m)
                for m in ("qwen/qwen3.8-27b", "qwen/qwen3.6-27b")
            ],
            context_budget=9_000,
        ),
    ]
    if local:
        examiners.append(
            Examiner(
                "examiner/gpt-oss-20b",
                "openai",
                [OllamaProvider("gpt-oss:20b", port=OllamaServer.PORT)],
                context_budget=40_000,
            )
        )
    return examiners


@arena_app.command("examine")
def arena_examine(
    local: Annotated[bool, typer.Option(help="Add the local gpt-oss examiner.")] = False,
    only: Annotated[
        list[str] | None, typer.Option("--task", "-t", help="Task id or issue number.")
    ] = None,
) -> None:
    """Blind examiners write regression tests from each issue alone.

    They see the issue and the base version of the files the contestants
    changed - never a patch. One call per examiner per task.
    """
    tasks = _tasks(only)
    patches = _entry_patches({t.task_id for t in tasks})
    examiners = _examiners(local)
    exhibits: list[dict[str, object]] = []
    with OllamaServer() if local else nullcontext():
        for task in tasks:
            bench = arena_bench(task.target_slug)
            base = verdict_home() / "pristine" / task.task_id
            if not base.exists():
                bench.archive(task.base_commit, base)
            test_root = bench.target.test_root
            touched: dict[str, set[int]] = {}
            counts: dict[str, int] = defaultdict(int)
            for patch in patches.get(task.task_id, {}).values():
                for rel, lines in patch.only(under=test_root, keep=False).touched_lines().items():
                    touched.setdefault(rel, set()).update(lines)
                    counts[rel] += 1
            files = sorted(counts, key=lambda f: -counts[f])
            for examiner in examiners:
                context = source_context(base, files, touched, examiner.context_budget)
                exhibit = examiner.examine(task, bench.target.full_name, context)
                exhibits.append(exhibit.to_dict())
                state = f"[red]{exhibit.error[:90]}[/red]" if exhibit.error else exhibit.model
                out.print(
                    f"  {task.target_slug:12s} #{task.reference:<6} {examiner.author:26s} {state}"
                )
    path = save_exhibits(
        exhibits,
        {
            e.author: {"family": e.family, "models": [p.model for p in e.providers]}
            for e in examiners
        },
    )
    written = sum(1 for e in exhibits if e["test_code"])
    out.print(f"\n[bold]{written}/{len(exhibits)}[/bold] exhibits written  ->  {path}")


@arena_app.command("cross")
def arena_cross(
    only: Annotated[
        list[str] | None, typer.Option("--task", "-t", help="Task id or issue number.")
    ] = None,
    dry_run: Annotated[bool, typer.Option(help="Print the matrix sizes; save nothing.")] = False,
) -> None:
    """Run every exhibit against every fix and the untouched base."""
    tasks = _tasks(only)
    patches = _entry_patches({t.task_id for t in tasks})
    try:
        blind = [Exhibit.from_dict(e) for e in load_exhibits()]
    except ArtifactError:
        blind = []
        out.print(
            "[yellow]No blind exhibits yet; only contestants' own tests will be used.[/yellow]"
        )
    matrices = []
    for task in tasks:
        bench = arena_bench(task.target_slug)
        entries = patches.get(task.task_id, {})
        test_root = bench.target.test_root
        exhibits = [
            *self_exhibits(task, entries, test_root),
            *(e for e in blind if e.task_id == task.task_id and e.test_code),
        ]
        fixes = fixes_only(entries, test_root)
        matrix = CrossExaminer(bench, verdict_home() / "scratch").build(task, fixes, exhibits)
        matrices.append(matrix.to_dict())
        sizes = ", ".join(f"{t}={len(o)}" for t, o in matrix.outcomes.items())
        out.print(
            f"  {task.target_slug:12s} #{task.reference:<6} {len(exhibits)} exhibits  {sizes}"
        )
    if dry_run:
        for m in matrices:
            verdict = judge(Matrix.from_dict(m))
            out.print(f"  dry run: {verdict.chosen or 'abstain'} - {verdict.reason}")
        return
    path = save_matrices(matrices)
    out.print(f"\n[bold]{len(matrices)}[/bold] matrices  ->  {path}")


@arena_app.command("judge")
def arena_judge() -> None:
    """Pick a patch per task from its matrix alone - or abstain."""
    verdicts = [judge(Matrix.from_dict(m)) for m in load_matrices()]
    path = save_verdicts([v.to_dict() for v in verdicts])
    for v in verdicts:
        colour = "green" if v.chosen else "yellow"
        out.print(f"  {v.task_id}  [{colour}]{v.chosen or 'abstain'}[/{colour}]  {v.reason}")
    out.print(f"\n  saved {path}")


@arena_app.command("report")
def arena_report() -> None:
    """Score the judge's picks against the hidden oracles, beside every baseline."""
    tasks = _tasks()
    ids = [t.task_id for t in tasks]
    matrices = {m["task_id"]: m for m in load_matrices()}
    candidates = {tid: [t for t in m["trees"] if t != "base"] for tid, m in matrices.items()}
    board = scoreboard.build(ids, load_resolutions(), load_verdicts(), candidates)

    table = Table(title=f"{board.tasks} real bugs, scored by the maintainers' hidden tests")
    for column in ("Selector", "Resolved", "Note"):
        table.add_column(column)
    for entrant, stats in sorted(board.per_entrant.items(), key=lambda kv: -kv[1]["resolved"]):
        table.add_row(
            entrant,
            f"{stats['resolved']}/{board.tasks}",
            f"broke existing tests on {stats['broke_tests']}",
        )
    table.add_row("random pick among patches", f"{board.random_pick:.1f}/{board.tasks}", "expected")
    table.add_row(
        "[bold]Verdict[/bold]",
        f"[bold]{board.verdict_resolved}/{board.tasks}[/bold]",
        f"{board.verdict_picks} picks, {board.wrong_picks} wrong, "
        f"{board.verdict_abstained} abstained ({board.abstained_correctly} correctly)",
    )
    table.add_row("best possible (any agent)", f"{board.best_possible}/{board.tasks}", "ceiling")
    out.print(table)


@arena_app.command("export")
def arena_export(
    out_path: Annotated[
        Path, typer.Option("--out", help="Where the dashboard reads its data.")
    ] = Path("dashboard/data/arena.json"),
) -> None:
    """Bundle every artifact the dashboard shows into one JSON file."""
    tasks = _tasks()
    ids = [t.task_id for t in tasks]
    matrices = {m["task_id"]: Matrix.from_dict(m) for m in load_matrices()}
    candidates = {tid: [t for t in m.trees if t != "base"] for tid, m in matrices.items()}
    board = scoreboard.build(ids, load_resolutions(), load_verdicts(), candidates)
    entries: dict[str, dict[str, dict[str, object]]] = defaultdict(dict)
    meta: dict[str, dict[str, object]] = {}
    for entrant in list_entrants():
        payload = load_entries(entrant)
        meta[entrant] = payload.get("meta", {})
        for entry in payload["entries"]:
            entries[entry["task_id"]][entrant] = entry
    exhibits_doc = payload_of(read_artifact(arena_dir() / "exhibits.json"))
    bundle = build_bundle(
        tasks,
        {t.target_slug: get_target(t.target_slug).full_name for t in tasks},
        load_oracles(),
        entries,
        meta,
        exhibits_doc["exhibits"],
        exhibits_doc.get("examiners", {}),
        matrices,
        load_resolutions(),
        board,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(bundle, indent=1, default=str), encoding="utf-8")
    size = out_path.stat().st_size / 1024
    out.print(
        f"[bold]{len(bundle['tasks'])}[/bold] tasks exported  ->  {out_path}  ({size:.0f} KB)"
    )
