"""The arena's stages, as one facade the CLI (or a notebook, or CI) calls.

Each method is one stage. It reads the artifacts the previous stage wrote,
does its work, writes its own artifact, and returns domain objects - it never
prints. Stages that run against a subset of tasks merge their results into the
saved set rather than replacing it.

``mine -> oracles -> race -> score -> examine -> cross -> judge -> report``
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any

from verdict.arena import analysis, scoreboard
from verdict.arena.agents import CodingAgent, OllamaServer
from verdict.arena.assignments import Fix
from verdict.arena.examiners import Examiner, ExamRoom, Exhibit, SourceContext
from verdict.arena.export import build_bundle
from verdict.arena.judge import Judge, Verdict
from verdict.arena.layout import ArenaLayout
from verdict.arena.matrix import CrossExaminer, Matrix, fixes_only, self_exhibits
from verdict.arena.patch import Patch
from verdict.arena.race import Attempt, Race
from verdict.arena.recorded import RECORDED_ENTRANT, RECORDED_META, RecordedImporter
from verdict.arena.roster import Roster
from verdict.arena.store import ArenaStore
from verdict.arena.truth import Oracle, Referee, Resolution
from verdict.config import Settings, load_settings
from verdict.paths import artifacts_dir
from verdict.review.bench import TaskBench
from verdict.review.store import load_diffs
from verdict.review.tasks import GitHubText, IssueTask, MiningReport, mine_tasks
from verdict.targets import get_target
from verdict.targets.workspace import TargetWorkspace


class ArenaWorkflow:
    def __init__(
        self,
        suite: str,
        *,
        settings: Settings | None = None,
        layout: ArenaLayout | None = None,
        store: ArenaStore | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self.layout = layout or ArenaLayout()
        self.store = store or ArenaStore(suite)
        self.roster = Roster(self.settings)

    @property
    def suite(self) -> str:
        return self.store.suite

    # --- tasks ---------------------------------------------------------------------

    def tasks(self, only: list[str] | None = None) -> list[IssueTask]:
        """Scorable tasks, optionally narrowed to ids or issue numbers."""
        tasks = self.store.scorable_tasks() if self.store.exists("oracles") else self.store.tasks()
        if only:
            wanted = set(only)
            tasks = [t for t in tasks if t.task_id in wanted or str(t.reference) in wanted]
        return tasks

    def mine(self, sources: dict[str, tuple[str, str]], per_target: int) -> list[IssueTask]:
        """Reconstruct tasks from each target's history: ``slug -> (source prefix, since)``."""
        github = GitHubText(self._github_tokens(), artifacts_dir() / "review" / "github")
        tasks: list[IssueTask] = []
        mining: dict[str, Any] = {}
        for slug, (prefix, since) in sources.items():
            target = get_target(slug)
            found, report = mine_tasks(
                TargetWorkspace(target).repo_dir,
                target_slug=slug,
                full_name=target.full_name,
                github=github,
                since=since,
                source_prefix=prefix,
                limit=per_target,
            )
            tasks.extend(found)
            mining[slug] = _mining_summary(report, len(found))
        self.store.save_tasks(tasks, mining)
        return tasks

    def _github_tokens(self) -> list[str | None]:
        """Configured token first, then the GitHub CLI's, then anonymous."""
        tokens: list[str | None] = []
        if self.settings.has("github_token"):
            tokens.append(self.settings.require("github_token"))
        if (cli := _gh_token()) is not None:
            tokens.append(cli)
        return [*tokens, None]

    # --- ground truth -------------------------------------------------------------

    def build_oracles(self) -> list[tuple[IssueTask, Oracle]]:
        referees: dict[str, Referee] = {}
        results = []
        for task in self.store.tasks():
            referee = referees.setdefault(
                task.target_slug, Referee(self.layout.bench(task.target_slug), self.layout.scratch)
            )
            results.append((task, referee.oracle(task)))
        self.store.save_oracles([oracle for _, oracle in results])
        return results

    def score(self) -> list[Resolution]:
        """Grade every entrant's fix against the hidden maintainer tests."""
        tasks = {t.task_id: t for t in self.tasks()}
        oracles = self.store.oracles()
        referees: dict[str, Referee] = {}
        resolutions = []
        for entrant in self.store.entrants():
            attempts, _ = self.store.entries(entrant)
            for attempt in attempts:
                task = tasks.get(attempt.task_id)
                if task is None:
                    continue
                oracle = oracles[task.task_id]
                if not attempt.source_files:
                    resolutions.append(Resolution.unproduced(task.task_id, entrant, oracle))
                    continue
                referee = referees.setdefault(
                    task.target_slug,
                    Referee(self.layout.bench(task.target_slug), self.layout.scratch),
                )
                resolutions.append(referee.evaluate(task, oracle, attempt.patch, entrant))
        self.store.save_resolutions(resolutions)
        return resolutions

    # --- contestants ----------------------------------------------------------------

    def import_recorded(self) -> Path:
        tasks = {t.task_id: t for t in self.store.tasks()}
        importer = RecordedImporter(self.layout)
        recorded_benches: dict[str, TaskBench] = {}
        attempts = []
        for diff in load_diffs():
            task = tasks.get(diff.task_id)
            if task is None:
                continue
            if task.target_slug not in recorded_benches:
                target = get_target(task.target_slug)
                recorded_benches[task.target_slug] = TaskBench(
                    target, TargetWorkspace(target).repo_dir
                )
            attempts.append(importer.attempt(task, diff, recorded_benches[task.target_slug]))
        return self.store.save_entries(RECORDED_ENTRANT, attempts, RECORDED_META)

    def race(
        self,
        entrants: list[str],
        *,
        only: list[str] | None = None,
        timeout: int = 1800,
        chatgpt_slots: int = 2,
        retry_failed: bool = False,
    ) -> dict[str, list[Attempt]]:
        agents = [self.roster.contestant(name) for name in entrants]
        race = Race(
            self.layout,
            self.suite,
            Fix(),
            timeout=timeout,
            lane_slots={"chatgpt": chatgpt_slots},
            retry_failed=retry_failed,
        )
        with self._local_models(any(a.lane == "ollama" for a in agents)):
            attempts = race.run(self.tasks(only), agents)
        by_agent: dict[str, list[Attempt]] = defaultdict(list)
        for attempt in attempts:
            by_agent[attempt.agent].append(attempt)
        for agent in agents:
            self._merge_entries(agent, by_agent[agent.name])
        return dict(by_agent)

    def _merge_entries(self, agent: CodingAgent, attempts: list[Attempt]) -> None:
        """Add this run's attempts to the agent's saved ones, replacing by task."""
        kept: dict[str, Attempt] = {}
        if agent.name in self.store.entrants():
            kept = {a.task_id: a for a in self.store.entries(agent.name)[0]}
        kept.update({a.task_id: a for a in attempts})
        self.store.save_entries(agent.name, list(kept.values()), agent.describe())

    def patches(self) -> dict[str, dict[str, Patch]]:
        """task id -> entrant -> the entrant's whole patch, for every entrant."""
        patches: dict[str, dict[str, Patch]] = defaultdict(dict)
        for entrant in self.store.entrants():
            for attempt in self.store.entries(entrant)[0]:
                if attempt.produced:
                    patches[attempt.task_id][entrant] = attempt.patch
        return dict(patches)

    # --- evidence -----------------------------------------------------------------------

    def examine(
        self, examiners: list[str], *, only: list[str] | None = None, retry_failed: bool = False
    ) -> list[Exhibit]:
        """Blind exhibits from each examiner, merged into the saved set by (task, author)."""
        tasks = self.tasks(only)
        room = ExamRoom(
            self.layout,
            self.suite,
            SourceContext(self.layout, self.patches()),
            retry_failed=retry_failed,
        )
        chosen: list[Examiner] = [self.roster.examiner(name) for name in examiners]
        fresh: list[Exhibit] = []
        with self._local_models(any(e.uses_local_models for e in chosen)):
            for examiner in chosen:
                fresh.extend(examiner.examine(tasks, room))
        saved, meta = self.store.exhibits() if self.store.exists("exhibits") else ([], {})
        merged = {(e.task_id, e.author): e for e in saved}
        merged.update({(e.task_id, e.author): e for e in fresh})
        meta.update({e.author: e.describe() for e in chosen})
        self.store.save_exhibits(list(merged.values()), meta)
        return fresh

    def cross(self, *, only: list[str] | None = None) -> list[Matrix]:
        """Every exhibit against every fix and the base, merged into the saved set."""
        patches = self.patches()
        blind = self.store.exhibits()[0] if self.store.exists("exhibits") else []
        matrices = []
        for task in self.tasks(only):
            bench = self.layout.bench(task.target_slug)
            entries = patches.get(task.task_id, {})
            test_root = bench.target.test_root
            exhibits = [
                *self_exhibits(task, entries, test_root),
                *(e for e in blind if e.task_id == task.task_id and e.usable),
            ]
            matrix = CrossExaminer(bench, self.layout.scratch).build(
                task, fixes_only(entries, test_root), exhibits
            )
            matrices.append(matrix)
        saved = self.store.matrices() if self.store.exists("matrices") else {}
        saved.update({m.task_id: m for m in matrices})
        self.store.save_matrices(list(saved.values()))
        return matrices

    # --- verdicts and results -------------------------------------------------------------

    def judge(self, policy: str = "v1") -> list[Verdict]:
        judge = Judge(policy)
        matrices = self.store.matrices()
        verdicts = [judge(matrices[t.task_id]) for t in self.tasks() if t.task_id in matrices]
        self.store.save_verdicts(policy, [v.to_dict() for v in verdicts])
        return verdicts

    def analyze(
        self, policies: list[str], entrants: set[str] | None = None
    ) -> list[analysis.PolicyResult]:
        """Every named policy, measured on this suite's saved matrices; nothing re-run."""
        task_ids = [t.task_id for t in self.tasks()]
        matrices = self.store.matrices()
        resolutions = self.store.resolutions()
        return [analysis.evaluate(p, task_ids, matrices, resolutions, entrants) for p in policies]

    def evidence_signal(self) -> list[analysis.SourceSignal]:
        """How well each source of evidence separates correct from wrong patches."""
        return analysis.evidence_signal(self.store.matrices(), self.store.resolutions())

    def reproduction_blindness(
        self, author: str = "examiner/reproduce/codex/gpt-6.1-sol"
    ) -> analysis.Blindness:
        """How many wrong patches pass every test an examiner wrote from the issue."""
        return analysis.reproduction_blindness(
            self.store.matrices(), self.store.resolutions(), author
        )

    def scoreboard(self, policy: str = "v1") -> scoreboard.Scoreboard:
        matrices = self.store.matrices()
        return scoreboard.build(
            [t.task_id for t in self.tasks()],
            self.store.resolutions(),
            self.store.verdicts(policy),
            {tid: m.candidates for tid, m in matrices.items()},
        )

    def export(self, path: Path, policy: str = "v1") -> Path:
        attempts: dict[str, dict[str, Attempt]] = defaultdict(dict)
        meta: dict[str, dict[str, Any]] = {}
        for entrant in self.store.entrants():
            entries, meta[entrant] = self.store.entries(entrant)
            for attempt in entries:
                attempts[attempt.task_id][entrant] = attempt
        blind, examiners = self.store.exhibits()
        tasks = self.tasks()
        patches = self.patches()
        own = [
            exhibit
            for task in tasks
            for exhibit in self_exhibits(
                task,
                patches.get(task.task_id, {}),
                self.layout.bench(task.target_slug).target.test_root,
            )
        ]
        bundle = build_bundle(
            suite=self.suite,
            tasks=tasks,
            repos={t.target_slug: get_target(t.target_slug).full_name for t in tasks},
            oracles=self.store.oracles(),
            attempts=attempts,
            entrant_meta=meta,
            exhibits=[*blind, *own],
            examiner_meta=examiners,
            matrices=self.store.matrices(),
            resolutions=self.store.resolutions(),
            judge=Judge(policy),
            board=self.analyze([policy])[0].board,
            signals=self.evidence_signal(),
            blindness=self.reproduction_blindness(),
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(bundle, indent=1, default=str), encoding="utf-8")
        return path

    # --- infrastructure ---------------------------------------------------------------

    @staticmethod
    @contextmanager
    def _local_models(needed: bool) -> Iterator[None]:
        """Run the private Ollama server for as long as a local model is in play."""
        with OllamaServer() if needed else nullcontext():
            yield


def _mining_summary(report: MiningReport, usable: int) -> dict[str, Any]:
    return {
        "commits_examined": report.examined,
        "fix_shaped_subjects": report.matched_subject,
        "usable": usable,
        "rejected": report.rejected,
    }


def _gh_token() -> str | None:
    """The GitHub CLI's token, if it is installed and signed in."""
    gh = shutil.which("gh")
    if gh is None:
        return None
    completed = subprocess.run(  # noqa: S603 - the resolved gh binary and literal arguments
        [gh, "auth", "token"], capture_output=True, text=True, timeout=15, check=False
    )
    token = completed.stdout.strip()
    return token or None
