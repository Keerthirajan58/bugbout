"""Mutation testing: the objective ground truth the whole project scores against."""

from verdict.mutation.generate import generate_mutants, summarize
from verdict.mutation.mutant import MutantResult, MutantSpec
from verdict.mutation.operators import DEFAULT_OPERATORS
from verdict.mutation.runner import MutationRunner, RunStats, run_parallel, stats_from

__all__ = [
    "DEFAULT_OPERATORS",
    "MutantResult",
    "MutantSpec",
    "MutationRunner",
    "RunStats",
    "generate_mutants",
    "run_parallel",
    "stats_from",
    "summarize",
]
