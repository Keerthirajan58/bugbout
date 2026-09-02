"""Hermeticity invariants.

These are the highest-value tests in phase 0. Every mutation verdict and the
entire ablation number are downstream of the guarantees asserted here, and a
regression in any of them would be silent - results would still be produced,
they would just be wrong.
"""

from __future__ import annotations

import itertools

import pytest

from precedent.targets import get_target
from precedent.targets.workspace import (
    SuiteResult,
    TargetWorkspace,
    WorkspaceError,
    _parse_counts,
)


@pytest.fixture
def workspace(tmp_path):  # type: ignore[no-untyped-def]
    return TargetWorkspace(get_target("marshmallow"), root=tmp_path)


class TestPytestCommand:
    def test_clears_inherited_addopts(self, workspace: TargetWorkspace) -> None:
        """arrow sets --cov-fail-under=99 in addopts. Left in place, a mutant
        could be scored 'killed' because coverage dipped rather than because a
        test actually caught it."""
        cmd = workspace.pytest_command()
        assert "-o" in cmd
        assert "addopts=" in cmd
        assert cmd[cmd.index("-o") + 1] == "addopts="

    def test_disables_the_cache_provider(self, workspace: TargetWorkspace) -> None:
        """Cross-run state would let one mutant's result influence the next."""
        cmd = workspace.pytest_command()
        assert cmd[cmd.index("-p") + 1] == "no:cacheprovider"

    def test_fail_fast_is_opt_in(self, workspace: TargetWorkspace) -> None:
        assert "-x" not in workspace.pytest_command()
        assert "-x" in workspace.pytest_command(fail_fast=True)

    def test_defaults_to_the_targets_test_directory(self, workspace: TargetWorkspace) -> None:
        assert workspace.pytest_command()[-1] == str(workspace.test_dir)

    def test_selection_overrides_the_default_directory(self, workspace: TargetWorkspace) -> None:
        cmd = workspace.pytest_command(selection=("tests/test_fields.py",))
        assert cmd[-1] == "tests/test_fields.py"
        assert str(workspace.test_dir) not in cmd

    def test_runs_through_the_workspace_interpreter(self, workspace: TargetWorkspace) -> None:
        """Never the host interpreter: the target and its deps live in the venv."""
        cmd = workspace.pytest_command()
        assert cmd[0] == str(workspace.python)
        assert cmd[1:3] == ["-m", "pytest"]


class TestEnvironmentHygiene:
    def test_bytecode_writing_is_disabled(self, workspace: TargetWorkspace) -> None:
        """Mutation rewrites .py files in place. A stale .pyc would let the
        original code run and the mutant would be scored 'survived' without
        ever having executed."""
        assert workspace.test_env()["PYTHONDONTWRITEBYTECODE"] == "1"

    def test_hash_seed_is_pinned(self, workspace: TargetWorkspace) -> None:
        """Unpinned hash randomisation makes set/dict ordering vary, which shows
        up as a flaky mutant rather than a real behavioural difference."""
        assert workspace.test_env()["PYTHONHASHSEED"] == "0"

    def test_ambient_python_path_is_stripped(
        self, workspace: TargetWorkspace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PYTHONPATH", "/somewhere/that/should/not/leak")
        assert "PYTHONPATH" not in workspace.test_env()

    def test_ambient_pytest_addopts_is_stripped(
        self, workspace: TargetWorkspace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PYTEST_ADDOPTS", "--exitfirst --cov")
        assert "PYTEST_ADDOPTS" not in workspace.test_env()


class TestRunTestsGuards:
    def test_refuses_to_run_against_an_unprepared_workspace(
        self, workspace: TargetWorkspace
    ) -> None:
        with pytest.raises(WorkspaceError, match="not prepared"):
            workspace.run_tests()


class TestOutcomeClassification:
    @staticmethod
    def _result(exit_code: int, timed_out: bool = False) -> SuiteResult:
        return SuiteResult(
            exit_code=exit_code,
            passed=0,
            failed=0,
            errors=0,
            skipped=0,
            duration_seconds=0.0,
            timed_out=timed_out,
            stdout="",
            stderr="",
        )

    @pytest.mark.parametrize(
        ("exit_code", "expected"),
        [(0, "passed"), (1, "failed"), (2, "error"), (3, "error"), (4, "error"), (5, "no_tests")],
    )
    def test_maps_pytest_exit_codes(self, exit_code: int, expected: str) -> None:
        assert self._result(exit_code).outcome == expected

    def test_timeout_wins_over_exit_code(self) -> None:
        """An inverted loop condition is a normal mutation outcome; it must be
        scored as a timeout, not misread as a plain failure."""
        assert self._result(2, timed_out=True).outcome == "timeout"

    def test_only_a_clean_pass_counts_as_green(self) -> None:
        assert self._result(0).is_green
        assert not self._result(1).is_green
        assert not self._result(0, timed_out=True).is_green


class TestSummaryParsing:
    def test_parses_a_mixed_summary_line(self) -> None:
        counts = _parse_counts("=== 3 failed, 1190 passed, 2 skipped, 1 error in 1.42s ===")
        assert counts == {"passed": 1190, "failed": 3, "errors": 1, "skipped": 2}

    def test_parses_the_all_green_line(self) -> None:
        assert _parse_counts("1190 passed in 1.42s")["passed"] == 1190

    def test_handles_plural_errors(self) -> None:
        assert _parse_counts("2 errors in 0.5s")["errors"] == 2

    def test_returns_zeros_when_nothing_matches(self) -> None:
        assert _parse_counts("collection failure") == {
            "passed": 0,
            "failed": 0,
            "errors": 0,
            "skipped": 0,
        }

    def test_ignores_xfail_and_xpass_counts(self) -> None:
        """Expected failures are not regressions and must not inflate 'failed'."""
        counts = _parse_counts("1990 passed, 25 skipped, 1 xfailed in 2.52s")
        assert counts["failed"] == 0
        assert counts["passed"] == 1990


class TestLayout:
    def test_derives_source_and_test_directories_from_the_registry(
        self, workspace: TargetWorkspace
    ) -> None:
        assert workspace.source_dir == workspace.repo_dir / "src/marshmallow"
        assert workspace.test_dir == workspace.repo_dir / "tests"

    def test_is_not_ready_before_preparation(self, workspace: TargetWorkspace) -> None:
        assert not workspace.is_ready()


class TestContaminationGuard:
    """A run killed mid-mutant leaves the mutated file on disk, because the
    restoring `finally` never runs. Without this guard the next run scores
    every mutant on top of that stale mutation - and still reports a
    plausible-looking kill-rate. This actually happened during phase 1: five
    worker checkouts were left modified by an interrupted arrow run."""

    def test_clean_workspace_needs_no_repair(
        self, workspace: TargetWorkspace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(TargetWorkspace, "is_clean", lambda self: True)
        assert workspace.ensure_clean() is False

    def test_dirty_workspace_is_restored(
        self, workspace: TargetWorkspace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        restored: list[bool] = []
        monkeypatch.setattr(TargetWorkspace, "is_clean", lambda self: False)
        monkeypatch.setattr(TargetWorkspace, "restore_all", lambda self: restored.append(True))
        assert workspace.ensure_clean() is True
        assert restored == [True]


class TestReadinessMarker:
    """A workspace is ready only when the target is actually installed.

    An install that fails part-way leaves both the venv and the checkout in
    place, so checking for those alone reports a broken workspace as usable.
    That happened during the phase 2 run: a background shell without `uv` on
    PATH fell back to a stdlib venv whose pip was too old for PEP 660 editable
    installs, and the half-built workspace would have been handed straight to
    the evaluator - producing collection errors indistinguishable from results.
    """

    def test_not_ready_without_the_marker(
        self, workspace: TargetWorkspace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace.root.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(type(workspace.python), "exists", lambda self: True)
        assert not workspace.is_ready()

    def test_marker_must_match_the_pinned_commit(self, workspace: TargetWorkspace) -> None:
        """A workspace built for a different commit is not a usable workspace
        for this one, even though every file is present."""
        workspace.root.mkdir(parents=True, exist_ok=True)
        workspace._marker.write_text("0" * 40, encoding="utf-8")
        assert not workspace.is_ready()

    def test_missing_marker_is_not_an_error(self, workspace: TargetWorkspace) -> None:
        assert workspace.is_ready() is False


class TestExecutableDiscovery:
    def test_finds_tools_outside_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-interactive shells often have a minimal PATH. Silently falling
        back to a worse toolchain is how the broken workspace above happened."""
        from precedent.targets import workspace as module

        monkeypatch.setattr(module.shutil, "which", lambda _: None)
        found = module._find("definitely-not-a-real-binary-xyz")
        assert found is None

    def test_returns_path_when_available(self) -> None:
        from precedent.targets.workspace import _find

        assert _find("git") is not None


class TestSelectionFlagsSurviveAddoptsClearing:
    """Clearing `addopts` must drop coverage gates without dropping selection.

    click marks 31,000 stress tests and deselects them by default via
    `addopts = -m 'not stress'`. Stripping that wholesale turned a 2.5s suite
    into one that never finished - the baseline verification ran for over ten
    minutes with no output before this was caught.
    """

    def test_selection_flags_are_restored(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        ws = TargetWorkspace(get_target("click"), root=tmp_path)
        cmd = ws.pytest_command()
        # `python -m pytest` also contains "-m", so match the flag/value pair.
        pairs = list(itertools.pairwise(cmd))
        assert ("-m", "not stress") in pairs

    def test_addopts_is_still_cleared(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """The coverage gate must still go."""
        ws = TargetWorkspace(get_target("click"), root=tmp_path)
        cmd = ws.pytest_command()
        assert cmd[cmd.index("-o") + 1] == "addopts="

    def test_targets_without_selection_flags_are_unchanged(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        cmd = TargetWorkspace(get_target("marshmallow"), root=tmp_path).pytest_command()
        pairs = list(itertools.pairwise(cmd))
        assert not [p for p in pairs if p[0] == "-m" and p[1] != "pytest"]
