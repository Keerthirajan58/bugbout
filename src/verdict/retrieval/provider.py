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

from verdict.agents.change import CodeChange
from verdict.logging import get_logger
from verdict.retrieval.documents import Document
from verdict.retrieval.embedding import Embedder
from verdict.retrieval.index import KnowledgeBase, ScoredDocument

log = get_logger(__name__)

INSTRUCTION = """\
EVIDENCE FROM THIS REPOSITORY'S OWN HISTORY

Below are real past defects, reverts, call sites and related code from this \
codebase, retrieved because they concern the code the diff touches.

Read them BEFORE forming your hypothesis, and use them like a reviewer who \
remembers this codebase would:

- a past defect tells you which inputs have actually broken this code before;
- a call site tells you how the function is really reached, and with what \
argument shapes - so your test exercises a realistic path instead of a guessed one;
- a code chunk tells you what the surrounding API actually does, so your test \
does not assert behaviour the library never had.

THREE RULES

1. You are testing THE DIFF. This evidence exists to sharpen your hypothesis \
about the diff - never to redirect you onto a historical bug that the diff does \
not touch.
2. This evidence is HISTORICAL, and APIs get renamed. The diff and the enclosing \
source are the only authority on what the code is called TODAY. Where a retrieved \
item uses a parameter, method or class name that differs from the code in front of \
you, the code in front of you is right and the evidence is stale. Never call a name \
that does not appear in the current source you were shown.
3. Set "used_evidence" to a one-sentence statement of what these items told you, \
or to "nothing relevant" if none applied. Then set "citation" to the identifier \
in square brackets of the item you relied on, e.g. "incident:8bb5feb6931a", or \
null if none did. Never invent a citation - an honest "nothing relevant" is more \
useful than a fabricated one.
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

    def query_for(self, change: CodeChange) -> str:
        """Build the search query from the diff.

        The changed lines, the symbol and the file - not the whole function.
        Including the entire enclosing source would swamp the few tokens that
        actually characterise the change.
        """
        changed = "\n".join(
            line[1:].strip()
            for line in change.diff.splitlines()
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
        )
        parts = [change.function or "", change.module_path.rsplit("/", 1)[-1], changed]
        return "\n".join(p for p in parts if p)

    def retrieve(self, change: CodeChange) -> list[ScoredDocument]:
        query = self.query_for(change)
        symbols = tuple(s for s in (change.function,) if s)
        return self.kb.search(
            query,
            self.embedder.embed_one(query),
            top_k=self.top_k,
            module_path=change.module_path,
            symbols=symbols,
        )

    def block_for(self, change: CodeChange) -> tuple[str, list[dict[str, Any]]]:
        """The prompt section, plus a record of what was retrieved.

        The record is what the dashboard renders, and what makes a claim about
        grounding checkable after the fact rather than taken on trust.
        """
        hits = self.retrieve(change)
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
