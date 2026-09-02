"""Tests for the real-diff study's integrity guarantees.

Every assertion here protects a property the study's numbers depend on. If any
of them regress, the study still produces numbers - they are simply no longer
evidence, which is the failure mode worth catching automatically.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from precedent.review.bench import AgentDiff, GroundTruth, TaskBench
from precedent.review.tasks import GitHubText, IssueTask, mine_tasks, strip_solution
from precedent.targets import get_target


class TestStripSolution:
    """The agent must never be shown how the bug was actually fixed.

    Pull-request descriptions routinely carry both the bug report and the
    patch. Handing the second half to the agent would make every downstream
    number meaningless, and it would look exactly like a good result.
    """

    def test_a_solution_section_is_removed(self) -> None:
        body = "## Problem\nIt crashes on empty input.\n\n## Solution\nGuard with `if not x`.\n"
        cleaned, redacted = strip_solution(body)
        assert redacted
        assert "It crashes" in cleaned
        assert "Guard with" not in cleaned

    def test_the_report_after_a_solution_section_survives(self) -> None:
        """Stripping must stop at the next heading, not run to the end."""
        body = "## Fix\nchange the regex\n\n## Steps to reproduce\ncall f('')\n"
        cleaned, _ = strip_solution(body)
        assert "change the regex" not in cleaned
        assert "call f('')" in cleaned

    def test_a_fenced_diff_is_removed(self) -> None:
        body = "Broken since 4.2.\n\n```diff\n-  return a\n+  return b\n```\n"
        cleaned, redacted = strip_solution(body)
        assert redacted
        assert "return b" not in cleaned
        assert "Broken since 4.2." in cleaned

    def test_a_plain_bug_report_is_left_alone(self) -> None:
        body = "`validate.URL()` rejects `https://example.com#frag`.\n\nPer RFC 3986 that is valid."
        cleaned, redacted = strip_solution(body)
        assert not redacted
        assert cleaned == body.strip()

    def test_a_reproduction_code_block_is_kept(self) -> None:
        """Only diff and patch fences are stripped. A python reproduction is
        the most useful thing a report contains and must survive."""
        body = "Fails:\n\n```python\nvalidate.URL()('https://x.com#f')\n```\n"
        cleaned, redacted = strip_solution(body)
        assert not redacted
        assert "validate.URL()" in cleaned


class TestMiningFilters:
    """A commit is only a task if it can be scored."""

    @staticmethod
    def _repo(tmp_path: Path, subject: str, *, with_test: bool) -> Path:
        repo = tmp_path / "repo"
        (repo / "src" / "pkg").mkdir(parents=True)
        (repo / "tests").mkdir()
        run = lambda *a: subprocess.run(  # noqa: E731
            ["git", *a], cwd=repo, capture_output=True, check=True
        )
        run("init", "-q")
        run("config", "user.email", "t@example.com")
        run("config", "user.name", "t")
        (repo / "src" / "pkg" / "mod.py").write_text("def f():\n    return 1\n")
        (repo / "tests" / "test_mod.py").write_text("def test_old():\n    assert True\n")
        run("add", "-A")
        run("commit", "-qm", "initial")
        (repo / "src" / "pkg" / "mod.py").write_text("def f():\n    return 2\n")
        if with_test:
            (repo / "tests" / "test_mod.py").write_text(
                "def test_old():\n    assert True\n\n\ndef test_new():\n    assert True\n"
            )
        run("add", "-A")
        run("commit", "-qm", subject)
        return repo

    @staticmethod
    def _github(tmp_path: Path) -> GitHubText:
        cache = tmp_path / "gh"
        cache.mkdir()
        (cache / "o_r.42.json").write_text(
            json.dumps(
                {"title": "f returns the wrong number", "body": "f() returns 1, expected 2. " * 4}
            )
        )
        return GitHubText(None, cache)

    def _mine(self, repo: Path, tmp_path: Path) -> tuple[list[IssueTask], object]:
        return mine_tasks(
            repo,
            target_slug="t",
            full_name="o/r",
            github=self._github(tmp_path),
            since="1990-01-01",
        )

    def test_a_fix_that_shipped_a_new_test_becomes_a_task(self, tmp_path: Path) -> None:
        tasks, _ = self._mine(
            self._repo(tmp_path, "Fix wrong return (#42)", with_test=True), tmp_path
        )
        assert len(tasks) == 1
        assert tasks[0].oracle_tests == ("test_new",)

    def test_a_fix_without_a_new_test_is_dropped(self, tmp_path: Path) -> None:
        """Without a test the maintainer wrote, there is no oracle, and the
        agent's diff cannot be scored by anything but opinion."""
        tasks, report = self._mine(
            self._repo(tmp_path, "Fix wrong return (#42)", with_test=False), tmp_path
        )
        assert tasks == []
        assert report.rejected  # type: ignore[attr-defined]

    def test_the_base_commit_is_the_parent_of_the_fix(self, tmp_path: Path) -> None:
        """The agent works from the commit immediately before the fix. Off by
        one here and it would be handed the answer."""
        repo = self._repo(tmp_path, "Fix wrong return (#42)", with_test=True)
        task = self._mine(repo, tmp_path)[0][0]
        parent = subprocess.run(
            ["git", "rev-parse", f"{task.fix_commit}^"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert task.base_commit == parent


class TestTaskCheckout:
    """The agent must not be able to read the future."""

    def test_a_materialised_checkout_has_no_git_history(self, tmp_path: Path) -> None:
        """`git archive` rather than `git checkout`, so the fix commit and the
        maintainer's test are absent from the filesystem entirely - not merely
        out of reach behind an instruction."""
        repo = TestMiningFilters._repo(tmp_path, "Fix wrong return (#42)", with_test=True)
        task = mine_tasks(
            repo,
            target_slug="marshmallow",
            full_name="o/r",
            github=TestMiningFilters._github(tmp_path),
            since="1990-01-01",
        )[0][0]
        bench = TaskBench(get_target("marshmallow"), repo)
        bench.root = tmp_path / "bench"
        work = bench.materialise(task)
        assert work.exists()
        assert not (work / ".git").exists()
        assert (work / "src" / "pkg" / "mod.py").read_text() == "def f():\n    return 1\n"

    def test_the_pristine_copy_matches_the_working_tree(self, tmp_path: Path) -> None:
        """The diff is measured against this copy, so it must start identical."""
        repo = TestMiningFilters._repo(tmp_path, "Fix wrong return (#42)", with_test=True)
        task = mine_tasks(
            repo,
            target_slug="marshmallow",
            full_name="o/r",
            github=TestMiningFilters._github(tmp_path),
            since="1990-01-01",
        )[0][0]
        bench = TaskBench(get_target("marshmallow"), repo)
        bench.root = tmp_path / "bench"
        bench.materialise(task)
        patch, changed, touched = bench.diff_of(task)
        assert patch.strip() == ""
        assert changed == ()
        assert not touched


class TestAgentDiffRecord:
    def test_an_empty_patch_is_recognised(self) -> None:
        assert AgentDiff("t", "claude-code", "   \n", (), False).empty

    def test_a_round_trip_preserves_the_record(self) -> None:
        diff = AgentDiff("t", "claude-code", "--- a\n+++ b\n", ("src/x.py",), False, note="n")
        assert AgentDiff.from_dict(diff.to_dict()) == diff

    def test_ground_truth_requires_both_conditions(self) -> None:
        """A diff that fixes the bug but breaks the suite has not resolved the
        issue, and neither has one that leaves the suite green without fixing
        anything."""
        assert not GroundTruth("t", "passed", "failed", False).resolved
        assert not GroundTruth("t", "failed", "passed", False).resolved


class TestReporting:
    """The report generator's only job is to never invent a number.

    This project's first version failed in exactly the available way: targets
    written down before measurement, then reported as though measured. These
    tests are the mechanical guard against that happening again.
    """

    @staticmethod
    def _isolate(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.setenv("PRECEDENT_DATA_DIR", str(tmp_path))

    def test_an_unmeasured_section_says_so(self, monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        """With no artifacts at all, every section must decline to report rather
        than print zeros - a zero reads as a measurement."""
        from precedent.reporting import MISSING, build_report

        self._isolate(monkeypatch, tmp_path)
        document = build_report("marshmallow")
        assert document.count(MISSING) >= 2
        assert "0.0%" not in document

    def test_a_run_predating_the_gate_counter_shows_a_dash(self) -> None:
        """Not 0. A bare zero there would read as "no candidate was ever wrong
        about the library", which is the opposite of what those runs showed."""
        from precedent.reporting import _gate_total

        assert _gate_total([{"mutant_id": "a"}, {"mutant_id": "b"}]) == "-"

    def test_a_run_that_counted_the_gate_shows_the_total(self) -> None:
        from precedent.reporting import _gate_total

        assert _gate_total([{"gate_failures": 2}, {"gate_failures": 0}]) == "2"

    def test_splicing_replaces_only_the_generated_block(self, tmp_path: Path) -> None:
        from precedent.reporting import splice_into

        document = tmp_path / "report.md"
        document.write_text(
            "intro\n\n<!-- results:begin -->\nstale\n<!-- results:end -->\n\noutro\n"
        )
        splice_into(document, "| a | b |")
        text = document.read_text()
        assert "intro" in text
        assert "outro" in text
        assert "stale" not in text
        assert "| a | b |" in text

    def test_splicing_a_report_with_no_block_is_an_error(self, tmp_path: Path) -> None:
        """Silently appending would produce a report whose numbers are never
        refreshed, which is worse than failing."""
        from precedent.reporting import splice_into

        document = tmp_path / "report.md"
        document.write_text("no markers here\n")
        with pytest.raises(ValueError, match="results:begin"):
            splice_into(document, "| a | b |")
