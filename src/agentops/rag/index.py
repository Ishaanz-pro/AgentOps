"""FAISS-backed vector index over policy chunks, with save/load.

The index stores the embedder name so a stale index built with a different
provider is rejected instead of silently returning garbage.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import faiss
import numpy as np

from agentops.rag.chunker import Chunk, load_policy_chunks
from agentops.rag.embeddings import Embedder


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float


class IndexMismatch(Exception):
    pass


class PolicyIndex:
    def __init__(self, embedder: Embedder, chunks: list[Chunk], index: faiss.Index):
        self.embedder = embedder
        self.chunks = chunks
        self._index = index

    # ---- construction -------------------------------------------------
    @classmethod
    def build(cls, embedder: Embedder, chunks: list[Chunk]) -> "PolicyIndex":
        if not chunks:
            raise ValueError("no chunks to index")
        vectors = embedder.embed_documents([c.text for c in chunks])
        index = faiss.IndexFlatIP(vectors.shape[1])
        index.add(vectors)
        return cls(embedder, chunks, index)

    @classmethod
    def build_from_dir(cls, embedder: Embedder, policy_dir: Path) -> "PolicyIndex":
        return cls.build(embedder, load_policy_chunks(policy_dir))

    # ---- persistence --------------------------------------------------
    def save(self, index_dir: Path) -> None:
        index_dir = Path(index_dir)
        index_dir.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self._index, str(index_dir / "policies.faiss"))
        (index_dir / "chunks.json").write_text(
            json.dumps({"embedder": self.embedder.name, "chunks": [asdict(c) for c in self.chunks]}),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, embedder: Embedder, index_dir: Path) -> "PolicyIndex":
        index_dir = Path(index_dir)
        payload = json.loads((index_dir / "chunks.json").read_text(encoding="utf-8"))
        if payload["embedder"] != embedder.name:
            raise IndexMismatch(f"index built with {payload['embedder']}, embedder is {embedder.name}")
        chunks = [Chunk(**c) for c in payload["chunks"]]
        index = faiss.read_index(str(index_dir / "policies.faiss"))
        return cls(embedder, chunks, index)

    @classmethod
    def load_or_build(cls, embedder: Embedder, policy_dir: Path, index_dir: Path) -> "PolicyIndex":
        try:
            return cls.load(embedder, index_dir)
        except (FileNotFoundError, IndexMismatch, json.JSONDecodeError):
            built = cls.build_from_dir(embedder, policy_dir)
            built.save(index_dir)
            return built

    # ---- query --------------------------------------------------------
    def search(self, query: str, k: int = 4, min_score: float | None = None) -> list[Hit]:
        """Top-k hits at or above a relevance floor.

        ``min_score`` defaults to the embedder's own calibrated floor. Returning
        an empty list is the honest answer for out-of-scope queries: the agent
        must say it found no policy rather than quote the "nearest" irrelevant text.
        """
        floor = getattr(self.embedder, "min_score", 0.0) if min_score is None else min_score
        q = self.embedder.embed_query(query).reshape(1, -1).astype(np.float32)
        scores, ids = self._index.search(q, min(k, len(self.chunks)))
        hits: list[Hit] = []
        for score, idx in zip(scores[0], ids[0]):
            if idx < 0 or float(score) < floor:
                continue
            hits.append(Hit(self.chunks[int(idx)], float(score)))
        return hits
