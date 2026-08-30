"""Retrieval: mine a repository's own history and search it to ground hypotheses."""

from precedent.retrieval.builder import build_knowledge_base, kb_path, load_or_build, mine_documents
from precedent.retrieval.chunking import chunk_module, chunk_workspace
from precedent.retrieval.documents import Document
from precedent.retrieval.embedding import Embedder
from precedent.retrieval.index import KnowledgeBase, ScoredDocument, tokenize
from precedent.retrieval.provider import KnowledgeBaseRetrieval
from precedent.retrieval.risk import RiskScore, rank, score_symbols

__all__ = [
    "Document",
    "Embedder",
    "KnowledgeBase",
    "KnowledgeBaseRetrieval",
    "RiskScore",
    "ScoredDocument",
    "build_knowledge_base",
    "chunk_module",
    "chunk_workspace",
    "kb_path",
    "load_or_build",
    "mine_documents",
    "rank",
    "score_symbols",
    "tokenize",
]
