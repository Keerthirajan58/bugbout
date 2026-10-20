"""The command line, end to end on the toy project: init, doctor, tasks, run and report."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bugbout.__about__ import __version__
from bugbout.cli import app
from bugbout.project import Project
from tests.toy import FIXED, ISSUE, REGRESSION, Toy

runner = CliRunner()


def _invoke(*args: str) -> tuple[int, str]:
    result = runner.invoke(app, list(args))
    return result.exit_code, result.output


def _script(files: dict[str, str]) -> str:
    lines = ["import pathlib, sys", "sys.stdin.read()"]
    lines += [f"pathlib.Path({p!r}).write_text({t!r})" for p, t in files.items()]
    return "\n".join(lines)


@pytest.fixture
def configured(toy: Toy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The toy project with one scripted contestant, its issue already in the cache."""
    monkeypatch.setenv("BUGBOUT_HOME", str(tmp_path / "home"))
    fixer = [
        sys.executable,
        "-c",
        _script({"src/toy/core.py": FIXED, "tests/test_issue_regression.py": REGRESSION}),
    ]
    (toy.repo.path / "bugbout.toml").write_text(
        '[project]\nsource = "src/toy"\ngithub = "toy/toy"\n'
        f"interpreter = {json.dumps(sys.executable)}\n"
        '[run]\nsandbox = "none"\ntimeout = 120\n'
        f'[[contestants]]\nagent = "command"\nname = "fixer"\ncommand = {json.dumps(fixer)}\n'
        "sandboxed = false\n"
    )
    issues = Project.load(toy.repo.path).cache_dir / "issues"
    issues.mkdir(parents=True)
    payload = {"number": ISSUE.number, "title": ISSUE.title, "body": ISSUE.body}
    (issues / "toy_toy.12.json").write_text(json.dumps(payload))
    return toy.repo.path


def test_version() -> None:
    assert _invoke("version") == (0, f"bugbout {__version__}\n")


class TestInit:
    def test_it_writes_a_config_from_the_layout(self, tmp_path: Path) -> None:
        (tmp_path / "src" / "pkg").mkdir(parents=True)
        (tmp_path / "src" / "pkg" / "__init__.py").write_text("")
        code, output = _invoke("init", str(tmp_path))
        assert code == 0 and "src/pkg" in output
        assert Project.load(tmp_path).config.project.source == "src/pkg"

    def test_it_never_overwrites_without_force(self, tmp_path: Path) -> None:
        (tmp_path / "bugbout.toml").write_text("keep")
        assert _invoke("init", str(tmp_path), "--source", "pkg")[0] == 1
        assert (tmp_path / "bugbout.toml").read_text() == "keep"
        assert _invoke("init", str(tmp_path), "--source", "pkg", "--force")[0] == 0

    def test_without_a_package_it_asks_for_one(self, tmp_path: Path) -> None:
        code, output = _invoke("init", str(tmp_path))
        assert code == 1 and "--source" in output


def test_doctor_passes_when_everything_is_in_place(configured: Path) -> None:
    code, output = _invoke("doctor", "--config", str(configured))
    assert code == 0, output
    assert "issues from toy/toy" in output and "fixer" in output
    assert "unconfined" in output  # the "none" sandbox is a warning, not a failure


def test_doctor_fails_on_a_missing_agent(configured: Path) -> None:
    config = configured / "bugbout.toml"
    ghost = '[[contestants]]\nagent = "command"\nname = "ghost"\ncommand = ["no-such-agent"]\n'
    config.write_text(config.read_text() + ghost)
    code, output = _invoke("doctor", "--config", str(configured))
    assert code == 1 and "ghost" in output and "not installed" in output


def test_a_bad_config_is_one_line_not_a_traceback(tmp_path: Path) -> None:
    (tmp_path / "bugbout.toml").write_text("[project]\n")
    code, output = _invoke("report", "--config", str(tmp_path))
    assert code == 1 and output.startswith("error:") and "Traceback" not in output


def test_a_bout_from_the_command_line(configured: Path) -> None:
    code, output = _invoke("tasks", "--config", str(configured))
    assert code == 0 and "add() subtracts" in output and "1 failing test(s)" in output

    code, output = _invoke("run", "--config", str(configured))
    assert code == 0, output
    assert "fixer" in output and "1/1 (100%)" in output
    assert (configured / ".bugbout" / "report.md").exists()

    code, output = _invoke("report", "--config", str(configured), "--format", "json")
    assert code == 0 and json.loads(output)["agents"][0]["resolved"] == 1
    code, output = _invoke("report", "--config", str(configured), "--format", "markdown")
    assert code == 0 and output.startswith("# bugbout: toy/toy")
    assert _invoke("report", "--config", str(configured), "--format", "pdf")[0] == 1


def test_there_is_no_report_before_a_run(configured: Path) -> None:
    code, output = _invoke("report", "--config", str(configured))
    assert code == 1 and "no results yet" in output
