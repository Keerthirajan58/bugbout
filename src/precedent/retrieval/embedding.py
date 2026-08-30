"""Text embeddings, computed locally.

An off-the-shelf pretrained model (``BAAI/bge-small-en-v1.5``, 384 dimensions)
run through ONNX on the CPU. Nothing is trained here.

Running locally rather than calling an embedding API is a deliberate choice on
three grounds, and reproducibility is the one that matters most: a hosted model
can be updated underneath you, which would silently change the retrieval results
that the ablation number depends on. It is also free and has no rate limit, so
the corpus can be re-indexed as often as the chunking strategy changes.
"""

from __future__ import annotations

import numpy as np

from precedent.logging import get_logger

log = get_logger(__name__)

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_DIM = 384


class Embedder:
    """Lazily-loaded local embedding model."""

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        self.model_name = model_name
        self._model: object | None = None

    def _ensure(self) -> object:
        if self._model is None:
            from fastembed import TextEmbedding

            log.info("Loading embedding model %s", self.model_name)
            self._model = TextEmbedding(self.model_name)
        return self._model

    def embed(self, texts: list[str]) -> np.ndarray:
        """Embed a batch, returning L2-normalised row vectors.

        Normalising here means cosine similarity is a plain dot product, so
        search is one matrix multiply with no per-query division.
        """
        if not texts:
            return np.zeros((0, DEFAULT_DIM), dtype=np.float32)
        model = self._ensure()
        vectors = np.asarray(list(model.embed(texts)), dtype=np.float32)  # type: ignore[attr-defined]
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return np.asarray(vectors / np.clip(norms, 1e-8, None), dtype=np.float32)

    def embed_one(self, text: str) -> np.ndarray:
        row: np.ndarray = self.embed([text])[0]
        return row
