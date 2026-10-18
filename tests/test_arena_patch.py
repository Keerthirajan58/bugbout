"""The arena's patch format: measured from disk, applied strictly, portable."""

from __future__ import annotations

from pathlib import Path

import pytest

from verdict.arena.patch import Patch, PatchError
from verdict.arena.truth import read_junit


def _tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


def test_round_trip_reproduces_the_changed_tree(tmp_path: Path) -> None:
    before = _tree(
        tmp_path / "before",
        {"src/pkg/a.py": "one\ntwo\nthree\n", "src/pkg/gone.py": "x = 1\n"},
    )
    after = _tree(
        tmp_path / "after",
        {"src/pkg/a.py": "one\n2\nthree\nfour", "src/pkg/new.py": "y = 2\n"},
    )
    patch = Patch.between(before, after)
    assert set(patch.files) == {"src/pkg/a.py", "src/pkg/gone.py", "src/pkg/new.py"}

    patch.apply(before)
    assert Patch.between(before, after).empty
    # The missing final newline survives the round trip byte for byte.
    assert (before / "src/pkg/a.py").read_text() == "one\n2\nthree\nfour"


def test_paths_are_relative_so_a_space_in_the_root_cannot_matter(tmp_path: Path) -> None:
    before = _tree(tmp_path / "dir with space" / "b", {"m.py": "a\n"})
    after = _tree(tmp_path / "dir with space" / "a", {"m.py": "b\n"})
    diff = Patch.between(before, after).diff
    assert "--- a/m.py\n+++ b/m.py\n" in diff
    assert "space" not in diff


def test_a_mismatched_context_line_refuses_to_apply(tmp_path: Path) -> None:
    before = _tree(tmp_path / "before", {"m.py": "a\nb\nc\n"})
    after = _tree(tmp_path / "after", {"m.py": "a\nB\nc\n"})
    patch = Patch.between(before, after)
    drifted = _tree(tmp_path / "drifted", {"m.py": "a\nsomething else\nc\n"})
    with pytest.raises(PatchError):
        patch.apply(drifted)


def test_a_removed_line_that_looks_like_a_header_does_not_end_the_hunk(tmp_path: Path) -> None:
    before = _tree(tmp_path / "before", {"m.py": "x\n-- not a header\ny\n"})
    after = _tree(tmp_path / "after", {"m.py": "x\ny\n"})
    patch = Patch.between(before, after)
    patch.apply(before)
    assert (before / "m.py").read_text() == "x\ny\n"


def test_tool_state_and_bytecode_are_never_part_of_a_change(tmp_path: Path) -> None:
    before = _tree(tmp_path / "before", {"m.py": "a\n"})
    after = _tree(
        tmp_path / "after",
        {"m.py": "a\n", ".gemini/x.py": "z\n", "pkg/__pycache__/m.py": "z\n"},
    )
    assert Patch.between(before, after).empty


def test_fix_and_tests_separate_by_directory(tmp_path: Path) -> None:
    before = _tree(tmp_path / "before", {"src/m.py": "a\n", "tests/test_m.py": "t\n"})
    after = _tree(
        tmp_path / "after",
        {"src/m.py": "b\n", "tests/test_m.py": "t\n", "tests/test_new.py": "n\n"},
    )
    patch = Patch.between(before, after)
    assert patch.only(under="tests", keep=False).files == ("src/m.py",)
    assert patch.only(under="tests", keep=True).files == ("tests/test_new.py",)


def test_junit_outcomes_are_keyed_by_class_and_name(tmp_path: Path) -> None:
    report = tmp_path / "r.xml"
    report.write_text(
        '<testsuites><testsuite name="pytest">'
        '<testcase classname="tests.test_m" name="test_ok"/>'
        '<testcase classname="tests.test_m" name="test_bad[1]"><failure message="x"/></testcase>'
        '<testcase classname="tests.test_m.TestK" name="test_err"><error message="x"/></testcase>'
        '<testcase classname="tests.test_m" name="test_skip"><skipped message="x"/></testcase>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    assert read_junit(report) == {
        "tests.test_m::test_ok": "passed",
        "tests.test_m::test_bad[1]": "failed",
        "tests.test_m.TestK::test_err": "error",
        "tests.test_m::test_skip": "skipped",
    }
    assert read_junit(tmp_path / "missing.xml") == {}
