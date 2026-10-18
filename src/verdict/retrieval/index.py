"""The knowledge base: hybrid BM25 + embedding search over mined documents.

**Why hybrid.** The two retrievers fail in opposite directions. BM25 matches
literal tokens, so it finds the incident that names ``set_value`` but misses one
that describes the same defect as "nested key assignment". Embeddings match
meaning, so they find the paraphrase but happily rank a semantically similar
incident about a completely different function above the exact match. Neither is
sufficient; together they cover each other.

**Why rank fusion rather than score averaging.** BM25 scores are unbounded and
depend on corpus statistics; cosine similarity is bounded in [-1, 1]. Putting
them on a common scale requires an arbitrary normalisation that shifts whenever
the corpus changes. Reciprocal rank fusion uses only *positions*, so it is
immune to that, and its behaviour does not drift as documents are added.

**Why scoping is separate from relevance.** A diff in ``validate.URL`` should
prefer history about ``validate.URL``. That is a structural fact, not a textual
similarity, so it is applied as an explicit boost on symbol and file match
rather than left to the retrievers to infer from keywords.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from rank_bm25 import BM25Okapi

from verdict.logging import get_logger
from verdict.retrieval.documents import Document

log = get_logger(__name__)

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]+")

# Reciprocal rank fusion constant. 60 is the value from the original TREC work
# and is the de-facto default; it damps the influence of the very top rank so a
# single retriever cannot dominate the fused ordering.
RRF_K = 60

# Additive boosts, applied after fusion. Sized so that an exact symbol match
# outranks a merely similar document without suppressing it entirely.
SYMBOL_BOOST = 0.030
MODULE_BOOST = 0.015
INCIDENT_BOOST = 0.008


def tokenize(text: str) -> list[str]:
    """Split into identifier-aware tokens, with snake_case and camelCase parts.

    ``set_value`` must match a document that says ``setValue`` or that only
    mentions ``value``, so compound identifiers are indexed whole *and* split.
    """
    tokens: list[str] = []
    for match in _TOKEN.findall(text.lower()):
        tokens.append(match)
        if "_" in match:
            tokens.extend(part for part in match.split("_") if len(part) > 2)
    return tokens


@dataclass(frozen=True, slots=True)
class ScoredDocument:
    """A retrieved document with the evidence for why it ranked where it did."""

    document: Document
    score: float
    bm25_rank: int | None
    vector_rank: int | None
    boosts: tuple[str, ...]

    def explain(self) -> str:
        """Human-readable provenance, surfaced in the dashboard."""
        parts = []
        if self.bm25_rank is not None:
            parts.append(f"bm25 #{self.bm25_rank + 1}")
        if self.vector_rank is not None:
            parts.append(f"vector #{self.vector_rank + 1}")
        parts.extend(self.boosts)
        return ", ".join(parts)


class KnowledgeBase:
    """An in-memory hybrid index over one target's mined documents.

    Deliberately not a database. The corpus is ~1,200 documents and a 1200x384
    float32 matrix is under 2MB, so exact search is a single matrix product -
    faster than any index could be, with no network, no credentials, and
    identical results on every machine. That reproducibility matters more here
    than scalability the project will never need.
    """

    def __init__(self, documents: list[Document], embeddings: np.ndarray) -> None:
        if len(documents) != embeddings.shape[0]:
            raise ValueError(f"{len(documents)} documents but {embeddings.shape[0]} embeddings")
        self.documents = documents
        self.embeddings = embeddings
        self._bm25 = BM25Okapi([tokenize(d.text) for d in documents]) if documents else None
        self._by_id = {d.doc_id: d for d in documents}

    def __len__(self) -> int:
        return len(self.documents)

    def get(self, doc_id: str) -> Document | None:
        """Resolve a citation back to its document."""
        return self._by_id.get(doc_id)

    def search(
        self,
        query: str,
        query_vector: np.ndarray,
        *,
        top_k: int = 8,
        module_path: str | None = None,
        symbols: tuple[str, ...] = (),
        kinds: tuple[str, ...] = (),
        candidate_pool: int = 60,
    ) -> list[ScoredDocument]:
        """Hybrid retrieval, scoped to the code a diff actually touches."""
        if not self.documents or self._bm25 is None:
            return []

        eligible = [i for i, d in enumerate(self.documents) if not kinds or d.kind in kinds]
        if not eligible:
            return []

        bm25_scores = np.asarray(self._bm25.get_scores(tokenize(query)), dtype=np.float32)
        vector_scores = self.embeddings @ query_vector

        bm25_ranked = sorted(eligible, key=lambda i: -bm25_scores[i])[:candidate_pool]
        vector_ranked = sorted(eligible, key=lambda i: -vector_scores[i])[:candidate_pool]
        bm25_rank = {index: rank for rank, index in enumerate(bm25_ranked)}
        vector_rank = {index: rank for rank, index in enumerate(vector_ranked)}

        wanted = {s for s in symbols if s}
        fused: list[ScoredDocument] = []
        for index in set(bm25_ranked) | set(vector_ranked):
            score = 0.0
            if index in bm25_rank:
                score += 1.0 / (RRF_K + bm25_rank[index] + 1)
            if index in vector_rank:
                score += 1.0 / (RRF_K + vector_rank[index] + 1)

            document = self.documents[index]
            boosts: list[str] = []
            if wanted and wanted & set(document.symbols):
                score += SYMBOL_BOOST
                boosts.append("symbol match")
            if module_path and document.module_path == module_path:
                score += MODULE_BOOST
                boosts.append("same file")
            if document.kind in ("incident", "revert"):
                # A past defect in this code is the point of the exercise; a
                # code chunk of equal textual relevance is weaker evidence.
                score += INCIDENT_BOOST
                boosts.append("past defect")

            fused.append(
                ScoredDocument(
                    document=document,
                    score=score,
                    bm25_rank=bm25_rank.get(index),
                    vector_rank=vector_rank.get(index),
                    boosts=tuple(boosts),
                )
            )

        fused.sort(key=lambda s: (-s.score, s.document.doc_id))
        return fused[:top_k]

    # --- persistence ---------------------------------------------------------

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "documents.json").write_text(
            json.dumps([d.to_dict() for d in self.documents], indent=2), encoding="utf-8"
        )
        np.save(directory / "embeddings.npy", self.embeddings)
        log.info("Saved knowledge base (%d documents) to %s", len(self.documents), directory)

    @classmethod
    def load(cls, directory: Path) -> KnowledgeBase:
        payload: list[dict[str, Any]] = json.loads(
            (directory / "documents.json").read_text(encoding="utf-8")
        )
        documents = [Document.from_dict(item) for item in payload]
        embeddings = np.load(directory / "embeddings.npy")
        return cls(documents, embeddings)

    @staticmethod
    def exists(directory: Path) -> bool:
        return (directory / "documents.json").exists() and (directory / "embeddings.npy").exists()
