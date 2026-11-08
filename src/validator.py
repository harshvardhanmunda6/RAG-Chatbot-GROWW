"""Grounding, citation, sentence, and banned-term validation."""

from __future__ import annotations

import re

from src.config import Settings
from src.guardrails import ADVICE_PATTERNS, PERFORMANCE_PATTERNS
from src.models import Citation, QueryPlan, SearchHit, ValidationResult
from src.source_registry import is_url_allowed

OFFICIAL_SOURCE_TYPES = {"amc", "sebi", "amfi", "registrar"}
URL_RE = re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE)
NUMBER_RE = re.compile(r"\d+(?:,\d+)*(?:\.\d+)?%?")
FACT_HINTS: dict[str, tuple[str, ...]] = {
    "expense_ratio": ("expense ratio", "total expense", "ter"),
    "exit_load": ("exit load", "redemption fee"),
    "min_sip": ("minimum sip", "min sip", "sip amount", "sip"),
    "lock_in": ("lock-in", "lock in", "lockin"),
    "riskometer": ("riskometer", "risk level"),
    "benchmark": ("benchmark", "index"),
    "statement_process": ("statement", "capital gains", "cas"),
    "amc_overview": ("registrar", "fund house", "amc", "aum", "scheme count"),
}

_ABBREVIATIONS = (
    "rs",
    "mr",
    "mrs",
    "ms",
    "dr",
    "vs",
    "no",
    "fig",
    "e.g",
    "i.e",
)


def split_sentences(text: str) -> list[str]:
    """Split prose without treating decimals or common abbreviations as ends."""
    value = (text or "").strip()
    if not value:
        return []
    masked = re.sub(r"(?<=\d)\.(?=\d)", "§", value)
    for abbreviation in _ABBREVIATIONS:
        masked = re.sub(
            rf"\b{re.escape(abbreviation)}\.",
            abbreviation + "§",
            masked,
            flags=re.IGNORECASE,
        )
    boundaries = [match.end() for match in re.finditer(r"[.!?](?:\s+|$)", masked)]
    sentences: list[str] = []
    start = 0
    for boundary in boundaries:
        sentence = value[start:boundary].strip()
        if sentence:
            sentences.append(sentence)
        start = boundary
    remainder = value[start:].strip()
    if remainder:
        sentences.append(remainder)
    return sentences


def count_sentences(text: str) -> int:
    return len(split_sentences(text))


def _normalized_numbers(text: str) -> set[str]:
    numbers: set[str] = set()
    for match in NUMBER_RE.finditer(text or ""):
        token = match.group(0).replace(",", "").rstrip("%")
        if token:
            numbers.add(token)
    return numbers


def _check(name: str, passed: bool, detail: str = "") -> dict[str, object]:
    return {"check": name, "passed": bool(passed), "detail": detail}


def validate_answer(
    answer_text: str,
    citation: Citation | None,
    evidence: list[SearchHit],
    plan: QueryPlan,
    settings: Settings,
) -> ValidationResult:
    """Apply every pre-render check. The first failure is the reason."""
    checks: list[dict[str, object]] = []
    text = (answer_text or "").strip()

    checks.append(_check("non_empty", bool(text), "" if text else "answer is empty"))
    if not text:
        return ValidationResult(passed=False, reason="empty_answer", checks=checks)

    max_sentences = int(settings.data.get("app", {}).get("max_answer_sentences", 3))
    sentence_count = count_sentences(text)
    checks.append(
        _check(
            "sentence_limit",
            sentence_count <= max_sentences,
            f"{sentence_count} sentences; max {max_sentences}",
        )
    )

    has_url = bool(URL_RE.search(text))
    checks.append(_check("no_url_in_answer", not has_url, "URL found" if has_url else ""))

    advice = [pattern.pattern for pattern in ADVICE_PATTERNS if pattern.search(text)]
    checks.append(_check("no_advice", not advice, ", ".join(advice)))
    # "Total Return(s) Index" is part of an official benchmark name, not a
    # performance claim. Remove that phrase before applying the ban list.
    performance_text = re.sub(
        r"\bTotal\s+Returns?\s+Index\b", "Index", text, flags=re.IGNORECASE
    )
    performance = [
        pattern.pattern
        for pattern in PERFORMANCE_PATTERNS
        if pattern.search(performance_text)
    ]
    checks.append(_check("no_performance", not performance, ", ".join(performance)))

    evidence_by_id = {hit.chunk_id: hit for hit in evidence}
    citation_ok = citation is not None
    citation_detail = "" if citation_ok else "citation could not be resolved"
    checks.append(_check("citation_resolved", citation_ok, citation_detail))

    if citation is not None:
        in_evidence = citation.chunk_id in evidence_by_id
        checks.append(
            _check(
                "citation_in_evidence",
                in_evidence,
                "" if in_evidence else f"{citation.chunk_id!r} is not selected evidence",
            )
        )
        metadata = evidence_by_id[citation.chunk_id].metadata if in_evidence else {}
        citable = metadata.get("citable") is True
        official = str(metadata.get("source_type", "")) in OFFICIAL_SOURCE_TYPES
        checks.append(_check("citation_citable", citable, "citation is not citable"))
        checks.append(
            _check("citation_official", official, "citation source is not official")
        )
        scheme_ok = not plan.scheme_slug or metadata.get("scheme_slug") == plan.scheme_slug
        checks.append(
            _check(
                "scheme_match",
                scheme_ok,
                f"expected {plan.scheme_slug}, got {metadata.get('scheme_slug')}",
            )
        )

        actual_fact = str(metadata.get("fact_type", ""))
        if plan.fact_type:
            haystack = f"{metadata.get('section', '')} {text} {evidence_by_id[citation.chunk_id].text if in_evidence else ''}".lower()
            fact_ok = actual_fact == plan.fact_type or any(
                hint in haystack for hint in FACT_HINTS.get(plan.fact_type, ())
            )
        else:
            fact_ok = True
        checks.append(_check("fact_match", fact_ok, f"expected {plan.fact_type}"))

        url_ok = citation.url.startswith("https://") and is_url_allowed(
            citation.url, settings.sources.get("allowed_domains", [])
        )
        checks.append(
            _check("citation_url_safe", url_ok, "citation URL is not allowlisted https")
        )
        checks.append(
            _check(
                "citation_date",
                bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", citation.last_updated)),
                citation.last_updated,
            )
        )

        if in_evidence:
            chunk_text = evidence_by_id[citation.chunk_id].text
            missing = sorted(_normalized_numbers(text) - _normalized_numbers(chunk_text))
            checks.append(
                _check(
                    "numbers_grounded",
                    not missing,
                    "not in cited chunk: " + ", ".join(missing) if missing else "",
                )
            )
    else:
        checks.append(_check("numbers_grounded", False, "no citation"))

    failed = next((check for check in checks if not check["passed"]), None)
    return ValidationResult(
        passed=failed is None,
        reason=None if failed is None else str(failed["check"]),
        sentence_count=sentence_count,
        checks=checks,
    )
