"""``bugbout init`` reads the repository's layout and writes a working ``bugbout.toml``."""

from __future__ import annotations

from pathlib import Path

import pytest

from bugbout import scaffold
from bugbout.config import load_config
from bugbout.scaffold import Layout, detect_layout, installed_agents, render_config


def _package(root: Path, *parts: str) -> None:
    folder = root.joinpath(*parts)
    folder.mkdir(parents=True)
    (folder / "__init__.py").write_text("")


class TestDetectLayout:
    def test_a_src_layout(self, tmp_path: Path) -> None:
        _package(tmp_path, "src", "mypkg")
        (tmp_path / "tests").mkdir()
        assert detect_layout(tmp_path) == Layout("src/mypkg", "tests", ("pytest",))

    def test_a_flat_layout_ignores_folders_that_are_not_the_package(self, tmp_path: Path) -> None:
        _package(tmp_path, "mypkg")
        _package(tmp_path, "docs")
        _package(tmp_path, "test")
        assert detect_layout(tmp_path).source == "mypkg"
        assert detect_layout(tmp_path).tests == "test"

    def test_several_packages_are_settled_by_the_distribution_name(self, tmp_path: Path) -> None:
        _package(tmp_path, "src", "helpers")
        _package(tmp_path, "src", "my_pkg")
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "my-pkg"\n')
        assert detect_layout(tmp_path).source == "src/my_pkg"

    def test_tests_inside_the_package(self, tmp_path: Path) -> None:
        _package(tmp_path, "mypkg")
        (tmp_path / "mypkg" / "tests").mkdir()
        assert detect_layout(tmp_path).tests == "mypkg/tests"

    def test_nothing_recognisable(self, tmp_path: Path) -> None:
        assert detect_layout(tmp_path) == Layout(None, None, ("pytest",))

    def test_test_dependencies_come_from_extras_and_groups(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            "[project.optional-dependencies]\n"
            'test = ["hypothesis>=6", "pytest"]\n'
            "[dependency-groups]\n"
            'tests = ["freezegun", {include-group = "test"}]\n'
        )
        assert detect_layout(tmp_path).test_dependencies == ("pytest", "hypothesis>=6", "freezegun")


def test_the_rendered_config_loads_and_races_the_installed_agents(tmp_path: Path) -> None:
    layout = Layout("src/mypkg", "tests", ("pytest", "hypothesis"))
    (tmp_path / "bugbout.toml").write_text(render_config(layout, ["codex", "claude-code"]))
    config = load_config(tmp_path / "bugbout.toml")
    assert config.project.source == "src/mypkg"
    assert config.project.test_dependencies == ("pytest", "hypothesis")
    assert [c.agent for c in config.contestants] == ["codex", "claude-code"]


def test_without_installed_agents_the_example_stays_commented(tmp_path: Path) -> None:
    (tmp_path / "bugbout.toml").write_text(render_config(Layout("pkg", "tests", ("pytest",)), []))
    assert load_config(tmp_path / "bugbout.toml").contestants == ()


def test_installed_agents_are_the_ones_on_the_path(monkeypatch: pytest.MonkeyPatch) -> None:
    present = {"codex", "gemini"}
    monkeypatch.setattr(scaffold, "find_executable", lambda name: name if name in present else None)
    assert installed_agents() == ["codex", "gemini-cli"]
