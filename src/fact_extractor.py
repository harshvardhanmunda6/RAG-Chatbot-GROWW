"""Extract atomic fact cards from cleaned documents.

Seed/cross-check sources are stored with ``verified=false`` and a ``crosscheck``
payload. They are never treated as citable final evidence. Missing required
facts are emitted explicitly so coverage gaps are visible downstream.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from src.models import CleanDocument, FactCard

OFFICIAL_SOURCE_TYPES = {"amc", "sebi", "amfi", "registrar"}

FACT_LABELS = {
    "expense_ratio": "expense ratio",
    "exit_load": "exit load",
    "min_sip": "minimum SIP",
    "lock_in": "lock-in",
    "riskometer": "riskometer",
    "benchmark": "benchmark",
    "statement_process": "statement process",
    "amc_overview": "AMC overview",
}

EXPENSE_RE = re.compile(r"Expense ratio\s+([0-9]+(?:\.[0-9]+)?)\s*%", re.IGNORECASE)
MIN_SIP_RES = (
    re.compile(r"Min\.\s*for SIP\s*₹?\s*([0-9][0-9,]*)", re.IGNORECASE),
    re.compile(r"Min(?:imum)?\s*SIP\s*:?\s*₹?\s*([0-9][0-9,]*)", re.IGNORECASE),
    re.compile(
        r"Minimum SIP Investment is set to\s*₹?\s*([0-9][0-9,]*)",
        re.IGNORECASE,
    ),
)
EXIT_LOAD_RE = re.compile(r"Exit load\s*:?\s*(.*?)(?:\s+Stamp duty|$)", re.IGNORECASE | re.DOTALL)
LOCK_IN_RES = (
    re.compile(r"(\d+)\s*Y\s*Lock-in", re.IGNORECASE),
    re.compile(r"(\d+)\s*[- ]?year(?:s)?\s+lock-in", re.IGNORECASE),
    re.compile(r"lock-in[^.]{0,60}?(\d+)\s*(?:years|Y)", re.IGNORECASE),
)
RISK_RE = re.compile(
    r"(?:Riskometer\s*:?\s*)?\b(Very High|High|Moderate|Low|Very Low)\s+Risk\b",
    re.IGNORECASE,
)
BENCHMARK_RE = re.compile(
    r"Fund benchmark\s+(.+?)(?:\s+Scheme Information Document|\s*$)",
    re.IGNORECASE | re.DOTALL,
)
AUM_RE = re.compile(r"Total AUM[^₹]*₹\s*([0-9][0-9,.]*)\s*Cr", re.IGNORECASE)
SCHEMES_RE = re.compile(r"No of Schemes\s+([0-9]+)", re.IGNORECASE)
AGE_RE = re.compile(r"AMC Age\s+([0-9]+\s*years?)", re.IGNORECASE)
INCORPORATION_RE = re.compile(
    r"AMC Incorporation Date:\s*([^;]+)", re.IGNORECASE
)
INVESTORS_RE = re.compile(
    r"([0-9]+(?:\.[0-9]+)?\s*(?:Crore|Lakh)\+?)\s+investors", re.IGNORECASE
)
SEBI_RE = re.compile(r"\b(MF/[0-9/]+)\b", re.IGNORECASE)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _source_slug(doc: CleanDocument) -> str:
    if doc.scheme_slugs:
        return doc.scheme_slugs[0]
    return _slugify(doc.title)


def _page_type(doc: CleanDocument) -> str:
    declared = str(doc.metadata.get("page_type", "")).strip()
    if declared in {"amc_overview", "scheme_page", "statement", "official_education"}:
        return declared
    if "/amc/" in doc.url or doc.title.lower() == "hdfc mutual fund":
        return "amc_overview"
    return "scheme_page"


def _section_map(doc: CleanDocument) -> dict[str, str]:
    return {section.section_id: section.text for section in doc.sections}


def _first_match(text: str, patterns: tuple[re.Pattern[str], ...]) -> re.Match[str] | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match
    return None


def _as_of(doc: CleanDocument) -> str | None:
    if doc.fetched_at:
        return doc.fetched_at.date().isoformat()
    return None


def _base_fact(
    doc: CleanDocument,
    fact_type: str,
    value_text: str | None,
    *,
    value_normalized: float | int | str | None = None,
    unit: str | None = None,
    details: dict[str, Any] | None = None,
) -> FactCard:
    scheme = doc.title
    slug = _source_slug(doc)
    verified = bool(doc.citable and doc.source_type in OFFICIAL_SOURCE_TYPES)
    retrieved_at = doc.fetched_at or _now()

    crosscheck = None
    if not verified and value_text is not None:
        crosscheck = {
            "source_id": doc.source_id,
            "source_url": doc.url,
            "source_type": doc.source_type,
            "citable": doc.citable,
            "value_text": value_text,
        }

    return FactCard(
        fact_id=f"{slug}__{fact_type}",
        scheme=scheme,
        scheme_slug=slug,
        fact_type=fact_type,
        value_text=value_text,
        value_normalized=value_normalized,
        unit=unit,
        as_of=_as_of(doc),
        source_id=doc.source_id,
        source_url=doc.url,
        source_type=doc.source_type,
        verified=verified,
        conflict=False,
        status="verified" if verified else "seed_only",
        crosscheck=crosscheck,
        details=details,
        retrieved_at=retrieved_at,
    )


def _missing_fact(doc: CleanDocument, fact_type: str, reason: str) -> FactCard:
    return _base_fact(
        doc,
        fact_type,
        None,
        details={"reason": reason},
    ).model_copy(update={"status": "missing"})


def _extract_scheme_fact(
    doc: CleanDocument,
    fact_type: str,
    sections: dict[str, str],
    all_text: str,
) -> FactCard | None:
    if fact_type == "expense_ratio":
        match = EXPENSE_RE.search(sections.get("key_facts", all_text))
        if not match:
            return None
        value = f"{match.group(1)}%"
        return _base_fact(
            doc,
            fact_type,
            value,
            value_normalized=float(match.group(1)),
            unit="percent",
        )

    if fact_type == "exit_load":
        match = EXIT_LOAD_RE.search(sections.get("exit_load", ""))
        if not match:
            return None
        raw = match.group(1).strip(" .;")
        raw = re.sub(
            r"^Exit load(?:\s+of)?\s*:?\s*", "", raw, flags=re.IGNORECASE
        ).strip()
        if re.fullmatch(r"(?:nil|na|not applicable)", raw, re.IGNORECASE):
            value = "Nil"
            normalized: float | str = 0.0
            unit = "percent"
        else:
            percent = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%", raw)
            within = re.search(
                r"within\s+([0-9]+)\s*(years?|months?)", raw, re.IGNORECASE
            )
            if percent:
                value = f"{percent.group(1)}%"
                if within:
                    value += (
                        f" if redeemed within {within.group(1)} "
                        f"{within.group(2).lower()}"
                    )
                normalized = float(percent.group(1))
                unit = "percent"
            else:
                value = re.split(r"(?<=[.!?])\s+", raw, maxsplit=1)[0].strip()
                normalized = value
                unit = "text"
        return _base_fact(
            doc,
            fact_type,
            value,
            value_normalized=normalized,
            unit=unit,
        )

    if fact_type == "min_sip":
        match = _first_match(all_text, MIN_SIP_RES)
        if not match:
            return None
        amount_text = match.group(1).replace(",", "")
        return _base_fact(
            doc,
            fact_type,
            f"₹{int(amount_text):,}",
            value_normalized=int(amount_text),
            unit="INR",
        )

    if fact_type == "lock_in":
        if "ELSS" not in doc.title.upper() and "elss" not in _source_slug(doc):
            return None
        match = _first_match(all_text, LOCK_IN_RES)
        if not match:
            return None
        years = int(match.group(1))
        return _base_fact(
            doc,
            fact_type,
            f"{years} years",
            value_normalized=years * 12,
            unit="months",
        )

    if fact_type == "riskometer":
        match = RISK_RE.search(all_text)
        if not match:
            return None
        value = f"{match.group(1).title()} Risk"
        return _base_fact(doc, fact_type, value, value_normalized=value, unit="text")

    if fact_type == "benchmark":
        match = BENCHMARK_RE.search(sections.get("about_benchmark", all_text))
        if not match:
            return None
        value = match.group(1).strip(" .;")
        return _base_fact(doc, fact_type, value, value_normalized=value, unit="text")

    if fact_type == "statement_process":
        statement_section = next(
            (
                section
                for section in doc.sections
                if "statement" in section.heading.lower()
                or "statement" in section.text.lower()
            ),
            None,
        )
        if not statement_section:
            return None
        value = statement_section.text
        return _base_fact(doc, fact_type, value, value_normalized=value, unit="steps")

    return None


def _extract_amc_fact(doc: CleanDocument, all_text: str) -> FactCard | None:
    overview = next(
        (section.text for section in doc.sections if section.section_id == "overview"),
        "",
    )
    key_information = next(
        (section.text for section in doc.sections if section.section_id == "key_information"),
        "",
    )

    aum = AUM_RE.search(key_information) or AUM_RE.search(overview)
    schemes = SCHEMES_RE.search(overview)
    age = AGE_RE.search(overview)
    incorporation = INCORPORATION_RE.search(key_information)
    investors = INVESTORS_RE.search(overview)
    sebi = SEBI_RE.search(key_information) or SEBI_RE.search(all_text)

    parts: list[str] = [doc.title]
    details: dict[str, Any] = {}
    if aum:
        details["aum_cr"] = aum.group(1)
        parts.append(f"AUM ₹{aum.group(1)} Cr")
    if schemes:
        details["scheme_count"] = int(schemes.group(1))
        parts.append(f"{schemes.group(1)} schemes")
    if age:
        details["amc_age"] = age.group(1)
        parts.append(f"AMC age {age.group(1)}")
    if incorporation:
        details["incorporation_date"] = incorporation.group(1).strip()
        parts.append(f"incorporated {incorporation.group(1).strip()}")
    if investors:
        details["investor_count"] = investors.group(1).strip()
        parts.append(f"{investors.group(1).strip()} investors")
    if sebi:
        details["sebi_registration"] = sebi.group(1)
        parts.append(f"SEBI registration {sebi.group(1)}")

    if len(parts) == 1:
        return None

    value = "; ".join(parts)
    return _base_fact(doc, "amc_overview", value, value_normalized=value, unit="text", details=details)


def extract_facts(doc: CleanDocument) -> list[FactCard]:
    """Return extracted and explicit missing fact cards for one clean document."""
    sections = _section_map(doc)
    all_text = "\n".join(section.text for section in doc.sections)
    page_type = _page_type(doc)

    facts: list[FactCard] = []
    if page_type == "official_education":
        # Approved education/registrar pages provide official support citations
        # for refusals; they do not contribute scheme fact cards.
        return facts
    if page_type == "amc_overview":
        amc_fact = _extract_amc_fact(doc, all_text)
        facts.append(amc_fact or _missing_fact(doc, "amc_overview", "AMC overview facts not found"))
        required = ["amc_overview"]
    elif page_type == "statement":
        required = ["statement_process"]
    else:
        required = [
            "expense_ratio",
            "exit_load",
            "min_sip",
            "riskometer",
            "benchmark",
        ]
        if "ELSS" in doc.title.upper() or "elss" in _source_slug(doc):
            required.insert(3, "lock_in")

    for fact_type in required:
        if fact_type == "amc_overview":
            continue
        fact = _extract_scheme_fact(doc, fact_type, sections, all_text)
        facts.append(fact or _missing_fact(doc, fact_type, f"{fact_type} not found in loaded sections"))

    return facts
