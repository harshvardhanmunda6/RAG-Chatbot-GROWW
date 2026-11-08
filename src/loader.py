"""Fetch the approved source URLs and store raw snapshots.

No internal links are discovered or followed. The only URLs requested are the
rows in data/00_sources/sources.csv. HDFC AMC HTML pages are retrieved through a
read-only public reader because the origin blocks direct requests from this
environment; the official URL remains the canonical citation target.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from src.config import Settings
from src.models import SourceRecord, SourceSnapshot
from src.source_registry import is_url_allowed

_ROBOTS_CACHE: dict[str, RobotFileParser | None] = {}


class SourceLoadError(RuntimeError):
    """Base class for a source that could not be loaded."""


class SourceBlockedError(SourceLoadError):
    """The source returned 403/429 or is disallowed by robots.txt."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value)


def _source_meta_base(source: SourceRecord, settings: Settings) -> dict:
    return {
        "source_id": source.source_id,
        "requested_url": source.url,
        "source_type": source.source_type,
        "publisher": source.publisher,
        "title": source.title,
        "scheme_slugs": source.scheme_slugs,
        "notes": source.notes,
        "links_followed": 0,
        "internal_links_discovered": 0,
    }


def _write_failure_meta(
    source: SourceRecord,
    settings: Settings,
    status: str,
    reason: str,
    response: httpx.Response | None = None,
    fetched_at: datetime | None = None,
) -> None:
    raw_dir = settings.path("raw")
    raw_dir.mkdir(parents=True, exist_ok=True)
    meta = _source_meta_base(source, settings)
    meta.update(
        {
            "status": status,
            "reason": reason,
            "final_url": str(response.url) if response is not None else None,
            "http_status": response.status_code if response is not None else None,
            "content_type": response.headers.get("content-type", "") if response is not None else "",
            "bytes_written": 0,
            "content_hash": None,
            "fetched_at": (fetched_at or _utc_now()).isoformat(),
            "parser": None,
            "redirect_history": [str(r.url) for r in response.history] if response is not None else [],
            "raw_path": None,
        }
    )
    meta_path = raw_dir / f"{_safe_name(source.source_id)}.meta.json"
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


def _proxy_for(url: str, settings: Settings) -> tuple[str, dict[str, str]] | None:
    cfg = settings.sources.get("fetch_proxy", {})
    if not isinstance(cfg, dict) or not cfg.get("enabled"):
        return None
    host = (urlparse(url).hostname or "").lower()
    hosts = [str(item).lower() for item in cfg.get("hosts", [])]
    if not any(host == item or host.endswith("." + item) for item in hosts):
        return None
    prefix = str(cfg.get("prefix", ""))
    if not prefix:
        return None
    headers = {"X-Return-Format": str(cfg.get("return_format", "html"))}
    return prefix + url, headers


def _proxy_error(response: httpx.Response) -> str | None:
    head = response.text[:8000].lower()
    if "target url returned error" in head or "warning: target url returned error" in head:
        return "proxy target returned an error"
    return None


def _robots_allowed(client: httpx.Client, url: str, user_agent: str) -> bool:
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    if base not in _ROBOTS_CACHE:
        robots_url = base + "/robots.txt"
        try:
            response = client.get(robots_url)
            if response.status_code >= 400:
                _ROBOTS_CACHE[base] = None
            else:
                parser = RobotFileParser()
                parser.set_url(robots_url)
                parser.parse(response.text.splitlines())
                _ROBOTS_CACHE[base] = parser
        except httpx.HTTPError:
            _ROBOTS_CACHE[base] = None

    parser = _ROBOTS_CACHE[base]
    if parser is None:
        return True
    return parser.can_fetch(user_agent, url)


def _request_with_retries(
    client: httpx.Client,
    url: str,
    retries: int,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = client.get(url, headers=headers)
            if response.status_code in {429, 500, 502, 503, 504} and attempt < retries:
                time.sleep(2**attempt)
                continue
            return response
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(2**attempt)
                continue
            raise

    assert last_error is not None
    raise last_error


def load_source(
    source: SourceRecord,
    settings: Settings,
    client: httpx.Client | None = None,
) -> SourceSnapshot:
    allowed_domains = settings.sources.get("allowed_domains", [])
    if not is_url_allowed(source.url, allowed_domains):
        raise ValueError(f"Blocked non-allowlisted URL: {source.url}")

    own_client = client is None
    if client is None:
        client = httpx.Client(
            follow_redirects=True,
            timeout=float(settings.sources.get("request_timeout_s", 30)),
            headers={"User-Agent": settings.sources.get("user_agent", "MF-facts-demo/1.0")},
        )

    proxy = _proxy_for(source.url, settings)
    request_url = proxy[0] if proxy else source.url
    request_headers = proxy[1] if proxy else None

    try:
        if not proxy and bool(settings.sources.get("respect_robots", True)):
            user_agent = settings.sources.get("user_agent", "MF-facts-demo/1.0")
            if not _robots_allowed(client, source.url, user_agent):
                _write_failure_meta(
                    source, settings, "blocked", "robots.txt disallows this URL"
                )
                raise SourceBlockedError(f"Blocked by robots.txt: {source.url}")

        response = _request_with_retries(
            client,
            request_url,
            int(settings.sources.get("retries", 1)),
            headers=request_headers,
        )
    finally:
        if own_client:
            client.close()

    if response.status_code in {403, 429}:
        _write_failure_meta(
            source,
            settings,
            "blocked",
            f"HTTP {response.status_code} blocked the request",
            response=response,
        )
        raise SourceBlockedError(f"HTTP {response.status_code} blocked {source.url}")
    if response.status_code >= 400:
        _write_failure_meta(
            source,
            settings,
            "error",
            f"HTTP {response.status_code}",
            response=response,
        )
        raise SourceLoadError(f"HTTP {response.status_code} for {source.url}")

    if proxy:
        proxy_error = _proxy_error(response)
        if proxy_error:
            _write_failure_meta(
                source,
                settings,
                "error",
                proxy_error,
                response=response,
            )
            raise SourceLoadError(f"{proxy_error}: {source.url}")
        final_url = source.url
        redirect_history: list[str] = []
    else:
        if not is_url_allowed(str(response.url), allowed_domains):
            _write_failure_meta(
                source,
                settings,
                "blocked",
                f"Redirected outside allowlist: {response.url}",
                response=response,
            )
            raise SourceBlockedError(f"Redirected outside allowlist: {response.url}")
        final_url = str(response.url)
        redirect_history = [str(r.url) for r in response.history]

    content = response.content
    if not content:
        raise ValueError(f"Empty response body for {source.url}")

    raw_dir = settings.path("raw")
    raw_dir.mkdir(parents=True, exist_ok=True)
    source_id = _safe_name(source.source_id)
    suffix = ".pdf" if "pdf" in response.headers.get("content-type", "").lower() else ".html"
    raw_path = raw_dir / f"{source_id}{suffix}"

    content_hash = _sha256(content)
    fetched_at = _utc_now()
    raw_path.write_bytes(content)

    meta = _source_meta_base(source, settings)
    meta.update(
        {
            "status": "ok",
            "final_url": final_url,
            "http_status": response.status_code,
            "content_type": response.headers.get("content-type", ""),
            "content_length_header": response.headers.get("content-length"),
            "bytes_written": len(content),
            "content_hash": content_hash,
            "fetched_at": fetched_at.isoformat(),
            "parser": source.parser or ("pdf" if suffix == ".pdf" else "html"),
            "fetch_via": "jina_proxy" if proxy else "direct",
            "redirect_history": redirect_history,
            "raw_path": str(raw_path.relative_to(settings.root)),
        }
    )
    meta_path = raw_dir / f"{source_id}.meta.json"
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    return SourceSnapshot(
        source_id=source.source_id,
        requested_url=source.url,
        final_url=final_url,
        http_status=response.status_code,
        content_type=response.headers.get("content-type", ""),
        fetched_at=fetched_at,
        content_hash=content_hash,
        raw_path=str(raw_path.relative_to(settings.root)),
        meta_path=str(meta_path.relative_to(settings.root)),
        bytes_written=len(content),
        redirect_history=redirect_history,
    )


def load_all(
    sources: list[SourceRecord],
    settings: Settings,
) -> tuple[list[SourceSnapshot], list[str]]:
    snapshots: list[SourceSnapshot] = []
    errors: list[str] = []
    delay = float(settings.sources.get("min_delay_s", 1.0))

    with httpx.Client(
        follow_redirects=True,
        timeout=float(settings.sources.get("request_timeout_s", 30)),
        headers={"User-Agent": settings.sources.get("user_agent", "MF-facts-demo/1.0")},
    ) as client:
        for index, source in enumerate(sources):
            try:
                snapshot = load_source(source, settings, client=client)
                snapshots.append(snapshot)
                print(f"[ok]   {source.source_id}  {snapshot.http_status}  {snapshot.bytes_written} bytes")
            except Exception as exc:  # noqa: BLE001 - report and continue batch
                meta_path = settings.path("raw") / f"{_safe_name(source.source_id)}.meta.json"
                if not meta_path.exists():
                    _write_failure_meta(source, settings, "error", str(exc))
                errors.append(f"{source.source_id}: {exc}")
                print(f"[fail] {source.source_id}: {exc}")
            if index < len(sources) - 1:
                time.sleep(delay)

    return snapshots, errors


def read_snapshot_meta(settings: Settings, source: SourceRecord) -> dict:
    meta_path = settings.path("raw") / f"{_safe_name(source.source_id)}.meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"Missing raw metadata: {meta_path}")
    return json.loads(meta_path.read_text(encoding="utf-8"))


def raw_path_for(settings: Settings, source: SourceRecord) -> Path:
    meta = read_snapshot_meta(settings, source)
    return settings.root / meta["raw_path"]
