"""A task: one real, already-fixed bug from the project's own history."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

_TEST_MODULE = re.compile(r"(?:^|/)(?:test_[^/]*|[^/]*_tests?|tests)\.py$")


def is_test_module(path: str) -> bool:
    """Whether ``path`` is named like a module pytest collects tests from."""
    return _TEST_MODULE.search(path) is not None


@dataclass(frozen=True, slots=True)
class Task:
    task_id: str

    base_commit: str
    """The commit just before the fix. Agents work from here."""

    fix_commit: str
    """The maintainers' real fix. Never shown to an agent; it supplies the oracle."""

    reference: int
    """The issue or pull-request number the problem statement came from."""

    reference_kind: str
    """``issue`` when a user-filed report was found, ``pull_request`` otherwise."""

    title: str
    problem_statement: str
    """What agents are given, and the only thing they are given."""

    source_files: tuple[str, ...]
    test_files: tuple[str, ...]
    """Test-directory files the fix touched, helpers included."""

    added_tests: tuple[str, ...]
    """Test functions the fix added - absent from the base commit."""

    statement_redacted: bool = False
    """Whether a section describing the author's own fix had to be removed."""

    @property
    def test_modules(self) -> tuple[str, ...]:
        """The touched test files that hold tests; their outcomes define "resolved"."""
        return tuple(path for path in self.test_files if is_test_module(path))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        payload = {k: v for k, v in data.items() if k in set(cls.__slots__)}
        for key in ("source_files", "test_files", "added_tests"):
            payload[key] = tuple(payload.get(key) or ())
        return cls(**payload)
