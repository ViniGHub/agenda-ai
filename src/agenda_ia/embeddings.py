"""Turns text into vectors, locally (no API, no cost, the agenda never leaves the machine)."""

from __future__ import annotations

from typing import Protocol, Sequence

import numpy as np

from .config import EMBEDDING_MODEL


class Embedder(Protocol):
    dim: int

    def passages(self, texts: Sequence[str]) -> np.ndarray: ...

    def query(self, text: str) -> np.ndarray: ...


class E5Embedder:
    """multilingual-e5: documents get "passage: ", questions get "query: "."""

    def __init__(self, model_name: str = EMBEDDING_MODEL):
        from sentence_transformers import SentenceTransformer  # heavy import, only when used

        self._model = SentenceTransformer(model_name)
        get_dim = getattr(self._model, "get_embedding_dimension", None) or self._model.get_sentence_embedding_dimension
        self.dim = get_dim()

    def passages(self, texts: Sequence[str]) -> np.ndarray:
        return self._encode([f"passage: {t}" for t in texts])

    def query(self, text: str) -> np.ndarray:
        return self._encode([f"query: {text}"])[0]

    def _encode(self, texts: list[str]) -> np.ndarray:
        # Normalized vectors: the dot product is the cosine similarity.
        vectors = self._model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
        return vectors.astype(np.float32)
