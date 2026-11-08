"""MiniLM embeddings with a content-addressed cache and manifest."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from src.config import Settings
from src.models import Chunk

EMBEDDING_DIMENSION = 384


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_hash(value: dict[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + _sha256(payload)


def _cache_key(text: str, model_name: str, revision: str) -> str:
    return _sha256(f"{text}|{model_name}|{revision}")


def load_chunks(settings: Settings) -> list[Chunk]:
    path = settings.path("chunks") / "chunks.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"Chunks file not found: {path}")
    chunks: list[Chunk] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                chunks.append(Chunk.model_validate_json(line))
    return chunks


def load_model(settings: Settings):
    from sentence_transformers import SentenceTransformer

    cfg = settings.data.get("embedding", {})
    model_name = cfg.get("model", "sentence-transformers/all-MiniLM-L6-v2")
    kwargs: dict[str, Any] = {
        "device": cfg.get("device", "cpu"),
        "local_files_only": False,
    }
    revision = cfg.get("revision")
    if revision:
        kwargs["revision"] = revision
    model = SentenceTransformer(model_name, **kwargs)
    model.max_seq_length = int(cfg.get("max_seq_length", 256))
    return model


def _resolve_revision(model_name: str, configured_revision: str | None) -> str:
    if configured_revision:
        return configured_revision
    try:
        from huggingface_hub import snapshot_download

        path = snapshot_download(model_name, local_files_only=True)
        return Path(path).name
    except Exception:  # noqa: BLE001 - revision metadata is best-effort
        return "unknown"


def _load_cached(path: Path, dimension: int) -> np.ndarray | None:
    if not path.exists():
        return None
    try:
        vector = np.load(path)
    except Exception:  # noqa: BLE001 - corrupt cache is a miss
        return None
    if vector.shape != (dimension,) or not np.isfinite(vector).all():
        return None
    return vector.astype(np.float32)


def _l2_normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (vectors / norms).astype(np.float32)


def embed_chunks(
    chunks: list[Chunk],
    settings: Settings,
    *,
    model=None,
    model_revision: str | None = None,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    cfg = settings.data.get("embedding", {})
    model_name = str(cfg.get("model", "sentence-transformers/all-MiniLM-L6-v2"))
    batch_size = int(cfg.get("batch_size", 32))
    normalize = bool(cfg.get("normalize", True))
    max_seq_length = int(cfg.get("max_seq_length", 256))

    if model is None:
        model = load_model(settings)
    resolved_revision = model_revision or _resolve_revision(
        model_name, cfg.get("revision")
    )
    dimension = int(model.get_embedding_dimension())
    if dimension != EMBEDDING_DIMENSION:
        raise ValueError(
            f"Expected {EMBEDDING_DIMENSION}-dim MiniLM vectors, got {dimension}"
        )

    cache_dir = settings.path("embeddings") / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_hits = 0
    cache_misses = 0
    vectors: list[np.ndarray | None] = [None] * len(chunks)
    missing_texts: list[str] = []
    missing_rows: list[int] = []

    for row, chunk in enumerate(chunks):
        key = _cache_key(chunk.text, model_name, resolved_revision)
        cached = _load_cached(cache_dir / f"{key}.npy", dimension)
        if cached is not None:
            vectors[row] = cached
            cache_hits += 1
        else:
            missing_rows.append(row)
            missing_texts.append(chunk.text)

    if missing_texts:
        encoded = model.encode(
            missing_texts,
            batch_size=batch_size,
            normalize_embeddings=False,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        encoded = np.asarray(encoded, dtype=np.float32)
        if encoded.shape != (len(missing_texts), dimension):
            raise ValueError(
                f"Unexpected encoding shape {encoded.shape} for {len(missing_texts)} texts"
            )
        if not np.isfinite(encoded).all():
            raise ValueError("Embedding output contains NaN or Inf")
        if normalize:
            encoded = _l2_normalize(encoded)
        for row, text, vector in zip(missing_rows, missing_texts, encoded, strict=True):
            vectors[row] = vector
            key = _cache_key(text, model_name, resolved_revision)
            np.save(cache_dir / f"{key}.npy", vector.astype(np.float32))
            cache_misses += 1

    if any(vector is None for vector in vectors):
        raise ValueError("Some chunks were not embedded")

    if not chunks:
        all_embeddings = np.zeros((0, dimension), dtype=np.float32)
    else:
        all_embeddings = np.vstack([vector for vector in vectors if vector is not None])
        all_embeddings = all_embeddings.astype(np.float32)
        if normalize and not np.allclose(
            np.linalg.norm(all_embeddings, axis=1), 1.0, atol=1e-4
        ):
            raise ValueError("Embeddings are not L2-normalized")

    citable_rows = [row for row, chunk in enumerate(chunks) if chunk.citable]
    citable_embeddings = all_embeddings[citable_rows].astype(np.float32)

    corpus_hash = _canonical_hash(
        {
            "chunk_ids": [chunk.chunk_id for chunk in chunks],
            "content_hashes": [chunk.content_hash for chunk in chunks],
        }
    )
    chunk_ids = [chunk.chunk_id for chunk in chunks]

    stable_manifest = {
        "embedding_model": model_name,
        "resolved_revision": resolved_revision,
        "dimension": dimension,
        "normalization": "l2" if normalize else "none",
        "dtype": "float32",
        "max_seq_length": max_seq_length,
        "batch_size": batch_size,
        "device": cfg.get("device", "cpu"),
        "chunk_count": len(chunks),
        "citable_chunk_count": len(citable_rows),
        "config_hash": settings.config_hash,
        "chunker_version": chunks[0].chunker_version if chunks else "1.0",
        "corpus_hash": corpus_hash,
        "chunk_ids": chunk_ids,
    }
    manifest = {
        **stable_manifest,
        "created_at": _now().isoformat(),
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "embeddings_file": "data/04_embeddings/embeddings.npy",
        "citable_embeddings_file": "data/04_embeddings/embeddings_citable.npy",
        "chunk_id_map_file": "data/04_embeddings/chunk_id_map.json",
        "citable_chunk_id_map_file": "data/04_embeddings/chunk_id_map_citable.json",
        "manifest_hash": _canonical_hash(stable_manifest),
    }
    return all_embeddings, citable_embeddings, manifest


def embed_query(
    text: str,
    settings: Settings,
    *,
    model=None,
) -> np.ndarray:
    """Embed one retrieval query with the same model and normalization as ingestion."""
    cfg = settings.data.get("embedding", {})
    normalize = bool(cfg.get("normalize", True))
    if model is None:
        model = load_model(settings)
    vector = model.encode(
        [text],
        batch_size=int(cfg.get("batch_size", 32)),
        normalize_embeddings=False,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    vector = np.asarray(vector, dtype=np.float32).reshape(-1)
    if vector.shape != (EMBEDDING_DIMENSION,):
        raise ValueError(
            f"Expected a {EMBEDDING_DIMENSION}-dim query vector, got {vector.shape}"
        )
    if not np.isfinite(vector).all():
        raise ValueError("Query embedding contains NaN or Inf")
    if normalize:
        norm = float(np.linalg.norm(vector))
        if norm == 0:
            raise ValueError("Query embedding has zero norm")
        vector = (vector / norm).astype(np.float32)
    return vector


def write_embedding_artifacts(
    chunks: list[Chunk],
    all_embeddings: np.ndarray,
    citable_embeddings: np.ndarray,
    manifest: dict[str, Any],
    settings: Settings,
) -> None:
    out_dir = settings.path("embeddings")
    out_dir.mkdir(parents=True, exist_ok=True)

    np.save(out_dir / "embeddings.npy", all_embeddings)
    np.save(out_dir / "embeddings_citable.npy", citable_embeddings)

    chunk_ids = [chunk.chunk_id for chunk in chunks]
    all_map = {
        "count": len(chunk_ids),
        "chunk_ids": chunk_ids,
        "index": {chunk_id: row for row, chunk_id in enumerate(chunk_ids)},
    }
    citable_map = {
        "count": int(citable_embeddings.shape[0]),
        "chunk_ids": [chunk.chunk_id for chunk in chunks if chunk.citable],
        "index": {
            chunk.chunk_id: row
            for row, chunk in enumerate([chunk for chunk in chunks if chunk.citable])
        },
    }
    (out_dir / "chunk_id_map.json").write_text(
        json.dumps(all_map, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "chunk_id_map_citable.json").write_text(
        json.dumps(citable_map, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
