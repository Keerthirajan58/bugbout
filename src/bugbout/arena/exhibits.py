"""Exhibits: test files offered as evidence about a task.

Two kinds of author write them. An **examiner** wrote its tests from the issue
alone and never saw a patch. A **contestant** wrote a regression test for its
own fix; run against everyone else's fix, that test is evidence too.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal, get_args

from bugbout.arena.briefs import ASSIGNMENTS
from bugbout.arena.race import Attempt
from bugbout.tasks.task import is_test_module

Kind = Literal["examiner", "contestant"]
KINDS: frozenset[Kind] = frozenset(get_args(Kind))


@dataclass(frozen=True, slots=True)
class Exhibit:
    task_id: str
    author: str
    vendor: str
    kind: Kind
    test_code: str
    note: str = ""
    """Anything the author did that it was not asked to, such as editing the source."""

    error: str = ""
    """Why there is no test, when there is none."""

    @property
    def usable(self) -> bool:
        return bool(self.test_code.strip())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Exhibit:
        if data.get("kind") not in KINDS:
            raise ValueError(f"unknown exhibit kind {data.get('kind')!r}")
        return cls(**{k: v for k, v in data.items() if k in set(cls.__slots__)})


def author_of(attempt: Attempt) -> str:
    """Who an attempt's tests are attributed to: a contestant by name, an examiner by role too."""
    if ASSIGNMENTS[attempt.assignment].role == "contestant":
        return attempt.agent
    return f"{attempt.assignment}/{attempt.agent}"


def exhibit_of(attempt: Attempt, test_root: str) -> Exhibit:
    """The test file an attempt created - the one its brief asked for, if it wrote that one."""
    assignment = ASSIGNMENTS[attempt.assignment]
    kind: Kind = "contestant" if assignment.role == "contestant" else "examiner"
    root = test_root.strip("/")
    created = attempt.patch.only(under=root, keep=True).created()
    wanted = f"{root}/{assignment.test_file}"
    written = (
        wanted if wanted in created else next((p for p in sorted(created) if is_test_module(p)), "")
    )
    notes = []
    if written and written != wanted:
        notes.append(f"tests written to {written} instead of {wanted}")
    if kind == "examiner" and attempt.patch.only(under=root, keep=False).files:
        notes.append("edited the source, which was discarded")
    return Exhibit(
        task_id=attempt.task_id,
        author=author_of(attempt),
        vendor=attempt.vendor,
        kind=kind,
        test_code=created.get(written, ""),
        note="; ".join(notes),
        error="" if written else f"no test written ({attempt.status})",
    )
