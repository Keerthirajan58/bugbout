"""``bugbout doctor`` checks each thing a bout needs, and names each agent by its role."""

from __future__ import annotations

from pathlib import Path

from bugbout.doctor import diagnose
from bugbout.project import Project

REMOTE = '[project]\nsource = "pkg"\nrepository = "https://github.com/owner/name.git"\n'
AGENT = 'agent = "command"\nname = "a"\ncommand = ["true"]\n'


def _checks(tmp_path: Path, text: str) -> dict[str, bool]:
    (tmp_path / "bugbout.toml").write_text(text)
    return {check.name: check.ok for check in diagnose(Project.load(tmp_path))}


def test_contestants_and_examiners_are_told_apart(tmp_path: Path) -> None:
    checks = _checks(tmp_path, f"{REMOTE}[[contestants]]\n{AGENT}[[examiners]]\n{AGENT}")
    assert checks["a"] and checks["reproduce/a"]
    assert checks["repository"]  # a remote repository is cloned on the first run


def test_a_bout_without_contestants_fails_the_check(tmp_path: Path) -> None:
    assert _checks(tmp_path, REMOTE)["agents"] is False


def test_a_configuration_error_is_a_failed_check(tmp_path: Path) -> None:
    checks = _checks(tmp_path, f'{REMOTE}[[contestants]]\nagent = "nope"\n')
    assert checks["agents"] is False
