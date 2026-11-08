"""Tests for the Chroma vector-store phase."""

from __future__ import annotations

import json

import numpy as np
import pytest

from src.config import Settings
from src.embedder import embed_chunks, write_embedding_artifacts
from src.models import Chunk
from src.vector_store import (
    RebuildRequiredError,
    VectorStoreError,
    build_vector_store,
    query_citable,
)


class FakeMiniLM:
    def get_embedding_dimension(self) -> int:
        return 384

    def encode(self, texts, **kwargs):
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
            "paths": {
                "chunks": "data/03_chunks",
                "embeddings": "data/04_embeddings",
                "vector": "data/05_vector",
            },
            "sources": {},
            "embedding": {
                "model": "sentence-transformers/all-MiniLM-L6-v2",
                "device": "cpu",
                "batch_size": 32,
                "normalize": True,
                "max_seq_length": 256,
            },
            "vector_db": {
                "collection_prefix": "mf_facts_v1",
                "distance": "cosine",
            },
        },
    )


def _chunk(
    chunk_id: str,
    text: str,
    *,
    citable: bool,
    scheme_slug: str = "hdfc-large-cap-fund-direct-growth",
    fact_type: str = "expense_ratio",
) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        source_id="source_" + chunk_id,
        source_url="https://www.hdfcfund.com/example",
        source_type="amc" if citable else "seed",
        citable=citable,
        page_type="scheme",
        scheme="HDFC Large Cap Fund Direct Growth",
        scheme_slug=scheme_slug,
        fact_type=fact_type,
        section="Key facts",
        text=text,
        token_count=len(text.split()),
        char_count=len(text),
        content_hash="sha256:" + chunk_id,
        chunker_version="1.0",
    )


def _prepare(tmp_path, chunks: list[Chunk]):
    settings = _settings(tmp_path)
    all_vectors, citable_vectors, manifest = embed_chunks(
        chunks, settings, model=FakeMiniLM(), model_revision="test-revision"
    )
    write_embedding_artifacts(chunks, all_vectors, citable_vectors, manifest, settings)
    chunks_dir = settings.path("chunks")
    chunks_dir.mkdir(parents=True, exist_ok=True)
    with (chunks_dir / "chunks.jsonl").open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(chunk.model_dump_json() + "\n")
    return settings, all_vectors, citable_vectors, manifest


def _fixture_chunks() -> list[Chunk]:
    return [
        _chunk("seed_one", "Seed expense ratio 1.05%", citable=False),
        _chunk("amc_one", "Official expense ratio 1.02%", citable=True),
        _chunk(
            "amc_two",
            "Official exit load 1%",
            citable=True,
            fact_type="exit_load",
        ),
    ]


def test_main_collection_excludes_seed_chunks_and_smoke_passes(tmp_path):
    chunks = _fixture_chunks()
    settings, _, citable_vectors, manifest = _prepare(tmp_path, chunks)

    report = build_vector_store(settings)

    assert report["status"] == "citable_ready"
    assert report["collection_count"] == 2
    assert report["expected_citable_count"] == 2
    assert report["seed_inspection_count"] == 1
    assert report["smoke_mode"] == "citable"
    assert report["smoke_passed"] is True
    assert all(item["passed"] for item in report["smoke_queries"])

    citable_map = json.loads(
        (settings.path("embeddings") / "chunk_id_map_citable.json").read_text()
    )
    vector = citable_vectors[citable_map["index"]["amc_one"]]
    hits = query_citable(
        settings,
        vector,
        scheme_slug="hdfc-large-cap-fund-direct-growth",
        fact_type="expense_ratio",
    )

    assert hits
    assert hits[0]["chunk_id"] == "amc_one"
    assert all(hit["metadata"]["citable"] is True for hit in hits)
    assert "seed_one" not in {hit["chunk_id"] for hit in hits}


def test_reindex_is_idempotent(tmp_path):
    chunks = _fixture_chunks()
    settings, _, _, _ = _prepare(tmp_path, chunks)

    first = build_vector_store(settings)
    second = build_vector_store(settings)

    assert first["collection_name"] == second["collection_name"]
    assert first["collection_count"] == second["collection_count"] == 2
    assert first["seed_inspection_count"] == second["seed_inspection_count"] == 1


def test_config_mismatch_requires_rebuild(tmp_path):
    chunks = _fixture_chunks()
    settings, all_vectors, _, _ = _prepare(tmp_path, chunks)
    build_vector_store(settings)

    settings.data["embedding"]["batch_size"] = 16

    with pytest.raises(RebuildRequiredError):
        build_vector_store(settings)
    with pytest.raises(RebuildRequiredError):
        query_citable(settings, all_vectors[0])


def test_empty_citable_collection_cannot_be_queried(tmp_path):
    chunks = [_chunk("seed_only", "Seed expense ratio 1.05%", citable=False)]
    settings, all_vectors, _, _ = _prepare(tmp_path, chunks)
    report = build_vector_store(settings)

    assert report["status"] == "seed_only"
    assert report["collection_count"] == 0
    assert report["smoke_mode"] == "inspection_only"

    with pytest.raises(VectorStoreError):
        query_citable(settings, all_vectors[0])
