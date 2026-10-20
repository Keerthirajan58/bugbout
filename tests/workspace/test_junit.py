"""Per-test outcomes from pytest's JUnit XML report."""

from __future__ import annotations

from pathlib import Path

from bugbout.workspace.junit import read_junit


def test_outcomes_are_keyed_by_class_and_name(tmp_path: Path) -> None:
    report = tmp_path / "report.xml"
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


def test_a_missing_report_means_no_outcomes(tmp_path: Path) -> None:
    assert read_junit(tmp_path / "missing.xml") == {}
