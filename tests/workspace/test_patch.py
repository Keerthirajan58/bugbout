"""Patches are measured from disk, applied strictly, and portable."""

from __future__ import annotations

from pathlib import Path

import pytest

from bugbout.errors import PatchError
from bugbout.workspace.patch import Patch


def _tree(root: Path, files: dict[str, str]) -> Path:
    for relative, text in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(text, encoding="utf-8")
    return root


def test_a_round_trip_reproduces_the_changed_tree(tmp_path: Path) -> None:
    before = _tree(
        tmp_path / "before", {"src/pkg/a.py": "one\ntwo\nthree\n", "src/pkg/gone.py": "x = 1\n"}
    )
    after = _tree(
        tmp_path / "after", {"src/pkg/a.py": "one\n2\nthree\nfour", "src/pkg/new.py": "y = 2\n"}
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
    drifted = _tree(tmp_path / "drifted", {"m.py": "a\nsomething else\nc\n"})
    with pytest.raises(PatchError):
        Patch.between(before, after).apply(drifted)


def test_a_removed_line_that_looks_like_a_header_does_not_end_the_hunk(tmp_path: Path) -> None:
    before = _tree(tmp_path / "before", {"m.py": "x\n-- not a header\ny\n"})
    after = _tree(tmp_path / "after", {"m.py": "x\ny\n"})
    Patch.between(before, after).apply(before)
    assert (before / "m.py").read_text() == "x\ny\n"


def test_tool_state_and_bytecode_are_never_part_of_a_change(tmp_path: Path) -> None:
    before = _tree(tmp_path / "before", {"m.py": "a\n"})
    after = _tree(
        tmp_path / "after",
        {
            "m.py": "a\n",
            ".codex/x.py": "z\n",
            ".bugbout/y.py": "z\n",
            "pkg/__pycache__/m.py": "z\n",
        },
    )
    assert Patch.between(before, after).empty


def test_a_fix_and_its_tests_separate_by_directory(tmp_path: Path) -> None:
    before = _tree(tmp_path / "before", {"src/m.py": "a\n", "tests/test_m.py": "t\n"})
    after = _tree(
        tmp_path / "after",
        {"src/m.py": "b\n", "tests/test_m.py": "t\n", "tests/test_new.py": "n\n"},
    )
    patch = Patch.between(before, after)
    assert patch.only(under="tests", keep=False).files == ("src/m.py",)
    assert patch.only(under="tests/", keep=True).files == ("tests/test_new.py",)


def test_created_files_come_back_whole(tmp_path: Path) -> None:
    before = _tree(tmp_path / "before", {"m.py": "a\n"})
    after = _tree(
        tmp_path / "after", {"m.py": "a\n", "tests/test_new.py": "def test():\n    pass\n"}
    )
    assert Patch.between(before, after).created() == {
        "tests/test_new.py": "def test():\n    pass\n"
    }


def test_the_dict_form_round_trips() -> None:
    patch = Patch("--- a/m.py\n+++ b/m.py\n@@ -1 +1 @@\n-a\n+b\n")
    assert Patch.from_dict(patch.to_dict()) == patch
    assert patch.lines_changed() == 2
