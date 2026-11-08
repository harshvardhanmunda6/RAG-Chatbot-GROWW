"""Read-only artifact helpers for the Streamlit UI.

The UI never talks to ChromaDB or the LLM directly. It reads the files that
the ingestion, retrieval, and answer stages already wrote, so the Pipeline tab
can inspect every stage without triggering a new query.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import Settings
from src.providers import resolve_provider
from src.source_registry import load_registry


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return default


def read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return default


def read_jsonl(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if limit is not None and len(rows) >= limit:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except (FileNotFoundError, OSError):
        pass
    return rows


def sorted_files(directory: Path, pattern: str = "*.json") -> list[Path]:
    if not directory.exists():
        return []
    return sorted(
        (path for path in directory.glob(pattern) if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def latest_file(directory: Path, pattern: str = "*.json") -> Path | None:
    files = sorted_files(directory, pattern)
    return files[0] if files else None


def short_hash(value: str | None, length: int = 12) -> str:
    if not value:
        return "—"
    text = str(value)
    if text.startswith("sha256:"):
        text = text[7:]
    return text[:length] + ("…" if len(text) > length else "")


def format_timestamp(value: Any) -> str:
    if value is None or value == "":
        return "—"
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return text


def format_bytes(value: Any) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if amount < 1024 or unit == "GB":
            return f"{amount:,.0f} {unit}" if unit == "B" else f"{amount:,.1f} {unit}"
        amount /= 1024
    return f"{amount:,.1f} GB"


def corpus_state(settings: Settings) -> dict[str, Any]:
    """Return a small, secret-free snapshot of index health."""
    report = read_json(settings.path("vector") / "collection_report.json", {}) or {}
    manifest = read_json(settings.path("embeddings") / "manifest.json", {}) or {}
    count = int(report.get("collection_count", 0) or 0)
    config_hash = str(report.get("config_hash", ""))
    manifest_hash = str(report.get("manifest_hash", ""))
    expected_manifest = str(manifest.get("manifest_hash", ""))

    if not report:
        state = "missing"
        message = "Corpus not indexed. Run `python3 -m src.cli ingest --stage index`."
    elif config_hash and config_hash != settings.config_hash:
        state = "stale"
        message = "Index is stale — rebuild embeddings and the vector collection."
    elif manifest_hash and expected_manifest and manifest_hash != expected_manifest:
        state = "stale"
        message = "Embedding manifest mismatch — rebuild the vector collection."
    elif count < 1:
        state = "seed_only"
        message = (
            "Seed-only corpus: the main collection has no citable official chunks. "
            "Factual answers will return a no-answer until official sources are added."
        )
    else:
        state = "ready"
        message = f"{count} citable chunk{'s' if count != 1 else ''} indexed."

    return {
        "state": state,
        "message": message,
        "count": count,
        "report": report,
        "manifest": manifest,
        "config_hash": config_hash,
        "manifest_hash": manifest_hash,
        "expected_manifest": expected_manifest,
        "seed_count": int(report.get("seed_inspection_count", 0) or 0),
        "status": report.get("status", "missing"),
        "collection_name": report.get("collection_name", "—"),
    }


def llm_state(settings: Settings) -> dict[str, Any]:
    """Describe the configured provider without making a network request."""
    provider, model, error = resolve_provider(settings)
    if error:
        return {
            "mode": "extractive",
            "label": "Extractive mode — LLM unavailable",
            "detail": _humanize_llm_error(error),
            "provider": None,
            "model": None,
        }
    return {
        "mode": "llm",
        "label": "LLM ready",
        "detail": f"{getattr(provider, 'name', '?')} · {model}",
        "provider": getattr(provider, "name", None),
        "model": model,
    }


def _humanize_llm_error(error: str) -> str:
    labels = {
        "missing_model": "LLM_MODEL is not set",
        "missing_api_key": "provider API key is not set",
        "llm_unavailable": "provider is not configured",
    }
    if error in labels:
        return labels[error]
    if error.startswith("provider_error:"):
        return "provider configuration is invalid"
    if error.startswith("model_invalid:"):
        return "configured model is not available"
    return "provider is not configured"


def registry_rows(settings: Settings) -> tuple[list[dict[str, Any]], list[str]]:
    sources, errors = load_registry(settings)
    rows: list[dict[str, Any]] = []
    for source in sources:
        rows.append(
            {
                "source_id": source.source_id,
                "title": source.title,
                "publisher": source.publisher,
                "source_type": source.source_type,
                "status": source.status,
                "citable": source.citable,
                "scheme_slugs": ", ".join(source.scheme_slugs),
                "purpose": source.purpose,
                "url": source.url,
                "http_status": source.http_status,
                "fetched_at": source.fetched_at.isoformat() if source.fetched_at else "",
            }
        )
    return rows, errors


def source_checks(settings: Settings) -> list[dict[str, Any]]:
    payload = read_json(settings.path("sources") / "source_checks.json", {}) or {}
    rows = payload.get("sources", []) if isinstance(payload, dict) else []
    return [row for row in rows if isinstance(row, dict)]


def clean_documents(settings: Settings) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    directory = settings.path("clean")
    if not directory.exists():
        return rows
    for path in sorted(directory.glob("*.json")):
        payload = read_json(path, {})
        if not isinstance(payload, dict):
            continue
        sections = payload.get("sections", []) or []
        text = payload.get("clean_text", "") or ""
        rows.append(
            {
                "source_id": payload.get("source_id", path.stem),
                "title": payload.get("title", ""),
                "source_type": payload.get("source_type", ""),
                "scheme_slugs": ", ".join(payload.get("scheme_slugs", []) or []),
                "sections": len(sections),
                "chars": len(text),
                "removed_sections": ", ".join(payload.get("removed_sections", []) or []),
                "clean_text_hash": payload.get("clean_text_hash", ""),
                "content_hash": payload.get("content_hash", ""),
                "fetched_at": payload.get("fetched_at", ""),
                "path": str(path),
            }
        )
    return rows


def latest_retrieval_trace(settings: Settings) -> dict[str, Any] | None:
    path = latest_file(settings.path("retrieval"), "tr_*.json")
    if path is None:
        return None
    payload = read_json(path, None)
    return payload if isinstance(payload, dict) else None


def latest_answer(settings: Settings) -> dict[str, Any] | None:
    path = latest_file(settings.path("answers"), "tr_*.json")
    if path is None:
        return None
    payload = read_json(path, None)
    return payload if isinstance(payload, dict) else None


def answer_artifact(settings: Settings, trace_id: str) -> dict[str, Any] | None:
    if not trace_id:
        return None
    payload = read_json(settings.path("answers") / f"{trace_id}.json", None)
    return payload if isinstance(payload, dict) else None


def retrieval_artifact(settings: Settings, trace_id: str) -> dict[str, Any] | None:
    if not trace_id:
        return None
    payload = read_json(settings.path("retrieval") / f"{trace_id}.json", None)
    return payload if isinstance(payload, dict) else None


def prompt_artifact(settings: Settings, trace_id: str) -> str:
    return read_text(settings.path("answers") / f"{trace_id}.prompt.txt", "")


def raw_llm_artifact(settings: Settings, trace_id: str) -> dict[str, Any] | None:
    payload = read_json(settings.path("answers") / f"{trace_id}.llm_raw.json", None)
    return payload if isinstance(payload, dict) else None


def user_prompt_text(prompt: str) -> str:
    """Return only the user/context portion; never expose the system prompt."""
    marker = "\n\nUSER:\n"
    if marker in prompt:
        return prompt.split(marker, 1)[1]
    return prompt


def trace_rows(trace: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not trace:
        return []
    rows: list[dict[str, Any]] = []
    for rank, hit in enumerate(trace.get("candidates", []) or [], start=1):
        metadata = hit.get("metadata", {}) or {}
        rows.append(
            {
                "rank": rank,
                "chunk_id": hit.get("chunk_id", ""),
                "score": hit.get("score"),
                "similarity": hit.get("similarity"),
                "boost": hit.get("exact_fact_boost", 0),
                "scheme": metadata.get("scheme", ""),
                "fact_type": metadata.get("fact_type", ""),
                "citable": metadata.get("citable"),
                "source_type": metadata.get("source_type", ""),
                "selected": hit.get("chunk_id") in (trace.get("selected", []) or []),
            }
        )
    return rows


def selected_rows(trace: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not trace:
        return []
    rows: list[dict[str, Any]] = []
    for hit in trace.get("selected_hits", []) or []:
        metadata = hit.get("metadata", {}) or {}
        rows.append(
            {
                "chunk_id": hit.get("chunk_id", ""),
                "score": hit.get("score"),
                "scheme": metadata.get("scheme", ""),
                "fact_type": metadata.get("fact_type", ""),
                "source_type": metadata.get("source_type", ""),
                "text": hit.get("text", ""),
            }
        )
    return rows


def validation_rows(answer: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not answer:
        return []
    validation = answer.get("validation", {}) or {}
    rows = validation.get("checks", []) or []
    return [row for row in rows if isinstance(row, dict)]


def file_age_label(path: Path | None) -> str:
    if path is None or not path.exists():
        return "—"
    try:
        modified = datetime.fromtimestamp(path.stat().st_mtime)
        return modified.strftime("%Y-%m-%d %H:%M")
    except OSError:
        return "—"
