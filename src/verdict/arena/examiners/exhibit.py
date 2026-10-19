"""An exhibit: one author's test file, offered as evidence about a task."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

Kind = Literal["blind", "self"]
"""``blind``: written without seeing any patch. ``self``: a contestant's own test."""


@dataclass(frozen=True, slots=True)
class Exhibit:
    task_id: str
    author: str
    vendor: str
    kind: Kind
    test_code: str
    reasoning: str = ""
    model: str = ""
    error: str = ""
    note: str = ""

    @property
    def usable(self) -> bool:
        return bool(self.test_code.strip())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Exhibit:
        return cls(**{k: v for k, v in data.items() if k in set(cls.__slots__)})
