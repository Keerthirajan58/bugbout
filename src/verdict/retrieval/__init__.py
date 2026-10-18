"""Retrieval: mine a repository's own history and search it to ground hypotheses."""

from verdict.retrieval.builder import build_knowledge_base, kb_path, load_or_build, mine_documents
from verdict.retrieval.chunking import chunk_module, chunk_workspace
from verdict.retrieval.documents import Document
from verdict.retrieval.embedding import Embedder
from verdict.retrieval.index import KnowledgeBase, ScoredDocument, tokenize
from verdict.retrieval.provider import KnowledgeBaseRetrieval
from verdict.retrieval.risk import RiskScore, rank, score_symbols

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
