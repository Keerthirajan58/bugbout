"""A bout: the whole pipeline for one project, stage by stage, resumably.

    mine          real fixes from the history become tasks
    ground truth  the maintainers' tests say what "resolved" means for each task
    race          contestants fix every task, each in its own sandboxed checkout
    examine       examiners write tests from the issue alone
    judge         every test runs against every fix, and a policy picks one fix per task
    score         the ground truth scores every fix, and so every pick

Each stage saves what it produced and later stages read it back. Running a
stage again skips the work already done, so a bout survives interruptions and
grows when agents are added.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Callable, Sequence
from functools import cached_property
from pathlib import Path

from bugbout.agents.base import CodingAgent
from bugbout.arena.briefs import ASSIGNMENTS, FIX, Assignment
from bugbout.arena.exhibits import Exhibit, exhibit_of
from bugbout.arena.judge import Judge, JudgePolicy, Verdict
from bugbout.arena.matrix import CrossExaminer, Matrix
from bugbout.arena.race import Attempt, Race
from bugbout.errors import ArtifactError, ConfigError
from bugbout.log import get_logger
from bugbout.project import Examiner, Project
from bugbout.report import Report, Results, build_report
from bugbout.sandbox import Sandbox, get_sandbox
from bugbout.store import Store
from bugbout.tasks.issues import GitHubIssues, IssueTracker
from bugbout.tasks.mining import Miner
from bugbout.tasks.oracle import Oracle, Referee, Resolution
from bugbout.tasks.task import Task
from bugbout.workspace.patch import Patch
from bugbout.workspace.repository import Repository
from bugbout.workspace.workspace import Workspace

log = get_logger(__name__)

OnAttempt = Callable[[Attempt], None]


class Bout:
    def __init__(
        self,
        project: Project,
        *,
        contestants: Sequence[CodingAgent] | None = None,
        examiners: Sequence[Examiner] | None = None,
        sandbox: Sandbox | None = None,
        issues: IssueTracker | None = None,
    ) -> None:
        """Agents, sandbox and issue tracker default to what the project configures."""
        self.project = project
        self.contestants = list(contestants if contestants is not None else project.contestants())
        self.examiners = list(examiners if examiners is not None else project.examiners())
        self.store = Store(project.results_dir)
        self._sandbox = sandbox
        self._issues = issues

    @classmethod
    def load(cls, path: Path | str = ".") -> Bout:
        """The bout ``bugbout.toml`` at ``path`` describes."""
        return cls(Project.load(path))

    # --- what the stages share ----------------------------------------------------

    @cached_property
    def sandbox(self) -> Sandbox:
        return self._sandbox or get_sandbox(self.project.config.run.sandbox)

    @cached_property
    def repository(self) -> Repository:
        return self.project.open_repository()

    @cached_property
    def workspace(self) -> Workspace:
        return Workspace(
            self.project.cache_dir / "workspace",
            self.repository,
            self.project.python_project(),
            self.sandbox,
        )

    @cached_property
    def repo_name(self) -> str:
        """How agents are told the project is called."""
        configured = self.project.config.project.github
        return configured or self.repository.github_slug() or self.project.root.name

    @cached_property
    def issues(self) -> IssueTracker:
        if self._issues is not None:
            return self._issues
        slug = self.project.config.project.github or self.repository.github_slug()
        if slug is None:
            raise ConfigError(
                "cannot tell which GitHub repository holds the issues; "
                'set github = "owner/name" under [project]'
            )
        return GitHubIssues(slug, self.project.cache_dir / "issues")

    @property
    def test_root(self) -> str:
        return self.project.config.project.tests

    # --- the stages -----------------------------------------------------------------

    def check(self) -> None:
        """Fail before any agent runs if something the bout needs is missing."""
        if not self.contestants:
            raise ConfigError("no contestants are configured; add a [[contestants]] entry")
        for agent in [*self.contestants, *(examiner.agent for examiner in self.examiners)]:
            agent.check()
        log.debug("Sandboxing with %s", self.sandbox.name)

    def mine(self, *, refresh: bool = False) -> list[Task]:
        """Tasks from the project's history: mined once, then read back."""
        if self.store.has("tasks.json") and not refresh:
            return self.store.tasks()
        log.info("Mining %s's history for fixed bugs", self.repo_name)
        tasks, report = Miner(self.repository, self.issues, self.project.mining_rules()).mine()
        self.store.save_tasks(tasks, report)
        log.info("Found %d tasks in %d changes", len(tasks), report.examined)
        return tasks

    def ground_truth(self) -> dict[str, Oracle]:
        """Each task's oracle, built once from the maintainers' own tests."""
        tasks = self.mine()
        oracles = self.store.oracles()
        missing = [task for task in tasks if task.task_id not in oracles]
        if missing:
            self.workspace.prepare()
            referee = Referee(self.workspace)
            for task in missing:
                log.info("Establishing ground truth for %s", task.task_id)
                oracles[task.task_id] = referee.oracle(task)
                self.store.save_oracles(oracles)  # after each task, so progress survives
        return {task.task_id: oracles[task.task_id] for task in tasks}

    def scorable_tasks(self) -> list[Task]:
        """Tasks whose ground truth proves something: the denominator of every number."""
        oracles = self.ground_truth()
        return [task for task in self.mine() if oracles[task.task_id].valid]

    def race(
        self, *, retry_failed: bool = False, on_attempt: OnAttempt | None = None
    ) -> list[Attempt]:
        """Every contestant fixes every scorable task."""
        self.store.save_agents({agent.name: agent.describe() for agent in self.contestants})
        return self._race(FIX, self.contestants, retry_failed, on_attempt)

    def examine(
        self, *, retry_failed: bool = False, on_attempt: OnAttempt | None = None
    ) -> list[Exhibit]:
        """Every examiner writes tests for every scorable task, from the issue alone."""
        teams: dict[str, list[CodingAgent]] = defaultdict(list)
        for examiner in self.examiners:
            teams[examiner.assignment.name].append(examiner.agent)
        attempts = [
            attempt
            for name, agents in teams.items()
            for attempt in self._race(ASSIGNMENTS[name], agents, retry_failed, on_attempt)
        ]
        return [exhibit_of(attempt, self.test_root) for attempt in attempts]

    def cross_examine(self) -> dict[str, Matrix]:
        """Every test against every fix; a task's matrix is rebuilt only when its inputs change."""
        contestants = self._journaled(FIX, self.contestants)
        examiners = self._examiner_exhibits()
        cross = CrossExaminer(self.workspace, run_suite=self.project.config.judge.run_suite)
        matrices: dict[str, Matrix] = {}
        for task in self.scorable_tasks():
            attempts = [a for a in contestants if a.task_id == task.task_id]
            fixes = {
                a.agent: fix
                for a in attempts
                if not (fix := a.patch.only(under=self.test_root, keep=False)).empty
            }
            exhibits = [exhibit_of(a, self.test_root) for a in attempts]
            exhibits += [e for e in examiners if e.task_id == task.task_id]
            vendors = {a.agent: a.vendor for a in attempts}
            inputs = _fingerprint(fixes, exhibits, vendors, cross.run_suite)
            stored = self.store.matrix(task.task_id)
            if stored is not None and stored[1] == inputs:
                matrices[task.task_id] = stored[0]
                continue
            log.info("Cross-examining %d fixes for %s", len(fixes), task.task_id)
            matrices[task.task_id] = cross.build(task, fixes, exhibits, vendors)
            self.store.save_matrix(matrices[task.task_id], inputs)
        return matrices

    def judge(self, policy: str | JudgePolicy | None = None) -> list[Verdict]:
        """A verdict per task: the fix the evidence supports, or an abstention."""
        judge = Judge(policy or self.project.config.judge.policy)
        verdicts = [judge(matrix) for matrix in self.cross_examine().values()]
        self.store.save_verdicts(verdicts)
        return verdicts

    def score(self) -> list[Resolution]:
        """Every contestant's fix against the hidden ground truth; each patch is scored once."""
        oracles = self.ground_truth()
        tasks = {task.task_id: task for task in self.scorable_tasks()}
        stored = self.store.resolutions()
        referee = Referee(self.workspace)
        scored: dict[str, Resolution] = {}
        for attempt in self._journaled(FIX, self.contestants):
            task = tasks.get(attempt.task_id)
            if task is None:
                continue
            key = _patch_key(attempt)
            scored[key] = stored.get(key) or referee.score(
                task, oracles[task.task_id], attempt.patch, attempt.agent
            )
        self.store.save_resolutions(scored)
        return list(scored.values())

    def run(self, *, retry_failed: bool = False, on_attempt: OnAttempt | None = None) -> Report:
        """Every stage, in order, skipping whatever is already done."""
        self.check()
        self.race(retry_failed=retry_failed, on_attempt=on_attempt)
        self.examine(retry_failed=retry_failed, on_attempt=on_attempt)
        self.judge()
        self.score()
        return self.report()

    def report(self) -> Report:
        """What the stored results say, computed without running anything."""
        if not self.store.has("tasks.json"):
            raise ArtifactError("there are no results yet; run `bugbout run` first")
        tasks = self.store.tasks()
        oracles = self.store.oracles()
        scorable = [t for t in tasks if (o := oracles.get(t.task_id)) is not None and o.valid]
        ids = {task.task_id for task in scorable}
        names = {agent.name for agent in self.contestants}
        verdicts = [v for v in self.store.verdicts() if v.task_id in ids]
        matrices = {
            task_id: stored[0]
            for task_id in ids
            if (stored := self.store.matrix(task_id)) is not None
        }
        return build_report(
            Results(
                project=self.project.name,
                policy=verdicts[0].policy if verdicts else self.project.config.judge.policy,
                mining=self.store.mining(),
                tasks_mined=len(tasks),
                tasks=scorable,
                attempts=[a for a in self._journaled(FIX, self.contestants) if a.task_id in ids],
                resolutions=[r for r in self.store.resolutions().values() if r.entrant in names],
                verdicts=verdicts,
                matrices=matrices,
                agents={n: d for n, d in self.store.agents().items() if n in names},
            )
        )

    # --- internals ------------------------------------------------------------------

    def _race(
        self,
        assignment: Assignment,
        agents: list[CodingAgent],
        retry_failed: bool,
        on_attempt: OnAttempt | None,
    ) -> list[Attempt]:
        if not agents:
            return []
        tasks = self.scorable_tasks()
        settings = self.project.config.run
        race = Race(
            self.workspace,
            assignment,
            self.store.journal(assignment.name),
            sandbox=self.sandbox,
            repo=self.repo_name,
            timeout=settings.timeout,
            quotas=settings.quotas,
            retry_failed=retry_failed,
        )
        return race.run(tasks, agents, on_attempt)

    def _journaled(self, assignment: Assignment, agents: Sequence[CodingAgent]) -> list[Attempt]:
        """Journaled attempts by the agents configured now - not ones since removed."""
        names = {agent.name for agent in agents}
        return [a for a in self.store.attempts(assignment.name) if a.agent in names]

    def _examiner_exhibits(self) -> list[Exhibit]:
        return [
            exhibit_of(attempt, self.test_root)
            for examiner in self.examiners
            for attempt in self._journaled(examiner.assignment, [examiner.agent])
        ]


def _fingerprint(
    fixes: dict[str, Patch], exhibits: list[Exhibit], vendors: dict[str, str], run_suite: bool
) -> str:
    """What a matrix depends on; when it changes, the matrix is stale."""
    inputs = {
        "fixes": {name: fix.diff for name, fix in sorted(fixes.items())},
        "exhibits": sorted((e.author, e.kind, e.vendor, e.test_code) for e in exhibits),
        "vendors": dict(sorted(vendors.items())),
        "run_suite": run_suite,
    }
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()


def _patch_key(attempt: Attempt) -> str:
    digest = hashlib.sha256(attempt.patch.diff.encode()).hexdigest()[:16]
    return f"{attempt.task_id}/{attempt.agent}/{digest}"
