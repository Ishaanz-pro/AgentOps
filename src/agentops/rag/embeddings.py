"""Embedding providers.

- GeminiEmbedder: real embeddings via the Gemini API (gemini-embedding-001).
- LocalHashEmbedder: deterministic, offline, dependency-free hashed
  bag-of-words vectors. Used by tests and when no API key is present so the
  whole pipeline (retrieval -> tool -> agent -> API) is testable without
  network access. It is a *stand-in*, not a quality benchmark.

Both return L2-normalised float32 arrays so FAISS inner-product == cosine.
"""

from __future__ import annotations

import hashlib
import re
from typing import Protocol

import numpy as np

_TOKEN = re.compile(r"[a-z0-9]+")


class Embedder(Protocol):
    name: str
    dim: int

    def embed_documents(self, texts: list[str]) -> np.ndarray: ...
    def embed_query(self, text: str) -> np.ndarray: ...


def _normalise(vectors: np.ndarray) -> np.ndarray:
    vectors = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vectors / norms


class LocalHashEmbedder:
    name = "local-hash-v1"

    def __init__(self, dim: int = 512):
        self.dim = dim

    @staticmethod
    def _tokens(text: str) -> list[str]:
        toks = _TOKEN.findall(text.lower())
        # crude plural/verb stripping so "refunds" ~ "refund"
        toks = [t[:-1] if len(t) > 4 and t.endswith("s") else t for t in toks]
        bigrams = [f"{a}_{b}" for a, b in zip(toks, toks[1:])]
        return toks + bigrams

    def _vector(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        for tok in self._tokens(text):
            h = int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=8).digest(), "little")
            idx = h % self.dim
            sign = 1.0 if (h >> 63) & 1 else -1.0
            # bigrams weigh less than unigrams
            v[idx] += sign * (0.5 if "_" in tok else 1.0)
        return v

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return _normalise(np.stack([self._vector(t) for t in texts]))

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed_documents([text])[0]


class GeminiEmbedder:
    name = "gemini-embedding-001"

    def __init__(self, api_key: str, model: str = "gemini-embedding-001", dim: int = 768):
        from google import genai  # imported lazily so offline paths never need it

        self._client = genai.Client(api_key=api_key)
        self._model = model
        self.dim = dim

    def _embed(self, texts: list[str], task_type: str) -> np.ndarray:
        from google.genai import types

        out: list[list[float]] = []
        for i in range(0, len(texts), 100):  # API batch limit
            resp = self._client.models.embed_content(
                model=self._model,
                contents=texts[i : i + 100],
                config=types.EmbedContentConfig(task_type=task_type, output_dimensionality=self.dim),
            )
            out.extend(e.values for e in resp.embeddings)
        return _normalise(np.asarray(out, dtype=np.float32))

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return self._embed(texts, "RETRIEVAL_DOCUMENT")

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed([text], "RETRIEVAL_QUERY")[0]


def make_embedder(provider: str, api_key: str | None) -> Embedder:
    if provider == "gemini":
        if not api_key:
            raise ValueError("AGENTOPS_EMBEDDINGS=gemini requires GOOGLE_API_KEY")
        return GeminiEmbedder(api_key)
    return LocalHashEmbedder()
