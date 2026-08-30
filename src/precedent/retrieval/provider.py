"""Supplies the Prosecutor's retrieved context.

This class is the *entire* difference between the two arms of the ablation.
Phase 2 runs with ``retrieval=None``; phase 3 runs with this. Everything else -
the corpus, the operator set, the prompt, the Skeptic, the sandbox, the scoring
rule - is byte-identical between the two runs. That is what makes the delta
attributable to retrieval rather than to some other change.

The Prosecutor is also required to **cite** the item that motivated its
hypothesis. Two reasons. It makes grounding visible, so the dashboard can show
which retrieved item produced which accusation instead of asserting that
retrieval helped. And it discourages the failure mode where a model is handed
context, ignores it, and writes the same generic test it would have written
anyway - a citation requirement makes that visible in the output.
"""

from __future__ import annotations

from typing import Any

from precedent.logging import get_logger
from precedent.mutation.mutant import MutantResult
from precedent.retrieval.documents import Document
from precedent.retrieval.embedding import Embedder
from precedent.retrieval.index import KnowledgeBase, ScoredDocument

log = get_logger(__name__)

INSTRUCTION = """\
RETRIEVED CONTEXT FROM THIS REPOSITORY'S OWN HISTORY

These are real past defects, reverts, call sites and related code from this \
codebase, retrieved because they concern the code this diff touches. Use them to \
make your hypothesis specific to THIS codebase rather than generically plausible.

You MUST set "citation" in your response to the identifier in square brackets of \
the item that motivated your hypothesis, e.g. "citation": "incident:8bb5feb6931a". \
If none of them informed you, set it to null and say so - do not invent one.
"""


class KnowledgeBaseRetrieval:
    """Retrieves scoped context for one diff and formats it for the prompt."""

    def __init__(
        self,
        knowledge_base: KnowledgeBase,
        *,
        embedder: Embedder | None = None,
        top_k: int = 6,
    ) -> None:
        self.kb = knowledge_base
        self.embedder = embedder or Embedder()
        self.top_k = top_k

    def query_for(self, result: MutantResult) -> str:
        """Build the search query from the diff.

        The changed lines, the symbol and the file - not the whole function.
        Including the entire enclosing source would swamp the few tokens that
        actually characterise the change.
        """
        spec = result.spec
        changed = "\n".join(
            line[1:].strip()
            for line in result.diff.splitlines()
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
        )
        parts = [spec.function or "", spec.module_path.rsplit("/", 1)[-1], changed]
        return "\n".join(p for p in parts if p)

    def retrieve(self, result: MutantResult) -> list[ScoredDocument]:
        spec = result.spec
        query = self.query_for(result)
        symbols = tuple(s for s in (spec.function,) if s)
        return self.kb.search(
            query,
            self.embedder.embed_one(query),
            top_k=self.top_k,
            module_path=spec.module_path,
            symbols=symbols,
        )

    def block_for(self, result: MutantResult) -> tuple[str, list[dict[str, Any]]]:
        """The prompt section, plus a record of what was retrieved.

        The record is what the dashboard renders, and what makes a claim about
        grounding checkable after the fact rather than taken on trust.
        """
        hits = self.retrieve(result)
        if not hits:
            return "", []
        rendered = "\n\n".join(hit.document.render() for hit in hits)
        block = f"{INSTRUCTION}\n{rendered}"
        record = [
            {
                "doc_id": hit.document.doc_id,
                "kind": hit.document.kind,
                "citation": f"{hit.document.kind}:{hit.document.doc_id}",
                "title": hit.document.title,
                "module_path": hit.document.module_path,
                "symbols": list(hit.document.symbols),
                "score": round(hit.score, 6),
                "why": hit.explain(),
                "url": hit.document.metadata.get("url"),
            }
            for hit in hits
        ]
        return block, record

    def resolve_citation(self, citation: str | None) -> Document | None:
        """Turn a cited identifier back into the document it names."""
        if not citation:
            return None
        doc_id = citation.split(":")[-1].strip().strip("[]")
        return self.kb.get(doc_id)
