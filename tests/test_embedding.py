"""Tests for MiniLM embedding artifacts, cache behavior, and manifests."""

from __future__ import annotations

import json

import numpy as np

from src.config import Settings
from src.embedder import embed_chunks, write_embedding_artifacts
from src.models import Chunk


class FakeMiniLM:
    """Small deterministic stand-in for the real 384-dimension MiniLM model."""

    def __init__(self) -> None:
        self.encoded_texts = 0

    def get_embedding_dimension(self) -> int:
        return 384

    def encode(self, texts, **kwargs):
        self.encoded_texts += len(texts)
        vectors = np.zeros((len(texts), 384), dtype=np.float32)
        for row, text in enumerate(texts):
            seed = sum(ord(char) for char in text)
            vectors[row, seed % 384] = 1.0
            vectors[row, (seed * 7 + 11) % 384] = 0.25
        return vectors


def _settings(tmp_path) -> Settings:
    return Settings(
        root=tmp_path,
        data={
            "paths": {"embeddings": "data/04_embeddings"},
            "sources": {},
            "embedding": {
                "model": "sentence-transformers/all-MiniLM-L6-v2",
                "device": "cpu",
                "batch_size": 32,
                "normalize": True,
                "max_seq_length": 256,
            },
        },
    )


def _chunk(chunk_id: str, text: str, *, citable: bool) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        source_id="source_" + chunk_id,
        source_url="https://www.hdfcfund.com/example",
        source_type="amc" if citable else "seed",
        citable=citable,
        page_type="scheme",
        scheme="HDFC Large Cap Fund Direct Growth",
        scheme_slug="hdfc-large-cap-fund-direct-growth",
        fact_type="expense_ratio",
        section="Key facts",
        text=text,
        token_count=len(text.split()),
        char_count=len(text),
        content_hash="sha256:" + chunk_id,
        chunker_version="1.0",
    )


def test_embeddings_are_normalized_and_seed_chunks_are_excluded(tmp_path):
    chunks = [
        _chunk("seed__expense", "Seed expense ratio 1.05%", citable=False),
        _chunk("amc__expense", "Official expense ratio 1.02%", citable=True),
    ]
    settings = _settings(tmp_path)
    model = FakeMiniLM()

    all_vectors, citable_vectors, manifest = embed_chunks(
        chunks, settings, model=model, model_revision="test-revision"
    )
    write_embedding_artifacts(chunks, all_vectors, citable_vectors, manifest, settings)

    assert all_vectors.shape == (2, 384)
    assert citable_vectors.shape == (1, 384)
    assert np.isfinite(all_vectors).all()
    assert np.allclose(np.linalg.norm(all_vectors, axis=1), 1.0, atol=1e-5)
    assert manifest["chunk_count"] == 2
    assert manifest["citable_chunk_count"] == 1

    out = settings.path("embeddings")
    citable_map = json.loads((out / "chunk_id_map_citable.json").read_text())
    assert citable_map["chunk_ids"] == ["amc__expense"]
    assert "seed__expense" not in citable_map["index"]
    assert (out / "embeddings.npy").exists()
    assert (out / "embeddings_citable.npy").exists()
    assert (out / "manifest.json").exists()


def test_cache_hit_avoids_reembedding_and_keeps_manifest_hash(tmp_path):
    chunks = [_chunk("one", "First fact text", citable=True)]
    settings = _settings(tmp_path)

    first_model = FakeMiniLM()
    _, _, first = embed_chunks(
        chunks, settings, model=first_model, model_revision="test-revision"
    )
    second_model = FakeMiniLM()
    _, _, second = embed_chunks(
        chunks, settings, model=second_model, model_revision="test-revision"
    )

    assert first_model.encoded_texts == 1
    assert first["cache_misses"] == 1
    assert first["cache_hits"] == 0
    assert second_model.encoded_texts == 0
    assert second["cache_misses"] == 0
    assert second["cache_hits"] == 1
    assert first["manifest_hash"] == second["manifest_hash"]


def test_revision_or_config_change_changes_manifest_hash(tmp_path):
    chunks = [_chunk("one", "First fact text", citable=True)]
    settings = _settings(tmp_path)
    model = FakeMiniLM()

    _, _, baseline = embed_chunks(
        chunks, settings, model=model, model_revision="revision-a"
    )
    _, _, new_revision = embed_chunks(
        chunks, settings, model=model, model_revision="revision-b"
    )

    settings.data["embedding"]["batch_size"] = 16
    _, _, new_config = embed_chunks(
        chunks, settings, model=model, model_revision="revision-a"
    )

    assert baseline["manifest_hash"] != new_revision["manifest_hash"]
    assert baseline["manifest_hash"] != new_config["manifest_hash"]
