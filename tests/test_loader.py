"""Loader behavior tests for the seed-only scraping phase."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from src.config import Settings
from src.loader import SourceBlockedError, load_source
from src.models import SourceRecord
from src.source_registry import load_registry


def _settings(tmp_path: Path, *, allowed_domains: list[str] | None = None) -> Settings:
    return Settings(
        root=tmp_path,
        data={
            "paths": {
                "sources": "sources",
                "raw": "raw",
                "clean": "clean",
            },
            "sources": {
                "registry": "sources/sources.csv",
                "strict_seed_only": True,
                "min_total_sources": 1,
                "max_total_sources": 5,
                "min_official_sources": 0,
                "allowed_domains": allowed_domains or ["groww.in"],
                "request_timeout_s": 5,
                "min_delay_s": 0,
                "retries": 0,
                "respect_robots": False,
                "user_agent": "test-agent",
                "seed_urls": [],
            },
        },
    )


def _source(url: str, source_id: str = "test_source") -> SourceRecord:
    return SourceRecord(
        source_id=source_id,
        url=url,
        title="Test",
        publisher="Groww",
        source_type="seed",
        purpose="scheme_facts",
        scheme_slugs=[],
        status="seed_only",
        citable=False,
    )


def _client(handler) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
        timeout=5,
    )


def test_403_is_recorded_as_blocked(tmp_path: Path):
    settings = _settings(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="blocked")

    with _client(handler) as client:
        with pytest.raises(SourceBlockedError):
            load_source(_source("https://groww.in/blocked"), settings, client=client)

    meta = json.loads((tmp_path / "raw" / "test_source.meta.json").read_text())
    assert meta["status"] == "blocked"
    assert meta["http_status"] == 403
    assert meta["raw_path"] is None


def test_redirect_outside_allowlist_is_rejected(tmp_path: Path):
    settings = _settings(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "groww.in":
            return httpx.Response(302, headers={"location": "https://example.com/page"})
        return httpx.Response(200, text="outside")

    with _client(handler) as client:
        with pytest.raises(SourceBlockedError):
            load_source(_source("https://groww.in/redirect"), settings, client=client)

    meta = json.loads((tmp_path / "raw" / "test_source.meta.json").read_text())
    assert meta["status"] == "blocked"
    assert "outside allowlist" in meta["reason"]


def test_reload_overwrites_instead_of_duplicating(tmp_path: Path):
    settings = _settings(tmp_path)
    source = _source("https://groww.in/page", source_id="one")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<h1>Page</h1>", headers={"content-type": "text/html"})

    with _client(handler) as client:
        first = load_source(source, settings, client=client)
        second = load_source(source, settings, client=client)

    assert first.raw_path == second.raw_path
    assert len(list((tmp_path / "raw").glob("one.html"))) == 1
    assert len(list((tmp_path / "raw").glob("one.meta.json"))) == 1


def test_registry_rejects_duplicate_url(tmp_path: Path):
    settings = _settings(tmp_path)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    csv_text = (
        "source_id,url,title,publisher,source_type,purpose,scheme_slugs,status,citable,http_status,fetched_at,last_modified,content_hash,parser,notes\n"
        "a,https://groww.in/mutual-funds/a,A,Groww,seed,scheme_facts,,seed_only,false,,,,html,\n"
        "b,https://groww.in/mutual-funds/a,B,Groww,seed,scheme_facts,,seed_only,false,,,,html,\n"
    )
    (source_dir / "sources.csv").write_text(csv_text)

    _, errors = load_registry(settings)
    assert any("duplicate URL" in error for error in errors)
