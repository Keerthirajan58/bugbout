"""Assemble a target's knowledge base from its own repository."""

from __future__ import annotations

from pathlib import Path

from verdict.logging import get_logger
from verdict.paths import artifacts_dir
from verdict.retrieval.chunking import chunk_workspace
from verdict.retrieval.documents import Document
from verdict.retrieval.embedding import Embedder
from verdict.retrieval.index import KnowledgeBase
from verdict.retrieval.mining import mine_call_sites, mine_history
from verdict.targets.workspace import TargetWorkspace

log = get_logger(__name__)


def kb_path(target_slug: str) -> Path:
    return artifacts_dir() / "kb" / target_slug


def mine_documents(workspace: TargetWorkspace) -> list[Document]:
    """Every document type, from the local clone only - no API quota spent."""
    documents = [
        *mine_history(workspace),
        *chunk_workspace(workspace),
        *mine_call_sites(workspace),
    ]
    # Content-addressed ids make duplicates possible in principle (the same
    # commit reachable twice); collapse them so BM25 statistics stay honest.
    unique = {d.doc_id: d for d in documents}
    return sorted(unique.values(), key=lambda d: d.doc_id)


def build_knowledge_base(
    workspace: TargetWorkspace,
    *,
    embedder: Embedder | None = None,
    save: bool = True,
) -> KnowledgeBase:
    """Mine, chunk, embed and index one target."""
    documents = mine_documents(workspace)
    embedder = embedder or Embedder()
    log.info("Embedding %d documents", len(documents))
    embeddings = embedder.embed([d.text for d in documents])
    kb = KnowledgeBase(documents, embeddings)
    if save:
        kb.save(kb_path(workspace.target.slug))
    return kb


def load_or_build(workspace: TargetWorkspace, *, rebuild: bool = False) -> KnowledgeBase:
    directory = kb_path(workspace.target.slug)
    if not rebuild and KnowledgeBase.exists(directory):
        return KnowledgeBase.load(directory)
    return build_knowledge_base(workspace)
