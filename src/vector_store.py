"""ChromaDB persistence for citable evidence.

The main collection contains only ``citable=true`` chunks. Because the current
corpus is seed-only, a separately named inspection collection stores seed
chunks for pipeline visibility. The inspection collection is never used by
retrieval or citations.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from src.config import Settings
from src.embedder import load_chunks
from src.models import Chunk

COLLECTION_PREFIX = "mf_facts_v1"
BATCH_SIZE = 128


class VectorStoreError(RuntimeError):
    """Raised when the vector store cannot be built or queried safely."""


class RebuildRequiredError(VectorStoreError):
    """Raised when stored vectors do not match the current config or manifest."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _persist_dir(settings: Settings) -> Path:
    raw = settings.data.get("vector_db", {}).get("persist_dir")
    if raw:
        path = Path(str(raw))
        return path if path.is_absolute() else settings.root / path
    return settings.path("vector") / "chroma"


def _client(settings: Settings):
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    persist_dir = _persist_dir(settings)
    persist_dir.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(
        path=str(persist_dir),
        settings=ChromaSettings(anonymized_telemetry=False),
    )


def _digest(manifest_hash: str) -> str:
    return manifest_hash.removeprefix("sha256:")[:12]


def _collection_names(settings: Settings, manifest: dict[str, Any]) -> tuple[str, str]:
    prefix = str(settings.data.get("vector_db", {}).get("collection_prefix", COLLECTION_PREFIX))
    digest = _digest(str(manifest["manifest_hash"]))
    return f"{prefix}_{digest}", f"{prefix}_{digest}_seed"


def _validate_norm(vectors: np.ndarray, label: str) -> None:
    if not np.isfinite(vectors).all():
        raise VectorStoreError(f"{label} contains NaN or Inf")
    norms = np.linalg.norm(vectors, axis=1)
    if not np.allclose(norms, 1.0, atol=1e-4):
        raise VectorStoreError(f"{label} is not L2-normalized")


def load_index_inputs(settings: Settings):
    """Load and cross-check the embedding artifacts used for indexing."""
    manifest_path = settings.path("embeddings") / "manifest.json"
    if not manifest_path.exists():
        raise VectorStoreError(
            f"Embedding manifest not found: {manifest_path}. Run `embed` first."
        )
    manifest = _read_json(manifest_path)
    chunks = load_chunks(settings)
    all_vectors = np.load(settings.path("embeddings") / "embeddings.npy")
    citable_vectors = np.load(settings.path("embeddings") / "embeddings_citable.npy")
    all_map = _read_json(settings.path("embeddings") / "chunk_id_map.json")
    citable_map = _read_json(
        settings.path("embeddings") / "chunk_id_map_citable.json"
    )

    if manifest.get("config_hash") != settings.config_hash:
        raise RebuildRequiredError(
            "Embedding manifest config hash does not match config.yaml; "
            "re-run `embed` before indexing."
        )

    chunk_ids = [chunk.chunk_id for chunk in chunks]
    citable_ids = [chunk.chunk_id for chunk in chunks if chunk.citable]
    dimension = int(manifest.get("dimension", 0))

    if all_vectors.ndim != 2 or all_vectors.shape[1] != dimension:
        raise VectorStoreError(
            f"Embedding shape {all_vectors.shape} does not match dimension {dimension}"
        )
    citable_vectors = citable_vectors.reshape((-1, dimension))
    if manifest.get("chunk_count") != len(chunks):
        raise VectorStoreError(
            f"Manifest chunk count {manifest.get('chunk_count')} != chunks {len(chunks)}"
        )
    if all_vectors.shape[0] != len(chunks):
        raise VectorStoreError(
            f"Embedding rows {all_vectors.shape[0]} != chunks {len(chunks)}"
        )
    if manifest.get("citable_chunk_count") != len(citable_ids):
        raise VectorStoreError(
            f"Manifest citable count {manifest.get('citable_chunk_count')} "
            f"!= citable chunks {len(citable_ids)}"
        )
    if citable_vectors.shape[0] != len(citable_ids):
        raise VectorStoreError(
            f"Citable embedding rows {citable_vectors.shape[0]} "
            f"!= citable chunks {len(citable_ids)}"
        )
    if all_map.get("chunk_ids") != chunk_ids:
        raise VectorStoreError("chunk_id_map.json does not match chunks.jsonl")
    if citable_map.get("chunk_ids") != citable_ids:
        raise VectorStoreError("chunk_id_map_citable.json does not match citable chunks")
    if manifest.get("chunk_ids") != chunk_ids:
        raise VectorStoreError("Manifest chunk IDs do not match chunks.jsonl")

    _validate_norm(all_vectors, "embeddings.npy")
    if citable_vectors.shape[0]:
        _validate_norm(citable_vectors, "embeddings_citable.npy")

    return chunks, all_vectors, citable_vectors, all_map, citable_map, manifest


def _metadata(chunk: Chunk) -> dict[str, Any]:
    retrieved_at = chunk.retrieved_at.isoformat() if chunk.retrieved_at else ""
    return {
        "chunk_id": chunk.chunk_id,
        "source_id": chunk.source_id,
        "source_url": chunk.source_url,
        "source_type": chunk.source_type,
        "citable": chunk.citable,
        "page_type": chunk.page_type,
        "scheme": chunk.scheme,
        "scheme_slug": chunk.scheme_slug,
        "fact_type": chunk.fact_type,
        "section": chunk.section,
        "page": int(chunk.page or 0),
        "token_count": int(chunk.token_count),
        "content_hash": chunk.content_hash,
        "chunker_version": chunk.chunker_version,
        "retrieved_at": retrieved_at,
    }


def _records(
    chunks: list[Chunk],
    vectors: np.ndarray,
    row_index: dict[str, int],
    *,
    citable: bool,
) -> list[tuple[Chunk, np.ndarray]]:
    records: list[tuple[Chunk, np.ndarray]] = []
    for chunk in chunks:
        if chunk.citable != citable:
            continue
        try:
            row = row_index[chunk.chunk_id]
        except KeyError as exc:
            raise VectorStoreError(
                f"Missing embedding row for chunk {chunk.chunk_id}"
            ) from exc
        records.append((chunk, vectors[row]))
    return records


def _upsert(collection, records: list[tuple[Chunk, np.ndarray]]) -> None:
    for start in range(0, len(records), BATCH_SIZE):
        batch = records[start : start + BATCH_SIZE]
        if not batch:
            continue
        collection.upsert(
            ids=[chunk.chunk_id for chunk, _ in batch],
            embeddings=np.vstack([vector for _, vector in batch]),
            documents=[chunk.text for chunk, _ in batch],
            metadatas=[_metadata(chunk) for chunk, _ in batch],
        )


def _collection_metadata(
    manifest: dict[str, Any], settings: Settings, *, purpose: str
) -> dict[str, Any]:
    cfg = settings.data.get("vector_db", {})
    return {
        "hnsw:space": str(cfg.get("distance", "cosine")),
        "manifest_hash": str(manifest["manifest_hash"]),
        "config_hash": str(manifest["config_hash"]),
        "embedding_model": str(manifest["embedding_model"]),
        "model_revision": str(manifest["resolved_revision"]),
        "dimension": int(manifest["dimension"]),
        "chunker_version": str(manifest["chunker_version"]),
        "corpus_hash": str(manifest["corpus_hash"]),
        "citable_only": purpose == "main",
        "inspection_only": purpose == "seed_inspection",
        "purpose": purpose,
    }


def _get_or_create(client, name: str, metadata: dict[str, Any]):
    return client.get_or_create_collection(
        name=name,
        metadata=metadata,
        embedding_function=None,
    )


def _where(**conditions: Any) -> dict[str, Any]:
    clauses = [{key: value} for key, value in conditions.items() if value is not None]
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def _smoke_queries(
    collection,
    chunks: list[Chunk],
    vectors: np.ndarray,
    row_index: dict[str, int],
    *,
    citable: bool,
    mode: str,
) -> list[dict[str, Any]]:
    priority = [
        "expense_ratio",
        "amc_overview",
        "riskometer",
        "benchmark",
        "exit_load",
        "min_sip",
        "lock_in",
        "statement_process",
    ]
    by_scheme: dict[str, list[Chunk]] = {}
    for chunk in chunks:
        if chunk.citable == citable:
            by_scheme.setdefault(chunk.scheme_slug, []).append(chunk)

    results: list[dict[str, Any]] = []
    for scheme_slug in sorted(by_scheme):
        candidates = by_scheme[scheme_slug]
        expected = next(
            (
                chunk
                for fact_type in priority
                for chunk in candidates
                if chunk.fact_type == fact_type
            ),
            candidates[0],
        )
        query = collection.query(
            query_embeddings=[vectors[row_index[expected.chunk_id]]],
            n_results=min(3, max(1, len(candidates))),
            where=_where(
                scheme_slug=scheme_slug,
                fact_type=expected.fact_type,
                citable=citable,
            ),
            include=["metadatas", "distances"],
        )
        ids = (query.get("ids") or [[]])[0]
        results.append(
            {
                "scheme_slug": scheme_slug,
                "fact_type": expected.fact_type,
                "expected_chunk_id": expected.chunk_id,
                "returned_chunk_ids": ids,
                "passed": expected.chunk_id in ids,
                "mode": mode,
            }
        )
    return results


def _write_report(settings: Settings, report: dict[str, Any]) -> Path:
    out = settings.path("vector") / "collection_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def build_vector_store(settings: Settings) -> dict[str, Any]:
    """Build the citable collection and a non-citable seed inspection collection."""
    (
        chunks,
        all_vectors,
        citable_vectors,
        all_map,
        citable_map,
        manifest,
    ) = load_index_inputs(settings)

    client = _client(settings)
    try:
        main_name, seed_name = _collection_names(settings, manifest)
        main_meta = _collection_metadata(manifest, settings, purpose="main")
        seed_meta = _collection_metadata(manifest, settings, purpose="seed_inspection")

        main = _get_or_create(client, main_name, main_meta)
        seed = _get_or_create(client, seed_name, seed_meta)

        main_records = _records(
            chunks, citable_vectors, citable_map["index"], citable=True
        )
        seed_records = _records(chunks, all_vectors, all_map["index"], citable=False)
        _upsert(main, main_records)
        _upsert(seed, seed_records)

        main_count = main.count()
        seed_count = seed.count()
        expected_main = len(main_records)
        expected_seed = len(seed_records)
        if main_count != expected_main:
            raise VectorStoreError(
                f"Main collection count {main_count} != expected {expected_main}"
            )
        if seed_count != expected_seed:
            raise VectorStoreError(
                f"Seed inspection count {seed_count} != expected {expected_seed}"
            )

        if main_count:
            smoke_mode = "citable"
            smoke = _smoke_queries(
                main,
                chunks,
                citable_vectors,
                citable_map["index"],
                citable=True,
                mode=smoke_mode,
            )
        elif seed_count:
            smoke_mode = "inspection_only"
            smoke = _smoke_queries(
                seed,
                chunks,
                all_vectors,
                all_map["index"],
                citable=False,
                mode=smoke_mode,
            )
        else:
            smoke_mode = "none"
            smoke = []

        warnings: list[str] = []
        if not main_count:
            warnings.append(
                "No citable chunks are available. The main collection is empty; "
                "add official HDFC AMC/SEBI/AMFI/registrar sources, then re-run "
                "load, clean, chunk, embed, and index."
            )
        if smoke_mode == "inspection_only":
            warnings.append(
                "Smoke queries ran against the non-citable seed inspection collection. "
                "They verify vector storage only and are not citation evidence."
            )
        if seed_count == 0 and main_count == 0:
            warnings.append("No chunks are available to index.")
        if any(not item["passed"] for item in smoke):
            warnings.append("One or more smoke queries failed top-3 retrieval.")

        report = {
            "generated_at": _now().isoformat(),
            "status": (
                "citable_ready" if main_count else ("seed_only" if seed_count else "empty")
            ),
            "collection_name": main_name,
            "collection_count": main_count,
            "expected_citable_count": expected_main,
            "seed_inspection_collection": seed_name,
            "seed_inspection_count": seed_count,
            "expected_seed_count": expected_seed,
            "manifest_hash": manifest["manifest_hash"],
            "config_hash": manifest["config_hash"],
            "embedding_model": manifest["embedding_model"],
            "model_revision": manifest["resolved_revision"],
            "dimension": manifest["dimension"],
            "distance": str(settings.data.get("vector_db", {}).get("distance", "cosine")),
            "chunker_version": manifest["chunker_version"],
            "corpus_hash": manifest["corpus_hash"],
            "smoke_mode": smoke_mode,
            "smoke_passed": bool(smoke) and all(item["passed"] for item in smoke),
            "smoke_queries": smoke,
            "warnings": warnings,
        }
        _write_report(settings, report)
        return report
    finally:
        client.close()


def _current_collection(settings: Settings):
    manifest_path = settings.path("embeddings") / "manifest.json"
    if not manifest_path.exists():
        raise VectorStoreError("Embedding manifest not found. Run `embed` first.")
    manifest = _read_json(manifest_path)
    if manifest.get("config_hash") != settings.config_hash:
        raise RebuildRequiredError(
            "Embedding manifest config hash does not match config.yaml; "
            "re-run `embed` and `index`."
        )
    main_name, _ = _collection_names(settings, manifest)
    client = _client(settings)
    try:
        collection = client.get_collection(main_name, embedding_function=None)
    except Exception as exc:  # noqa: BLE001 - normalize Chroma's missing-collection error
        client.close()
        raise VectorStoreError(
            f"Collection {main_name} is not built. Run `index` first."
        ) from exc
    metadata = collection.metadata or {}
    if metadata.get("manifest_hash") != manifest.get("manifest_hash"):
        client.close()
        raise RebuildRequiredError(
            "Collection manifest hash does not match embeddings. Run `index` again."
        )
    return client, collection, manifest


def query_citable(
    settings: Settings,
    query_embedding: np.ndarray,
    *,
    scheme_slug: str | None = None,
    fact_type: str | None = None,
    n_results: int = 12,
) -> list[dict[str, Any]]:
    """Query the main collection. The citable filter cannot be disabled."""
    client, collection, _ = _current_collection(settings)
    try:
        if collection.count() == 0:
            raise VectorStoreError(
                "No citable chunks are indexed. Add official sources and rebuild."
            )

        vector = np.asarray(query_embedding, dtype=np.float32).reshape(-1)
        where = _where(
            citable=True,
            scheme_slug=scheme_slug,
            fact_type=fact_type,
        )

        result = collection.query(
            query_embeddings=[vector],
            n_results=max(1, min(int(n_results), collection.count())),
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        rows: list[dict[str, Any]] = []
        for row, chunk_id in enumerate((result.get("ids") or [[]])[0]):
            metadata = (result.get("metadatas") or [[]])[0][row] or {}
            distance = (result.get("distances") or [[]])[0][row]
            rows.append(
                {
                    "chunk_id": chunk_id,
                    "document": (result.get("documents") or [[]])[0][row],
                    "metadata": metadata,
                    "distance": float(distance),
                    "similarity": float(1.0 - distance),
                }
            )
        return rows
    finally:
        client.close()


def run_smoke(settings: Settings) -> dict[str, Any]:
    """Run and persist smoke queries for the current collection."""
    report_path = settings.path("vector") / "collection_report.json"
    if not report_path.exists():
        raise VectorStoreError("No collection report found. Run `index` first.")
    report = build_vector_store(settings)
    if not report["smoke_queries"]:
        raise VectorStoreError("No chunks are available for smoke queries.")
    return report
