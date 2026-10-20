"""Results are kept one file per stage, read back exactly, and kept out of version control."""

from __future__ import annotations

from pathlib import Path

import pytest

from bugbout.arena.judge import Verdict
from bugbout.arena.matrix import BASE, Matrix
from bugbout.errors import ArtifactError
from bugbout.store import Store
from bugbout.tasks.mining import MiningReport
from bugbout.tasks.oracle import Oracle, Resolution
from bugbout.tasks.task import Task

TASK = Task(
    "abc", "base", "fix", 12, "issue", "title", "statement", ("a.py",), ("t.py",), ("test_x",)
)


def test_each_stage_round_trips(tmp_path: Path) -> None:
    store = Store(tmp_path / ".bugbout")
    report = MiningReport(examined=3, fix_shaped=2, rejected={"no test": 1})
    oracle = Oracle("abc", ("t::a",), ("t::b",))
    matrix = Matrix("abc", [BASE], {}, outcomes={BASE: {"t::a": "failed"}})
    verdict = Verdict("abc", "majority", None, "no candidate")
    resolution = Resolution.unresolved("abc", "codex", oracle, "no fix produced")

    store.save_tasks([TASK], report)
    store.save_oracles({"abc": oracle})
    store.save_matrix(matrix, "inputs-1")
    store.save_verdicts([verdict])
    store.save_resolutions({"abc/codex/0": resolution})
    store.save_agents({"codex": {"model": "gpt-5"}})

    assert store.tasks() == [TASK]
    assert store.mining() == {"examined": 3, "fix_shaped": 2, "rejected": {"no test": 1}}
    assert store.oracles() == {"abc": oracle}
    assert store.matrix("abc") == (matrix, "inputs-1")
    assert store.verdicts() == [verdict]
    assert store.resolutions() == {"abc/codex/0": resolution}
    assert store.agents() == {"codex": {"model": "gpt-5"}}


def test_results_ignore_themselves_in_git(tmp_path: Path) -> None:
    store = Store(tmp_path / ".bugbout")
    store.save_verdicts([])
    assert (tmp_path / ".bugbout" / ".gitignore").read_text() == "*\n"


def test_stages_not_yet_run_read_as_empty(tmp_path: Path) -> None:
    store = Store(tmp_path / ".bugbout")
    assert store.oracles() == {} and store.verdicts() == [] and store.resolutions() == {}
    assert store.matrix("abc") is None and store.attempts("fix") == []


def test_a_damaged_file_is_reported_not_misread(tmp_path: Path) -> None:
    store = Store(tmp_path / ".bugbout")
    store.save_verdicts([])
    (tmp_path / ".bugbout" / "verdicts.json").write_text("{not json")
    with pytest.raises(ArtifactError, match="not valid JSON"):
        store.verdicts()
