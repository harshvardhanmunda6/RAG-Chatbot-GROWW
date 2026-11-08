"""Extract readable sections from the stored source pages.

This module never fetches a URL. It only reads the raw files produced by the
loader, so no internal or outbound links can be followed. Groww seed pages use
the existing section selectors; approved HDFC AMC pages use the dedicated
official-page parser.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from bs4 import BeautifulSoup, Tag

from src.config import Settings
from src.hdfc_parser import amc_sections as hdfc_amc_sections
from src.hdfc_parser import scheme_sections as hdfc_scheme_sections
from src.hdfc_parser import statement_sections as hdfc_statement_sections
from src.loader import raw_path_for, read_snapshot_meta
from src.models import CleanDocument, CleanSection, SourceRecord


def _pdf_sections(raw_path: Path) -> tuple[list[CleanSection], list[str]]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError("pypdf is required to extract text from PDFs") from exc

    reader = PdfReader(str(raw_path))
    sections: list[CleanSection] = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = _clean_text(page.extract_text() or "")
        if text:
            sections.append(
                CleanSection(
                    section_id=f"page_{page_number}",
                    heading=f"Page {page_number}",
                    text=text,
                    order=page_number,
                )
            )
    if not sections:
        raise ValueError("PDF has no extractable text (image-only PDF)")
    return sections, []


GENERIC_DENYLIST_RE = re.compile(
    r"\b(?:returns?|cagr|annuali[sz]ed|absolute\s+return|ranking|ranked|rank|"
    r"navs?|nav\s+history|portfolio\s+holding|portfolio|fund\s+manager|"
    r"performance|past\s+performance)\b",
    re.IGNORECASE,
)


_GENERIC_SANITIZE = (
    (re.compile(r"\bHistorical\s+performance\b", re.IGNORECASE), "historical scheme information"),
    (re.compile(r"\bPortfolio\s+Holdings?\b", re.IGNORECASE), "scheme details"),
    (re.compile(r"\bFund\s+Manager\b", re.IGNORECASE), "scheme details"),
    # The official statement page includes an illustrative SMS command with a
    # sample folio number and password. It is not needed for the FAQ process.
    (re.compile(r"\bSMS to be sent as\s*:.*$", re.IGNORECASE | re.DOTALL), ""),
)


def _sanitize_generic_text(value: str) -> str:
    text = value
    for pattern, replacement in _GENERIC_SANITIZE:
        text = pattern.sub(replacement, text)
    return text

SCHEME_DENYLIST = (
    "returnStats",
    "mfGraph",
    "returnCalculator",
    "holdingsContainer",
    "returnsAndRankings",
    "compareSimilarFunds",
    "fundManagement",
)

SCHEME_SECTIONS = (
    ("fundDetails_fundDetailsContainer", "key_facts", "Key facts"),
    ("exitLoadStampDutyTax_container", "exit_load", "Exit load and stamp duty"),
    ("investmentObjective_container", "about_benchmark", "About the scheme and benchmark"),
    ("fundHouse_container", "fund_house", "Fund house and registrar"),
)


def _now() -> datetime:
    return datetime.now().astimezone()


def _sha256(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _strip_out_of_scope(value: str) -> str:
    """Remove ratings, ranking claims, tax details, and link labels."""
    value = re.sub(r"\bRating\s+\d+(?:\.\d+)?\b", "", value, flags=re.IGNORECASE)
    value = re.sub(
        r"\bRank\s*\(total assets\)\s*#\d+\s*in India\b",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\bCheck past data\b", "", value, flags=re.IGNORECASE)
    tax_index = value.lower().find("tax implication")
    if tax_index != -1:
        value = value[:tax_index]
    return _clean_text(value)


def _class_text(element: Tag) -> str:
    classes = element.get("class") or []
    return " ".join(classes) if isinstance(classes, list) else str(classes)


def _make_section(section_id: str, heading: str, text: str, order: int) -> CleanSection | None:
    text = _strip_out_of_scope(text)
    if not text:
        return None
    return CleanSection(section_id=section_id, heading=heading, text=text, order=order)


def _scheme_sections(soup: BeautifulSoup) -> tuple[list[CleanSection], list[str], str]:
    h1 = soup.find("h1")
    if not h1:
        raise ValueError("No h1 found")
    title = _clean_text(h1.get_text(" ", strip=True))

    main = h1.find_parent("div", class_=lambda c: c and "layout-main" in c)
    if main is None:
        raise ValueError("Scheme layout-main container not found")

    sections: list[CleanSection] = []
    removed: list[str] = []

    header = main.find("header")
    if header:
        section = _make_section("overview", "Scheme overview", header.get_text(" ", strip=True), 1)
        if section:
            sections.append(section)

    order = len(sections) + 1
    for child in main.find_all(recursive=False):
        if not isinstance(child, Tag) or child.name == "header":
            continue

        classes = _class_text(child)
        child_id = child.get("id") or ""
        text = _clean_text(child.get_text(" ", strip=True))
        if not text:
            continue

        if any(token in classes or token == child_id for token in SCHEME_DENYLIST):
            removed.append(classes or child_id or child.name)
            continue

        matched = False
        for marker, section_id, heading in SCHEME_SECTIONS:
            if marker in classes:
                section_text = text
                if section_id == "exit_load":
                    content = child.select_one(
                        'div[class*="exitLoadStampDutyTax_contentContainer"]'
                    )
                    if content:
                        section_text = content.get_text(" ", strip=True)
                section = _make_section(section_id, heading, section_text, order)
                if section:
                    sections.append(section)
                    order += 1
                matched = True
                break

        if matched:
            continue

        if text.lower().startswith("minimum investments"):
            section = _make_section("minimum_investments", "Minimum investments", text, order)
            if section:
                sections.append(section)
                order += 1
            continue

        removed.append(classes or child_id or child.name)

    return sections, removed, title


def _generic_sections(soup: BeautifulSoup) -> tuple[list[CleanSection], list[str], str]:
    """Conservative extractor for approved SEBI/AMFI/registrar pages.

    These pages are used for official investor-education and statement support.
    Only visible main-content text is retained, and lines carrying returns,
    rankings, NAV history, or portfolio holdings are removed.
    """
    h1 = soup.find("h1")
    title = _clean_text(h1.get_text(" ", strip=True)) if h1 else ""
    if not title and soup.title:
        title = _clean_text(soup.title.get_text(" ", strip=True))

    root = soup.find("main") or soup.find("article") or soup.body or soup
    for tag in root.find_all(["script", "style", "noscript", "nav", "footer", "header", "aside", "form"]):
        tag.decompose()

    sections: list[CleanSection] = []
    removed: list[str] = []
    heading = "Overview"
    parts: list[str] = []
    order = 1

    def flush() -> None:
        nonlocal parts, order
        text = _clean_text(" ".join(parts))
        if text:
            section = CleanSection(
                section_id=f"section_{order}",
                heading=heading,
                text=text,
                order=order,
            )
            sections.append(section)
            order += 1
        parts = []

    for node in root.find_all(["h1", "h2", "h3", "p", "li", "td"]):
        text = _clean_text(_sanitize_generic_text(node.get_text(" ", strip=True)))
        if not text or len(text) < 3:
            continue
        if GENERIC_DENYLIST_RE.search(text):
            removed.append(text[:160])
            continue
        if node.name in {"h1", "h2", "h3"}:
            flush()
            heading = text
            continue
        parts.append(text)
    flush()

    if not sections:
        fallback = _clean_text(root.get_text(" ", strip=True))
        if fallback and not GENERIC_DENYLIST_RE.search(fallback):
            sections.append(
                CleanSection(
                    section_id="section_1",
                    heading="Overview",
                    text=fallback,
                    order=1,
                )
            )
    if not sections:
        raise ValueError("Generic cleaner produced no safe sections")

    return sections, removed, title or "Official investor resource"


def _amc_sections(soup: BeautifulSoup) -> tuple[list[CleanSection], list[str], str]:
    h1 = soup.find("h1")
    if not h1:
        raise ValueError("No h1 found")
    title = _clean_text(h1.get_text(" ", strip=True))

    sections: list[CleanSection] = []
    removed: list[str] = []

    overview_parts: list[str] = [title]
    info = soup.select_one('div[class*="amcMainPage_infoContainer"]')
    if info:
        overview_parts.append(_clean_text(info.get_text(" ", strip=True)))

    overview = _make_section("overview", "AMC overview", " ".join(overview_parts), 1)
    if overview:
        sections.append(overview)

    key_heading = None
    for h2 in soup.find_all("h2"):
        if "key information" in _clean_text(h2.get_text(" ", strip=True)).lower():
            key_heading = h2
            break

    if key_heading:
        table = key_heading.find_next("table")
        if table:
            rows: list[str] = []
            for tr in table.find_all("tr"):
                cells = [_clean_text(td.get_text(" ", strip=True)) for td in tr.find_all(["th", "td"])]
                cells = [cell for cell in cells if cell]
                if len(cells) >= 2:
                    rows.append(f"{cells[0]}: {cells[1]}")
                elif cells:
                    rows.append(cells[0])
            section = _make_section("key_information", "Key information", "; ".join(rows), 2)
            if section:
                sections.append(section)

    removed.extend(
        [
            "fund list",
            "returns calculator",
            "performance writeups",
            "other AMCs",
            "footer",
        ]
    )

    return sections, removed, title


def clean_document(source: SourceRecord, settings: Settings) -> CleanDocument:
    meta = read_snapshot_meta(settings, source)
    if meta.get("status", "ok") != "ok":
        raise ValueError(f"Source not loaded successfully: {meta.get('status')}")
    raw_path = raw_path_for(settings, source)
    if not raw_path.exists():
        raise FileNotFoundError(f"Raw file missing: {raw_path}")

    parser = (source.parser or "").strip().lower()
    page_type = "amc_overview" if "/amc/" in source.url else "scheme_page"
    if raw_path.suffix.lower() == ".pdf":
        sections, removed = _pdf_sections(raw_path)
        title = source.title or "PDF document"
        parser = "pdf_text"
    else:
        html = raw_path.read_text(encoding="utf-8", errors="replace")
        soup = BeautifulSoup(html, "lxml")
        if parser == "hdfc_scheme":
            sections, removed, title = hdfc_scheme_sections(soup)
            page_type = "scheme_page"
        elif parser == "hdfc_amc":
            sections, removed, title = hdfc_amc_sections(soup)
            page_type = "amc_overview"
        elif parser == "hdfc_statement":
            sections, removed, title = hdfc_statement_sections(soup)
            page_type = "statement"
        elif parser == "generic_official":
            sections, removed, title = _generic_sections(soup)
            page_type = "official_education"
        elif "/amc/" in source.url:
            sections, removed, title = _amc_sections(soup)
        else:
            sections, removed, title = _scheme_sections(soup)
        if parser not in {"hdfc_scheme", "hdfc_amc", "hdfc_statement", "generic_official"}:
            parser = "groww_html_sections"
    title = source.title or title

    clean_text = "\n\n".join(f"{section.heading}: {section.text}" for section in sections)
    if not clean_text:
        raise ValueError("Cleaner produced no sections")

    doc = CleanDocument(
        source_id=source.source_id,
        url=source.url,
        final_url=meta["final_url"],
        title=title,
        publisher=source.publisher,
        source_type=source.source_type,
        citable=source.citable,
        scheme_slugs=source.scheme_slugs,
        status="cleaned",
        http_status=meta.get("http_status"),
        fetched_at=meta.get("fetched_at"),
        content_hash=meta.get("content_hash"),
        clean_text_hash=_sha256(clean_text),
        sections=sections,
        removed_sections=removed,
        clean_text=clean_text,
        metadata={
            "requested_url": source.url,
            "final_url": meta["final_url"],
            "parser": parser,
            "page_type": page_type,
            "fetch_via": meta.get("fetch_via", "direct"),
            "links_followed": 0,
            "internal_links_discovered": 0,
            "retrieved_at": meta.get("fetched_at"),
        },
    )

    clean_dir = settings.path("clean")
    clean_dir.mkdir(parents=True, exist_ok=True)
    safe_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in source.source_id)
    clean_path = clean_dir / f"{safe_id}.json"
    clean_path.write_text(doc.model_dump_json(indent=2), encoding="utf-8")
    (clean_dir / f"{safe_id}.txt").write_text(clean_text, encoding="utf-8")
    return doc


def clean_all(
    sources: list[SourceRecord],
    settings: Settings,
) -> tuple[list[CleanDocument], list[str]]:
    docs: list[CleanDocument] = []
    errors: list[str] = []
    for source in sources:
        try:
            doc = clean_document(source, settings)
            docs.append(doc)
            print(
                f"[ok]   {source.source_id}  sections={len(doc.sections)}  "
                f"chars={len(doc.clean_text)}"
            )
        except Exception as exc:  # noqa: BLE001 - report and continue batch
            errors.append(f"{source.source_id}: {exc}")
            print(f"[fail] {source.source_id}: {exc}")
    return docs, errors
