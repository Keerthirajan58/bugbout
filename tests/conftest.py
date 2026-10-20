"""Fixtures shared across the suite: a real toy repository and its one task."""

from __future__ import annotations

from pathlib import Path

import pytest

from bugbout.tasks.mining import Miner, MiningRules
from bugbout.tasks.task import Task
from tests.toy import ISSUE, StaticIssues, Toy, build_toy


@pytest.fixture
def toy(tmp_path: Path) -> Toy:
    """The toy project: one real bug, fixed in a later commit with a new test."""
    return build_toy(tmp_path / "toy")


@pytest.fixture
def toy_task(toy: Toy) -> Task:
    """The toy project's one task, mined from its history."""
    rules = MiningRules(source_root="src/toy", test_root="tests")
    [task], _ = Miner(toy.repository, StaticIssues(ISSUE), rules).mine()
    return task
