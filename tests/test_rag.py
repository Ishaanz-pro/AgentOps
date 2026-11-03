from pathlib import Path

from agentops.rag.chunker import load_policy_chunks
from agentops.rag.embeddings import LocalHashEmbedder
from agentops.rag.index import IndexMismatch, PolicyIndex


def test_chunker_produces_section_chunks(policy_dir):
    chunks = load_policy_chunks(policy_dir)
    ids = {c.chunk_id for c in chunks}
    assert "POL-001#1" in ids
    refund_window = next(c for c in chunks if c.chunk_id == "POL-001#1")
    assert refund_window.section.startswith("1. Eligibility")
    assert "30 calendar days" in refund_window.text
    assert refund_window.doc_title == "Refund and Return Policy"
    # untrusted FAQ is indexed too (we must handle it, not hide it)
    assert any(c.doc_id == "FAQ-COMMUNITY" for c in chunks)


def test_local_embedder_is_deterministic_and_normalised():
    e = LocalHashEmbedder()
    a = e.embed_query("refund window")
    b = e.embed_query("refund window")
    assert (a == b).all()
    assert abs(float((a * a).sum()) - 1.0) < 1e-5


def test_search_retrieves_refund_policy(policy_dir):
    idx = PolicyIndex.build_from_dir(LocalHashEmbedder(), policy_dir)
    hits = idx.search("How many days do I have to request a refund after delivery?", k=3)
    assert hits, "no hits"
    assert any(h.chunk.doc_id == "POL-001" for h in hits)


def test_search_retrieves_warranty_for_warranty_query(policy_dir):
    idx = PolicyIndex.build_from_dir(LocalHashEmbedder(), policy_dir)
    hits = idx.search("warranty coverage for electronics", k=3)
    assert hits[0].chunk.doc_id == "POL-004"


def test_index_save_load_roundtrip(policy_dir, tmp_path: Path):
    e = LocalHashEmbedder()
    idx = PolicyIndex.build_from_dir(e, policy_dir)
    idx.save(tmp_path / "index")
    loaded = PolicyIndex.load(e, tmp_path / "index")
    assert len(loaded.chunks) == len(idx.chunks)
    assert loaded.search("refund", k=1)[0].chunk.chunk_id == idx.search("refund", k=1)[0].chunk.chunk_id


def test_index_rejects_mismatched_embedder(policy_dir, tmp_path: Path):
    import pytest

    PolicyIndex.build_from_dir(LocalHashEmbedder(), policy_dir).save(tmp_path / "index")

    class Other(LocalHashEmbedder):
        name = "other-embedder"

    with pytest.raises(IndexMismatch):
        PolicyIndex.load(Other(), tmp_path / "index")
