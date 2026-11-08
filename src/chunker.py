"""Page-type-aware chunking and fact-card preparation.

The chunker reads only clean documents. It never fetches a URL, and seed
documents remain ``citable=false`` in every emitted chunk.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from src.config import Settings
from src.fact_extractor import FACT_LABELS, extract_facts
from src.models import Chunk, CleanDocument, FactCard

CHUNKER_VERSION = "1.0"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _chunk_id(source_id: str, section_id: str, index: int, text: str) -> str:
    payload = f"{source_id}|{section_id}|{index}|{text}"
    return "ch_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def estimate_tokens(text: str) -> int:
    """Conservative token estimate for a MiniLM-style tokenizer.

    The embedding model is not loaded during chunking. This estimate takes the
    larger of word/punctuation count and ``ceil(chars / 4)`` so the 240-token
    hard limit stays safe even when the real tokenizer adds subword pieces.
    """
    lexical = len(re.findall(r"\w+|[^\w\s]", text, flags=re.UNICODE))
    return max(lexical, math.ceil(len(text) / 4))


def _page_type(doc: CleanDocument) -> str:
    declared = str(doc.metadata.get("page_type", "")).strip()
    if declared in {"amc_overview", "scheme_page", "statement", "official_education"}:
        return declared
    if "/amc/" in doc.url or doc.title.lower() == "hdfc mutual fund":
        return "amc_overview"
    return "scheme_page"


def _scheme_slug(doc: CleanDocument) -> str:
    if doc.scheme_slugs:
        return doc.scheme_slugs[0]
    return re.sub(r"[^a-z0-9]+", "-", doc.title.lower()).strip("-")


def _prefix(doc: CleanDocument) -> str:
    if _page_type(doc) in {"amc_overview", "statement"}:
        return f"AMC: {doc.title} | "
    if _page_type(doc) == "official_education":
        return f"Source: {doc.title} | "
    return f"Scheme: {doc.title} | "


def _fact_text(doc: CleanDocument, fact: FactCard) -> str:
    label = FACT_LABELS.get(fact.fact_type, fact.fact_type.replace("_", " "))
    prefix = _prefix(doc) + f"Fact: {label} | "
    sentence_label = label[:1].upper() + label[1:]
    return prefix + f"{sentence_label}: {fact.value_text}"


def _make_chunk(
    doc: CleanDocument,
    section_id: str,
    section: str,
    fact_type: str,
    text: str,
    index: int,
) -> Chunk:
    return Chunk(
        chunk_id=_chunk_id(doc.source_id, section_id, index, text),
        source_id=doc.source_id,
        source_url=doc.url,
        source_type=doc.source_type,
        citable=doc.citable,
        page_type=_page_type(doc),
        scheme=doc.title,
        scheme_slug=_scheme_slug(doc),
        fact_type=fact_type,
        section=section,
        page=None,
        text=text,
        token_count=estimate_tokens(text),
        char_count=len(text),
        content_hash=_sha256(text),
        chunker_version=CHUNKER_VERSION,
        retrieved_at=doc.fetched_at,
    )


def _fact_chunks(doc: CleanDocument, facts: list[FactCard], hard_max: int) -> list[Chunk]:
    chunks: list[Chunk] = []
    for fact in facts:
        if fact.status == "missing" or fact.value_text is None:
            continue
        text = _fact_text(doc, fact)
        token_count = estimate_tokens(text)
        if token_count > hard_max:
            raise ValueError(
                f"Fact chunk exceeds hard max: {fact.fact_id} ({token_count} tokens)"
            )
        chunk = _make_chunk(
            doc,
            section_id=f"fact_{fact.fact_type}",
            section=fact.fact_type,
            fact_type=fact.fact_type,
            text=text,
            index=0,
        )
        chunks.append(chunk)
        fact.chunk_id = chunk.chunk_id
    return chunks


def _split_words_with_overlap(
    text: str,
    prefix: str,
    target_tokens: int,
    overlap_tokens: int,
    hard_max: int,
) -> list[str]:
    words = text.split()
    if not words:
        return []

    prefix_tokens = estimate_tokens(prefix)
    if prefix_tokens >= hard_max:
        raise ValueError("Chunk prefix alone exceeds hard maximum")

    limit = min(target_tokens, hard_max - prefix_tokens)
    chunks: list[str] = []
    start = 0

    while start < len(words):
        end = start
        while end < len(words):
            candidate = prefix + " ".join(words[start : end + 1])
            if estimate_tokens(candidate) > limit and end > start:
                break
            end += 1
        candidate = prefix + " ".join(words[start:end])
        chunks.append(candidate)

        if end >= len(words):
            break
        overlap = min(overlap_tokens, end - start - 1)
        next_start = end - overlap
        if next_start <= start:
            next_start = end
        start = next_start

    return chunks


def _narrative_chunks(doc: CleanDocument, cfg: dict[str, Any]) -> list[Chunk]:
    target_tokens = int(cfg.get("narrative_tokens", 200))
    overlap_tokens = int(cfg.get("overlap_tokens", 30))
    hard_max = int(cfg.get("hard_max_tokens", 240))
    chunks: list[Chunk] = []

    for section in doc.sections:
        text = section.text.strip()
        if not text:
            continue
        prefix = _prefix(doc) + f"Section: {section.heading} | "
        for index, chunk_text in enumerate(
            _split_words_with_overlap(
                text,
                prefix,
                target_tokens=target_tokens,
                overlap_tokens=overlap_tokens,
                hard_max=hard_max,
            )
        ):
            chunk = _make_chunk(
                doc,
                section_id=section.section_id,
                section=section.heading,
                fact_type="narrative",
                text=chunk_text,
                index=index,
            )
            if chunk.token_count > hard_max:
                raise ValueError(
                    f"Narrative chunk exceeds hard max: {chunk.chunk_id} "
                    f"({chunk.token_count} tokens)"
                )
            chunks.append(chunk)

    return chunks


def chunk_document(
    doc: CleanDocument,
    cfg: dict[str, Any],
) -> tuple[list[Chunk], list[FactCard]]:
    """Create deterministic fact and narrative chunks for one document."""
    hard_max = int(cfg.get("hard_max_tokens", 240))
    facts = extract_facts(doc)
    chunks = _fact_chunks(doc, facts, hard_max=hard_max)
    chunks.extend(_narrative_chunks(doc, cfg))

    ids = [chunk.chunk_id for chunk in chunks]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Duplicate chunk IDs generated for {doc.source_id}")

    return chunks, facts


def detect_conflicts(facts: list[FactCard]) -> set[str]:
    """Flag verified facts whose official values disagree.

    Returns the fact IDs that became conflicted. Seed/cross-check values do not
    participate because they are not official evidence.
    """
    groups: dict[tuple[str, str], list[FactCard]] = {}
    for fact in facts:
        if fact.status == "verified" and fact.value_text is not None:
            groups.setdefault((fact.scheme_slug, fact.fact_type), []).append(fact)

    conflicted: set[str] = set()
    for group in groups.values():
        if len({str(fact.value_normalized) for fact in group}) <= 1:
            continue
        for fact in group:
            fact.conflict = True
            fact.status = "conflict"
            fact.chunk_id = None
            conflicted.add(fact.fact_id)
    return conflicted


def chunk_all(
    docs: list[CleanDocument],
    settings: Settings,
) -> tuple[list[Chunk], list[FactCard]]:
    cfg = settings.data.get("chunking", {})
    chunks: list[Chunk] = []
    facts: list[FactCard] = []
    for doc in docs:
        doc_chunks, doc_facts = chunk_document(doc, cfg)
        chunks.extend(doc_chunks)
        facts.extend(doc_facts)
        print(
            f"[ok]   {doc.source_id}  chunks={len(doc_chunks)}  "
            f"facts={len(doc_facts)}  citable={doc.citable}"
        )

    detect_conflicts(facts)
    conflict_chunk_ids = {
        chunk.chunk_id
        for chunk in chunks
        if chunk.fact_type != "narrative"
        and any(
            fact.conflict
            and fact.fact_type == chunk.fact_type
            and fact.scheme_slug == chunk.scheme_slug
            for fact in facts
        )
    }
    chunks = [chunk for chunk in chunks if chunk.chunk_id not in conflict_chunk_ids]

    chunk_ids = [chunk.chunk_id for chunk in chunks]
    if len(chunk_ids) != len(set(chunk_ids)):
        raise ValueError("Duplicate chunk IDs across documents")
    return chunks, facts


def load_clean_documents(
    sources,
    settings: Settings,
) -> tuple[list[CleanDocument], list[str]]:
    docs: list[CleanDocument] = []
    errors: list[str] = []
    clean_dir = settings.path("clean")
    for source in sources:
        path = clean_dir / f"{source.source_id}.json"
        if not path.exists():
            errors.append(f"{source.source_id}: missing clean document {path}")
            continue
        docs.append(CleanDocument.model_validate_json(path.read_text(encoding="utf-8")))
    return docs, errors


def _percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def build_report(
    docs: list[CleanDocument],
    chunks: list[Chunk],
    facts: list[FactCard],
    settings: Settings,
) -> dict[str, Any]:
    token_values = [chunk.token_count for chunk in chunks]
    fact_status = Counter(fact.status for fact in facts)
    fact_types = Counter(fact.fact_type for fact in facts)
    chunk_fact_types = Counter(chunk.fact_type for chunk in chunks)
    page_types = Counter(chunk.page_type for chunk in chunks)

    coverage: dict[str, dict[str, str]] = {}
    for fact in facts:
        coverage.setdefault(fact.scheme_slug, {})[fact.fact_type] = fact.status

    blocked_count = 0
    checks_path = settings.path("sources") / "source_checks.json"
    if checks_path.exists():
        checks = json.loads(checks_path.read_text(encoding="utf-8"))
        blocked_count = sum(
            1 for item in checks.get("sources", []) if item.get("status") == "blocked"
        )

    return {
        "generated_at": _now().isoformat(),
        "chunker_version": CHUNKER_VERSION,
        "config_hash": settings.config_hash,
        "source_count": len(docs),
        "approved_count": sum(1 for doc in docs if doc.citable),
        "seed_count": sum(1 for doc in docs if doc.source_type == "seed"),
        "blocked_count": blocked_count,
        "chunk_count": len(chunks),
        "fact_count": len(facts),
        "chunk_count_by_page_type": dict(sorted(page_types.items())),
        "chunk_count_by_fact_type": dict(sorted(chunk_fact_types.items())),
        "fact_count_by_type": dict(sorted(fact_types.items())),
        "fact_status_counts": dict(sorted(fact_status.items())),
        "token_distribution": {
            "min": min(token_values) if token_values else 0,
            "median": statistics.median(token_values) if token_values else 0,
            "p95": _percentile(token_values, 0.95),
            "max": max(token_values) if token_values else 0,
        },
        "rejected_sections": sum(len(doc.removed_sections) for doc in docs),
        "coverage": dict(sorted(coverage.items())),
        "missing_facts": [
            {
                "fact_id": fact.fact_id,
                "scheme": fact.scheme,
                "fact_type": fact.fact_type,
                "reason": (fact.details or {}).get("reason"),
            }
            for fact in facts
            if fact.status == "missing"
        ],
    }


def write_artifacts(
    docs: list[CleanDocument],
    chunks: list[Chunk],
    facts: list[FactCard],
    settings: Settings,
) -> dict[str, Any]:
    out_dir = settings.path("chunks")
    out_dir.mkdir(parents=True, exist_ok=True)

    with (out_dir / "chunks.jsonl").open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(chunk.model_dump_json() + "\n")

    with (out_dir / "facts.jsonl").open("w", encoding="utf-8") as handle:
        for fact in facts:
            handle.write(fact.model_dump_json() + "\n")

    report = build_report(docs, chunks, facts, settings)
    (out_dir / "chunk_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return report
