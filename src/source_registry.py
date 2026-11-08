"""Read and validate the shared seed URL registry.

This project is in seed-only loading mode. The loader must only fetch the
exact five URLs in sources.csv. It never discovers or follows links inside a
page.
"""

from __future__ import annotations

import csv
from datetime import datetime
from urllib.parse import urlparse

from src.config import Settings
from src.models import SourceRecord


def _as_bool(value: str | None) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _as_int(value: str | None) -> int | None:
    value = (value or "").strip()
    return int(value) if value else None


def _as_datetime(value: str | None) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_url_allowed(url: str, allowed_domains: list[str]) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    return any(host == domain or host.endswith("." + domain) for domain in allowed_domains)


def load_registry(settings: Settings) -> tuple[list[SourceRecord], list[str]]:
    path = settings.root / settings.sources["registry"]
    if not path.exists():
        return [], [f"Source registry not found: {path}"]

    allowed_domains = settings.sources.get("allowed_domains", [])
    strict_seed_only = bool(settings.sources.get("strict_seed_only", False))
    errors: list[str] = []
    rows: list[SourceRecord] = []

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for line_no, row in enumerate(reader, start=2):
            source_id = (row.get("source_id") or "").strip()
            url = (row.get("url") or "").strip()
            source_type = (row.get("source_type") or "").strip()
            citable = _as_bool(row.get("citable"))
            status = (row.get("status") or "").strip()

            if not source_id or not url or not source_type:
                errors.append(f"line {line_no}: source_id, url, and source_type are required")
                continue

            if not is_url_allowed(url, allowed_domains):
                errors.append(f"line {line_no}: URL is not https or not allowlisted: {url}")

            if strict_seed_only and source_type != "seed":
                errors.append(f"line {line_no}: strict_seed_only is active; only seed rows are allowed")
            if source_type == "seed" and citable:
                errors.append(f"line {line_no}: seed rows must not be citable")

            slugs = [s.strip() for s in (row.get("scheme_slugs") or "").split("|") if s.strip()]
            rows.append(
                SourceRecord(
                    source_id=source_id,
                    url=url,
                    title=(row.get("title") or "").strip(),
                    publisher=(row.get("publisher") or "").strip(),
                    source_type=source_type,
                    purpose=(row.get("purpose") or "").strip(),
                    scheme_slugs=slugs,
                    status=status or ("seed_only" if source_type == "seed" else "approved"),
                    citable=citable,
                    http_status=_as_int(row.get("http_status")),
                    fetched_at=_as_datetime(row.get("fetched_at")),
                    last_modified=_as_datetime(row.get("last_modified")),
                    content_hash=(row.get("content_hash") or "").strip() or None,
                    parser=(row.get("parser") or "").strip() or None,
                    notes=(row.get("notes") or "").strip(),
                )
            )

    ids = [row.source_id for row in rows]
    urls = [row.url for row in rows]
    if len(ids) != len(set(ids)):
        errors.append("duplicate source_id in sources.csv")
    if len(urls) != len(set(urls)):
        errors.append("duplicate URL in sources.csv")

    min_total = int(settings.sources.get("min_total_sources", 0))
    max_total = int(settings.sources.get("max_total_sources", 10**6))
    if not (min_total <= len(rows) <= max_total):
        errors.append(f"source count {len(rows)} is outside {min_total}..{max_total}")

    official_count = sum(1 for row in rows if row.source_type in {"amc", "sebi", "amfi", "registrar"})
    if official_count < int(settings.sources.get("min_official_sources", 0)):
        errors.append(
            f"official source count {official_count} is below "
            f"{settings.sources.get('min_official_sources')}"
        )

    configured_seeds = set(settings.sources.get("seed_urls", []))
    actual_seeds = {row.url for row in rows if row.source_type == "seed"}
    missing = configured_seeds - actual_seeds
    if missing:
        errors.append("configured seed URLs missing from registry: " + ", ".join(sorted(missing)))

    return rows, errors
