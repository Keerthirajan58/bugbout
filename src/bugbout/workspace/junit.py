"""Per-test outcomes from a pytest JUnit XML report."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path


def read_junit(path: Path) -> dict[str, str]:
    """Each test's outcome, keyed ``classname::name``; empty if there is no report.

    Outcomes are ``passed``, ``failed``, ``error`` or ``skipped``. The report is
    one pytest just wrote into bugbout's own scratch directory.
    """
    if not path.exists():
        return {}
    root = ET.parse(path).getroot()  # noqa: S314 - our own pytest output
    outcomes: dict[str, str] = {}
    for case in root.iter("testcase"):
        key = f"{case.get('classname', '')}::{case.get('name', '')}"
        if case.find("failure") is not None:
            outcomes[key] = "failed"
        elif case.find("error") is not None:
            outcomes[key] = "error"
        elif case.find("skipped") is not None:
            outcomes[key] = "skipped"
        else:
            outcomes[key] = "passed"
    return outcomes
