"""Corpus mining: turn a repository's own history into retrievable knowledge."""

from precedent.retrieval.mining.callsites import mine_call_sites
from precedent.retrieval.mining.git_history import mine_history

__all__ = ["mine_call_sites", "mine_history"]
