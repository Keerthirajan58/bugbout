"""``bugbout.toml`` is validated up front, and becomes agents, rules and paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from bugbout.agents import ClaudeCode, Codex, CommandAgent
from bugbout.errors import ConfigError, RepositoryError
from bugbout.project import Project

MINIMAL = '[project]\nsource = "src/toy"\n'


def _project(tmp_path: Path, text: str) -> Project:
    (tmp_path / "bugbout.toml").write_text(text)
    return Project.load(tmp_path)


class TestLoading:
    def test_a_minimal_file_gets_every_default(self, tmp_path: Path) -> None:
        config = _project(tmp_path, MINIMAL).config
        assert (config.project.source, config.project.tests) == ("src/toy", "tests")
        assert config.tasks.limit == 20 and config.run.sandbox == "auto"
        assert config.judge.policy == "majority" and config.contestants == ()

    def test_keys_are_dashed_as_in_pyproject(self, tmp_path: Path) -> None:
        text = (
            MINIMAL
            + 'test-dependencies = ["pytest", "hypothesis"]\n[tasks]\nmax-source-files = 3\n'
        )
        config = _project(tmp_path, text).config
        assert config.project.test_dependencies == ("pytest", "hypothesis")
        assert config.tasks.max_source_files == 3

    def test_every_problem_is_reported_with_where_it_is(self, tmp_path: Path) -> None:
        text = '[project]\nsorce = "src/toy"\n[tasks]\nlimit = 0\n'
        with pytest.raises(ConfigError) as caught:
            _project(tmp_path, text)
        message = str(caught.value)
        assert "project.source: Field required" in message
        assert "project.sorce: Extra inputs are not permitted" in message
        assert "tasks.limit" in message

    def test_a_missing_file_points_at_init(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="bugbout init"):
            Project.load(tmp_path)

    def test_invalid_toml_is_a_configuration_error(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="not valid TOML"):
            _project(tmp_path, "[project\n")

    def test_a_config_file_path_works_as_well_as_its_directory(self, tmp_path: Path) -> None:
        (tmp_path / "custom.toml").write_text(MINIMAL)
        assert Project.load(tmp_path / "custom.toml").root == tmp_path.resolve()


class TestAgents:
    def test_contestants_become_configured_agents(self, tmp_path: Path) -> None:
        text = (
            MINIMAL
            + '[[contestants]]\nagent = "codex"\nmodel = "gpt-5"\nreasoning-effort = "high"\n'
            + '[[contestants]]\nagent = "claude-code"\n'
            + '[[contestants]]\nagent = "command"\nname = "aider"\ncommand = ["aider"]\n'
        )
        codex, claude, aider = _project(tmp_path, text).contestants()
        assert isinstance(codex, Codex) and codex.reasoning_effort == "high"
        assert isinstance(claude, ClaudeCode) and claude.name == "claude-code"
        assert isinstance(aider, CommandAgent) and aider.argv == ["aider"]

    def test_examiners_carry_their_assignment(self, tmp_path: Path) -> None:
        text = MINIMAL + '[[examiners]]\nagent = "codex"\nassignment = "specify"\n'
        [examiner] = _project(tmp_path, text).examiners()
        assert examiner.assignment.name == "specify" and examiner.author == "specify/codex"

    def test_two_agents_with_one_name_are_refused(self, tmp_path: Path) -> None:
        text = MINIMAL + '[[contestants]]\nagent = "codex"\n' * 2
        with pytest.raises(ConfigError, match="both called 'codex'"):
            _project(tmp_path, text).contestants()

    def test_an_option_the_adapter_does_not_take_is_refused(self, tmp_path: Path) -> None:
        text = MINIMAL + '[[contestants]]\nagent = "codex"\nreasoning-efort = "high"\n'
        with pytest.raises(ConfigError, match="invalid options"):
            _project(tmp_path, text).contestants()


class TestPaths:
    def test_a_local_repository_resolves_against_the_config_file(self, tmp_path: Path) -> None:
        project = _project(tmp_path, MINIMAL)
        assert project.repository_url is None
        with pytest.raises(RepositoryError, match="not a git repository"):
            project.open_repository()

    @pytest.mark.parametrize(
        "location", ["https://github.com/owner/name.git", "git@github.com:owner/name.git"]
    )
    def test_a_url_is_cloned_into_the_cache(self, tmp_path: Path, location: str) -> None:
        project = _project(tmp_path, f'[project]\nsource = "x"\nrepository = "{location}"\n')
        assert project.repository_url == location

    def test_results_stay_with_the_project_and_state_goes_to_the_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BUGBOUT_HOME", str(tmp_path / "home"))
        (tmp_path / "repo").mkdir()
        project = _project(tmp_path / "repo", MINIMAL)
        assert project.results_dir == project.root / ".bugbout"
        assert project.cache_dir.parent == (tmp_path / "home").resolve()
        assert project.cache_dir.name.startswith("repo-")

    def test_settings_become_the_test_environment_and_mining_rules(self, tmp_path: Path) -> None:
        text = (
            MINIMAL
            + 'pytest-args = ["-x"]\ninterpreter = "/usr/bin/python3"\n[tasks]\nsince = "2024-01-01"\n'
        )
        project = _project(tmp_path, text)
        environment = project.python_project()
        assert environment.pytest_args == ("-x",) and environment.interpreter == "/usr/bin/python3"
        rules = project.mining_rules()
        assert (rules.source_root, rules.test_root, rules.since) == (
            "src/toy",
            "tests",
            "2024-01-01",
        )
