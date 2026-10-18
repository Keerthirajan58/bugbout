"""CLI smoke tests. The commands must work before any repo is prepared."""

from __future__ import annotations

from typer.testing import CliRunner

from verdict import __version__
from verdict.cli import app

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_targets_list_shows_both_pinned_targets() -> None:
    result = runner.invoke(app, ["targets", "list"])
    assert result.exit_code == 0
    assert "marshmallow" in result.stdout
    assert "arrow" in result.stdout


def test_doctor_runs_without_credentials() -> None:
    """`doctor` exists to diagnose a bare machine, so it must never require one."""
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "python" in result.stdout


def test_unknown_target_is_reported_not_traced() -> None:
    result = runner.invoke(app, ["targets", "prepare", "nonexistent"])
    assert result.exit_code != 0


def test_bare_invocation_shows_help() -> None:
    result = runner.invoke(app, [])
    assert "verdict" in result.stdout.lower()
