"""Running a checkout's tests: outcomes, isolation, and the runner agents call."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from bugbout.errors import EnvironmentSetupError
from bugbout.sandbox import Bubblewrap, Seatbelt, detect
from bugbout.workspace.environment import PythonProject, TestEnvironment, distribution_name

NATIVE = Seatbelt.available() or Bubblewrap.available()
PASSING = "def test_ok():\n    assert True\n"
FAILING = "def test_bad():\n    assert False\n"


def _tree(root: Path, tests: str) -> Path:
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "__init__.py").write_text("VALUE = 7\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_it.py").write_text(tests)
    return root


def _environment(
    tmp_path: Path, *, env: dict[str, str] | None = None, interpreter: str = sys.executable
) -> TestEnvironment:
    project = PythonProject(
        source_root="src/pkg", test_root="tests", env=env or {}, interpreter=interpreter
    )
    return TestEnvironment(tmp_path / "env", project)


class TestRun:
    @pytest.mark.parametrize(
        ("tests", "outcome"),
        [(PASSING, "passed"), (FAILING, "failed"), ("import missing_module\n", "error")],
    )
    def test_pytest_exit_codes_become_outcomes(
        self, tmp_path: Path, tests: str, outcome: str
    ) -> None:
        tree = _tree(tmp_path / "tree", tests)
        assert _environment(tmp_path).run(tree, ["tests/test_it.py"]).outcome == outcome

    def test_the_checkout_s_own_package_is_imported(self, tmp_path: Path) -> None:
        tests = "from pkg import VALUE\n\n\ndef test_value():\n    assert VALUE == 7\n"
        tree = _tree(tmp_path / "tree", tests)
        assert _environment(tmp_path).run(tree, ["tests"]).outcome == "passed"

    def test_secrets_in_the_caller_s_environment_stay_out(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SOME_API_KEY", "secret")
        tests = "import os\n\n\ndef test_env():\n    assert 'SOME_API_KEY' not in os.environ\n"
        tree = _tree(tmp_path / "tree", tests)
        assert _environment(tmp_path).run(tree, ["tests"]).outcome == "passed"

    def test_variables_the_project_declares_are_passed_in(self, tmp_path: Path) -> None:
        tests = "import os\n\n\ndef test_env():\n    assert os.environ['DB'] == 'sqlite://'\n"
        tree = _tree(tmp_path / "tree", tests)
        environment = _environment(tmp_path, env={"DB": "sqlite://"})
        assert environment.run(tree, ["tests"]).outcome == "passed"

    def test_a_hung_suite_times_out(self, tmp_path: Path) -> None:
        tree = _tree(tmp_path / "tree", "import time\n\n\ndef test_slow():\n    time.sleep(30)\n")
        assert _environment(tmp_path).run(tree, ["tests"], timeout=1).outcome == "timeout"

    @pytest.mark.skipif(not NATIVE, reason="no native sandbox on this machine")
    def test_a_sandboxed_run_cannot_write_outside_its_tree(self, tmp_path: Path) -> None:
        outside = Path(__file__).resolve().parent / "must-not-exist"
        tests = (
            "import pathlib\n\n\ndef test_escape():\n"
            f"    pathlib.Path({str(outside)!r}).write_text('x')\n"
        )
        tree = _tree(tmp_path / "tree", tests)
        environment = TestEnvironment(tmp_path / "env", _environment(tmp_path).project, detect())
        assert environment.run(tree, ["tests"]).outcome == "failed"
        assert not outside.exists()

    def test_junit_records_each_test(self, tmp_path: Path) -> None:
        tree = _tree(tmp_path / "tree", PASSING + "\n\n" + FAILING)
        junit = tmp_path / "report.xml"
        _environment(tmp_path).run(tree, ["tests"], junit=junit)
        assert junit.read_text().count("<testcase") == 2


class TestRunner:
    def test_the_runner_runs_the_bound_tree_from_anywhere(self, tmp_path: Path) -> None:
        tree = _tree(tmp_path / "it's a tree", PASSING)
        runner = _environment(tmp_path).write_runner(tree, tmp_path / "bin" / "run-tests")
        completed = subprocess.run(
            [str(runner), str(tree / "tests")], cwd=tmp_path, capture_output=True, check=False
        )
        assert completed.returncode == 0, completed.stdout


class TestEnsure:
    def test_a_missing_interpreter_is_reported(self, tmp_path: Path) -> None:
        environment = _environment(tmp_path, interpreter=str(tmp_path / "nope" / "python"))
        with pytest.raises(EnvironmentSetupError, match="does not exist"):
            environment.ensure(tmp_path)


@pytest.mark.parametrize(
    ("pyproject", "name"),
    [
        ('[project]\nname = "mypkg"\n', "mypkg"),
        ("[tool.other]\nx = 1\n", None),
        ("not toml [", None),
    ],
)
def test_the_distribution_name_comes_from_pyproject(
    tmp_path: Path, pyproject: str, name: str | None
) -> None:
    (tmp_path / "pyproject.toml").write_text(pyproject)
    assert distribution_name(tmp_path) == name


def test_a_project_without_pyproject_has_no_distribution_name(tmp_path: Path) -> None:
    assert distribution_name(tmp_path) is None
