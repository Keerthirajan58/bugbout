"""Execute a target's test suite against each mutant and score the outcome.

This is where the ground truth is actually produced. Every downstream claim -
the baseline kill-rate, the surviving-mutant corpus, the ablation delta - is
just arithmetic over the results this module returns.

**How a mutant is scored.** Apply the mutation to a file on disk, run the suite,
restore the file. If some test fails, the suite *detected* the change: the
mutant is **killed**. If every test still passes, the mutant **survived** - the
code changed, behaviour changed, and nothing noticed. Survivors are the point of
the whole exercise: each one is a real change that passes the entire test suite
and is still wrong, which is exactly what Verdict claims to catch.
"""

from __future__ import annotations

import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import cosmic_ray.plugins as plugins
from cosmic_ray.mutating import mutate_code

from verdict.logging import get_logger
from verdict.mutation.mutant import MutantResult, MutantSpec, Status, make_unified_diff
from verdict.paths import workspaces_dir
from verdict.targets.workspace import SuiteResult, TargetWorkspace

log = get_logger(__name__)

_FAILED_TEST = re.compile(r"^FAILED (\S+)", re.MULTILINE)
_COLLECTION_ERROR = re.compile(
    r"(ERROR collecting|errors? during collection|INTERNALERROR|Interrupted:)", re.MULTILINE
)


class ProgressCallback(Protocol):
    def __call__(self, done: int, total: int, result: MutantResult) -> None: ...


@dataclass(frozen=True, slots=True)
class RunStats:
    """Aggregate scoring for a mutation run."""

    total: int
    killed: int
    killed_import: int
    survived: int
    timeout: int
    incompetent: int
    no_mutation: int

    @property
    def detected(self) -> int:
        """Mutants the suite noticed, by any mechanism."""
        return self.killed + self.killed_import + self.timeout

    @property
    def scored(self) -> int:
        """The kill-rate denominator: every mutant that produced a real change.

        Excluded are only mutants that could not be applied at all, and any
        unexplained harness failures. Both measure nothing about test quality,
        and both are reported separately rather than quietly folded in.
        """
        return self.detected + self.survived

    @property
    def kill_rate(self) -> float:
        """Fraction of applicable mutants the existing suite detects."""
        return self.detected / self.scored if self.scored else 0.0

    @property
    def test_kill_rate(self) -> float:
        """Kill-rate counting only mutants caught by a test actually failing.

        The stricter reading: it excludes mutants that merely broke the import.
        Reported alongside the headline figure so the number cannot be accused
        of leaning on trivially-detected mutants.
        """
        return (self.killed + self.timeout) / self.scored if self.scored else 0.0

    def summary(self) -> str:
        return (
            f"kill-rate {self.kill_rate:.1%} ({self.detected}/{self.scored}) | "
            f"survived {self.survived}, killed {self.killed}, "
            f"killed-at-import {self.killed_import}, timeout {self.timeout}, "
            f"incompetent {self.incompetent}, not applicable {self.no_mutation}"
        )


def stats_from(results: list[MutantResult]) -> RunStats:
    counts: dict[Status, int] = {
        "killed": 0,
        "killed_import": 0,
        "survived": 0,
        "timeout": 0,
        "incompetent": 0,
        "no_mutation": 0,
    }
    for result in results:
        counts[result.status] += 1
    return RunStats(total=len(results), **counts)


class MutationRunner:
    """Scores mutants against one prepared workspace.

    A runner owns its workspace exclusively. Mutation rewrites source files in
    place, so two runners sharing a checkout would corrupt each other's results
    in a way that produces plausible-looking but wrong numbers.
    """

    def __init__(
        self,
        workspace: TargetWorkspace,
        *,
        timeout: int = 120,
        fail_fast: bool = True,
    ) -> None:
        self.workspace = workspace
        self.timeout = timeout
        # A killed mutant only needs one failing test, and stopping at the first
        # one is the single largest speedup available in the whole run.
        self.fail_fast = fail_fast

    def run_one(self, spec: MutantSpec) -> MutantResult:
        """Apply one mutant, run the suite, restore the file, and classify."""
        path = self.workspace.repo_dir / spec.module_path
        original = path.read_text(encoding="utf-8")

        operator = plugins.get_operator(spec.operator)()
        mutated = mutate_code(original, operator, spec.occurrence)

        if mutated is None or mutated == original:
            # The operator matched during enumeration but produces no change
            # here (cosmic-ray declines some sites). Not scoreable either way.
            return MutantResult(
                spec=spec,
                status="no_mutation",
                duration_seconds=0.0,
                tests_passed=0,
                tests_failed=0,
                killing_test=None,
                diff="",
            )

        diff = make_unified_diff(original, mutated, spec.module_path)
        try:
            path.write_text(mutated, encoding="utf-8")
            run = self.workspace.run_tests(
                fail_fast=self.fail_fast,
                extra_args=("--tb=no", "-rf"),
                timeout=self.timeout,
            )
        finally:
            # Restore unconditionally. A leaked mutation would silently poison
            # every subsequent mutant scored in this workspace.
            path.write_text(original, encoding="utf-8")

        status = self._classify(spec, run)
        killing = self._killing_test(run.stdout) if status == "killed" else None
        return MutantResult(
            spec=spec,
            status=status,
            duration_seconds=run.duration_seconds,
            tests_passed=run.passed,
            tests_failed=run.failed,
            killing_test=killing,
            # Survivors become the phase 2 corpus, so their diff is what matters
            # most; keeping every diff would bloat the artifact for no gain.
            diff=diff,
        )

    @staticmethod
    def _classify(spec: MutantSpec, run: SuiteResult) -> Status:
        """Decide what one test run says about a mutant.

        The subtle case is a non-standard exit code. A mutation can break a
        module badly enough that importing it raises, so pytest fails during
        *collection* and never runs a test at all. That is still detection - and
        the most obvious kind, since the whole suite errors out - so it must not
        be discarded as an invalid mutant.

        The two are told apart by evidence, not assumption: the baseline was
        verified green before any mutation, so if the failure output implicates
        the mutated module, the mutation caused it.
        """
        if run.timed_out:
            # An inverted loop bound that hangs is detection too: the suite
            # would never go green on this change.
            return "timeout"
        if run.exit_code == 0:
            return "survived"
        if run.exit_code == 1:
            return "killed"

        combined = f"{run.stdout}\n{run.stderr}"
        if spec.module_path in combined or _COLLECTION_ERROR.search(combined):
            return "killed_import"
        # Anything else is unexplained. Kept in its own bucket rather than
        # folded into a result bucket, so harness problems stay visible.
        return "incompetent"

    @staticmethod
    def _killing_test(output: str) -> str | None:
        match = _FAILED_TEST.search(output)
        return match.group(1) if match else None

    def run(
        self,
        specs: list[MutantSpec],
        *,
        on_result: ProgressCallback | None = None,
    ) -> list[MutantResult]:
        """Score a list of mutants sequentially in this runner's workspace."""
        # Guard against contamination from an interrupted previous run.
        self.workspace.ensure_clean()
        results: list[MutantResult] = []
        for index, spec in enumerate(specs, start=1):
            result = self.run_one(spec)
            results.append(result)
            if on_result:
                on_result(index, len(specs), result)
        return results


def prepare_workers(
    workspace: TargetWorkspace,
    count: int,
    *,
    force: bool = False,
) -> list[TargetWorkspace]:
    """Build ``count`` independent checkouts for parallel scoring.

    Each worker is cloned from the already-prepared primary workspace rather
    than from GitHub, so adding workers costs a local copy instead of a
    download. Worker 0 is the primary itself.
    """
    workspace.ensure_clean()
    if count <= 1:
        return [workspace]
    workers = [workspace]
    for index in range(1, count):
        worker = TargetWorkspace(
            workspace.target,
            root=workspaces_dir() / "workers" / f"w{index}",
            clone_source=workspace.repo_dir,
        )
        log.info("Preparing worker %d/%d", index, count - 1)
        worker.ensure(force=force)
        worker.ensure_clean()
        workers.append(worker)
    return workers


def run_parallel(
    workers: list[TargetWorkspace],
    specs: list[MutantSpec],
    *,
    timeout: int = 120,
    fail_fast: bool = True,
    on_result: ProgressCallback | None = None,
) -> list[MutantResult]:
    """Score mutants across a pool of workspaces.

    Work is split into one contiguous shard per worker. Threads are the right
    primitive here despite the GIL: every worker spends essentially all of its
    time blocked in ``subprocess.run`` waiting on pytest, which releases it.
    """
    if len(workers) == 1:
        return MutationRunner(workers[0], timeout=timeout, fail_fast=fail_fast).run(
            specs, on_result=on_result
        )

    shards: list[list[MutantSpec]] = [specs[i :: len(workers)] for i in range(len(workers))]
    results: list[MutantResult] = []
    done = 0
    total = len(specs)

    with ThreadPoolExecutor(max_workers=len(workers)) as pool:
        futures = {
            pool.submit(
                MutationRunner(worker, timeout=timeout, fail_fast=fail_fast).run, shard
            ): worker
            for worker, shard in zip(workers, shards, strict=True)
            if shard
        }
        for future in as_completed(futures):
            shard_results = future.result()
            results.extend(shard_results)
            done += len(shard_results)
            if on_result and shard_results:
                on_result(done, total, shard_results[-1])

    # Restore the deterministic ordering that sharding broke.
    results.sort(key=lambda r: r.spec.mutant_id)
    return results


def cleanup_workers(target_slug: str) -> None:
    """Delete worker checkouts. They are fully reproducible, so this is safe."""
    workers_root = workspaces_dir() / "workers"
    if workers_root.exists():
        for worker_dir in workers_root.glob(f"w*/{target_slug}"):
            shutil.rmtree(worker_dir, ignore_errors=True)
        _prune_empty(workers_root)


def _prune_empty(root: Path) -> None:
    for child in sorted(root.glob("w*"), reverse=True):
        if child.is_dir() and not any(child.iterdir()):
            child.rmdir()
