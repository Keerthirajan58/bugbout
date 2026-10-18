"""Evaluation: run the agent pair over a corpus and score the result."""

from verdict.eval.case import CaseOutcome
from verdict.eval.harness import EvalHarness, EvalStats, stats_from
from verdict.eval.store import (
    ARM_NO_RETRIEVAL,
    ARM_WITH_RETRIEVAL,
    load_cases,
    load_eval,
    save_eval,
)

__all__ = [
    "ARM_NO_RETRIEVAL",
    "ARM_WITH_RETRIEVAL",
    "CaseOutcome",
    "EvalHarness",
    "EvalStats",
    "load_cases",
    "load_eval",
    "save_eval",
    "stats_from",
]
