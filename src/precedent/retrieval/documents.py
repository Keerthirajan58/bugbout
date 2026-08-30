"""The unit of retrieval.

Everything indexed - a past bugfix, a reverted change, a call site, a chunk of
code - becomes a :class:`Document`. Keeping one shape lets BM25 and embedding
search run over a single corpus, and lets the Prosecutor cite any retrieved item
in the same way regardless of where it came from.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Kind = Literal["incident", "revert", "call_site", "code_chunk"]

KIND_LABELS: dict[Kind, str] = {
    "incident": "past bugfix in this repository",
    "revert": "a change that was reverted",
    "call_site": "where this code is called from",
    "code_chunk": "related code in this repository",
}


@dataclass(frozen=True, slots=True)
class Document:
    """One retrievable item of repository knowledge."""

    doc_id: str
    kind: Kind
    target_slug: str
    title: str
    body: str

    module_path: str | None = None
    """Source file this document concerns, used to scope retrieval to the diff."""

    symbols: tuple[str, ...] = ()
    """Function and class names this document touches. The primary scoping
    signal: an incident that touched ``set_value`` is relevant to a diff in
    ``set_value`` in a way that keyword overlap alone would not establish."""

    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        """What gets indexed. Title first: it carries the most signal per token."""
        parts = [self.title, self.body]
        if self.symbols:
            parts.append("symbols: " + " ".join(self.symbols))
        if self.module_path:
            parts.append(f"file: {self.module_path}")
        return "\n".join(p for p in parts if p)

    def cite(self) -> str:
        """Short, stable label the Prosecutor uses to cite this item."""
        return f"[{self.kind}:{self.doc_id}]"

    def render(self, max_body: int = 1200) -> str:
        """Human- and model-readable form for the prompt."""
        body = (
            self.body if len(self.body) <= max_body else self.body[:max_body] + "\n... (truncated)"
        )
        header = f"{self.cite()} {KIND_LABELS[self.kind]}"
        location = f"  file: {self.module_path}" if self.module_path else ""
        return "\n".join(p for p in (header, f"  {self.title}", location, body) if p)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Document:
        known = set(cls.__slots__)
        payload = {k: v for k, v in data.items() if k in known}
        if "symbols" in payload:
            payload["symbols"] = tuple(payload["symbols"])
        return cls(**payload)

    @staticmethod
    def make_id(kind: str, target_slug: str, *parts: str) -> str:
        """Content-addressed id, so re-indexing does not rename documents.

        A citation recorded in a phase 3 result must still resolve after the
        index is rebuilt, or the dashboard cannot show what was retrieved.
        """
        payload = ":".join((kind, target_slug, *parts))
        return hashlib.sha256(payload.encode()).hexdigest()[:12]
