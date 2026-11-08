"""Resolve selected chunks and refusal intents to one safe official citation."""

from __future__ import annotations

from datetime import datetime, timezone

from src.config import Settings
from src.models import Citation, QueryPlan, SearchHit, SourceRecord
from src.source_registry import is_url_allowed, load_registry

OFFICIAL_SOURCE_TYPES = {"amc", "sebi", "amfi", "registrar"}
APPROVED_STATUSES = {"approved", "verified", "ok", "citable"}


def _date_for(source: SourceRecord, metadata: dict | None = None) -> str:
    value = source.last_modified or source.fetched_at
    if value is not None:
        return value.date().isoformat()
    retrieved = str((metadata or {}).get("retrieved_at", ""))[:10]
    if len(retrieved) == 10 and retrieved[4] == "-" and retrieved[7] == "-":
        return retrieved
    return datetime.now(timezone.utc).date().isoformat()


def _load_sources(settings: Settings) -> tuple[list[SourceRecord], str | None]:
    sources, errors = load_registry(settings)
    if errors:
        return [], "source_registry_invalid"
    return sources, None


def _is_safe_source(source: SourceRecord, settings: Settings) -> bool:
    return (
        source.citable
        and source.source_type in OFFICIAL_SOURCE_TYPES
        and source.status.lower() in APPROVED_STATUSES
        and source.url.startswith("https://")
        and is_url_allowed(source.url, settings.sources.get("allowed_domains", []))
    )


def resolve_citation(
    chunk_id: str | None,
    evidence: list[SearchHit],
    settings: Settings,
    plan: QueryPlan,
) -> tuple[Citation | None, str | None]:
    """Resolve one selected chunk to its approved registry source."""
    if not chunk_id:
        return None, "missing_citation_chunk_id"
    hit = next((item for item in evidence if item.chunk_id == chunk_id), None)
    if hit is None:
        return None, "citation_chunk_not_in_evidence"

    metadata = hit.metadata or {}
    if metadata.get("citable") is not True:
        return None, "citation_not_citable"
    if str(metadata.get("source_type", "")) not in OFFICIAL_SOURCE_TYPES:
        return None, "citation_source_not_official"
    if plan.scheme_slug and metadata.get("scheme_slug") != plan.scheme_slug:
        return None, "citation_scheme_mismatch"

    sources, error = _load_sources(settings)
    if error:
        return None, error
    source_id = str(metadata.get("source_id", ""))
    source = next((item for item in sources if item.source_id == source_id), None)
    if source is None:
        return None, "citation_source_missing"
    if not _is_safe_source(source, settings):
        return None, "citation_source_rejected"
    if plan.scheme_slug and source.scheme_slugs and plan.scheme_slug not in source.scheme_slugs:
        return None, "citation_source_scheme_mismatch"

    return (
        Citation(
            source_id=source.source_id,
            chunk_id=chunk_id,
            title=source.title or str(metadata.get("scheme", "HDFC Mutual Fund")),
            url=source.url,
            source_type=source.source_type,
            publisher=source.publisher,
            last_updated=_date_for(source, metadata),
        ),
        None,
    )


def _support_match(intent: str, source: SourceRecord) -> bool:
    haystack = " ".join(
        [source.source_id, source.title, source.purpose, source.notes]
    ).lower()
    if intent in {"advice", "pii"}:
        return source.source_type in {"sebi", "amfi"} and (
            "educat" in haystack or "investor" in haystack
        )
    if intent == "performance":
        return "factsheet" in haystack or "fact sheet" in haystack
    if intent == "out_of_scope":
        return "overview" in haystack or "hdfc-mutual-fund" in source.scheme_slugs
    return False


def resolve_support_citation(intent: str, settings: Settings) -> Citation | None:
    """Pick one approved official source for a refusal or no-answer card."""
    sources, error = _load_sources(settings)
    if error:
        return None
    for source in sources:
        if _is_safe_source(source, settings) and _support_match(intent, source):
            return Citation(
                source_id=source.source_id,
                chunk_id=None,
                title=source.title,
                url=source.url,
                source_type=source.source_type,
                publisher=source.publisher,
                last_updated=_date_for(source),
            )
    return None


def resolve_overview_citation(settings: Settings) -> Citation | None:
    """AMC overview fallback used when no answer can be rendered."""
    return resolve_support_citation("out_of_scope", settings)
