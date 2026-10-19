"""An exhibit: one author's test file, offered as evidence about a task."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

Kind = Literal["agentic", "prompted", "self"]
"""How an exhibit was written. ``agentic`` and ``prompted`` are both blind - the
author never saw a patch - but an agentic author ran its test against the real
code before submitting it, and a prompted one answered in a single call.
``self`` is a contestant's own test for its own fix."""

KINDS: frozenset[Kind] = frozenset({"agentic", "prompted", "self"})
BLIND: frozenset[Kind] = frozenset({"agentic", "prompted"})


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
