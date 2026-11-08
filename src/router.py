"""Deterministic intent, scheme, and fact-type routing."""

from __future__ import annotations

import re

from src.models import QueryPlan

SCHEMES: list[tuple[str, str, list[re.Pattern[str]]]] = [
    (
        "hdfc-large-cap-fund-direct-growth",
        "HDFC Large Cap Fund Direct Growth",
        [
            re.compile(r"\bhdfc\s+large[\s-]*cap\b", re.IGNORECASE),
            re.compile(r"\blarge[\s-]*cap\b", re.IGNORECASE),
        ],
    ),
    (
        "hdfc-flexi-cap-direct-plan-growth",
        "HDFC Flexi Cap Direct Plan Growth",
        [
            re.compile(r"\bhdfc\s+flexi[\s-]*cap\b", re.IGNORECASE),
            re.compile(r"\bflexi[\s-]*cap\b", re.IGNORECASE),
            re.compile(r"\bequity\s+fund\b", re.IGNORECASE),
        ],
    ),
    (
        "hdfc-elss-tax-saver-fund-direct-plan-growth",
        "HDFC ELSS Tax Saver Fund Direct Plan Growth",
        [
            re.compile(r"\bhdfc\s+elss\b", re.IGNORECASE),
            re.compile(r"\belss\b", re.IGNORECASE),
            re.compile(r"\btax\s*saver\b", re.IGNORECASE),
            re.compile(r"\btax\s*saving\b", re.IGNORECASE),
        ],
    ),
    (
        "hdfc-mid-cap-fund-direct-growth",
        "HDFC Mid Cap Fund Direct Growth",
        [
            re.compile(r"\bhdfc\s+mid[\s-]*cap\b", re.IGNORECASE),
            re.compile(r"\bmid[\s-]*cap\b", re.IGNORECASE),
        ],
    ),
    (
        "hdfc-mutual-fund",
        "HDFC Mutual Fund",
        [
            re.compile(r"\bhdfc\s+mutual\s+fund\b", re.IGNORECASE),
            re.compile(r"\bfund\s+house\b", re.IGNORECASE),
            re.compile(r"\bamc\b", re.IGNORECASE),
        ],
    ),
]

FACT_TYPES: list[tuple[str, list[re.Pattern[str]]]] = [
    (
        "statement_process",
        [
            re.compile(r"\bcapital[\s-]*gains?\s+statement\b", re.IGNORECASE),
            re.compile(r"\bdownload\b[^?]{0,40}\bstatement\b", re.IGNORECASE),
            re.compile(r"\bstatement\b", re.IGNORECASE),
            re.compile(r"\bcas\b", re.IGNORECASE),
        ],
    ),
    (
        "expense_ratio",
        [
            re.compile(r"\bexpense\s+ratio\b", re.IGNORECASE),
            re.compile(r"\bter\b", re.IGNORECASE),
            re.compile(r"\btotal\s+expense\b", re.IGNORECASE),
        ],
    ),
    (
        "exit_load",
        [
            re.compile(r"\bexit\s+load\b", re.IGNORECASE),
            re.compile(r"\bredemption\s+fee\b", re.IGNORECASE),
        ],
    ),
    (
        "min_sip",
        [
            re.compile(r"\bminimum\s+sip\b", re.IGNORECASE),
            re.compile(r"\bmin\.?\s+sip\b", re.IGNORECASE),
            re.compile(r"\bsip\s+amount\b", re.IGNORECASE),
        ],
    ),
    (
        "lock_in",
        [
            re.compile(r"\block[\s-]*in\b", re.IGNORECASE),
        ],
    ),
    (
        "riskometer",
        [
            re.compile(r"\briskometer\b", re.IGNORECASE),
            re.compile(r"\brisk\s+level\b", re.IGNORECASE),
            re.compile(r"\brisk\b", re.IGNORECASE),
        ],
    ),
    (
        "benchmark",
        [
            re.compile(r"\bbenchmark\b", re.IGNORECASE),
            re.compile(r"\bindex\b", re.IGNORECASE),
        ],
    ),
    (
        "amc_overview",
        [
            re.compile(r"\bregistrar\b", re.IGNORECASE),
            re.compile(r"\bfund\s+house\b", re.IGNORECASE),
            re.compile(r"\bamc\b", re.IGNORECASE),
            re.compile(r"\baum\b", re.IGNORECASE),
            re.compile(r"\bscheme\s+count\b", re.IGNORECASE),
            re.compile(r"\bincorporated\b", re.IGNORECASE),
        ],
    ),
]

SCHEME_FACTS = {
    "expense_ratio",
    "exit_load",
    "min_sip",
    "lock_in",
    "riskometer",
    "benchmark",
}
AMC_FACTS = {"amc_overview", "statement_process"}

CLARIFY_SCHEME = (
    "Which scheme do you mean: HDFC Large Cap, HDFC Flexi Cap, "
    "HDFC ELSS Tax Saver, or HDFC Mid Cap?"
)
CLARIFY_FACT = (
    "Which fact do you want: expense ratio, exit load, minimum SIP, lock-in, "
    "riskometer, benchmark, or statement process?"
)


def _find(text: str, aliases: list[tuple[str, str, list[re.Pattern[str]]]]):
    found: list[tuple[int, int, str, str, str]] = []
    for order, (value, label, patterns) in enumerate(aliases):
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                found.append((match.start(), order, value, label, match.group(0)))
    return sorted(found)


def _find_facts(text: str):
    found: list[tuple[int, int, str, str]] = []
    for order, (value, patterns) in enumerate(FACT_TYPES):
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                found.append((match.start(), order, value, match.group(0)))
    return sorted(found)


def route(question: str) -> QueryPlan:
    """Resolve one factual question into a scheme and fact type."""
    text = " ".join((question or "").split())
    scheme_hits = _find(text, SCHEMES)
    fact_hits = _find_facts(text)

    scheme_slugs: list[str] = []
    scheme_labels: dict[str, str] = {}
    matched_terms: list[str] = []
    for _, _, slug, label, term in scheme_hits:
        if slug not in scheme_slugs:
            scheme_slugs.append(slug)
        scheme_labels[slug] = label
        matched_terms.append(term)

    fact_types: list[str] = []
    for _, _, fact_type, term in fact_hits:
        if fact_type not in fact_types:
            fact_types.append(fact_type)
        matched_terms.append(term)

    fact_type = fact_types[0] if fact_types else None

    # A bare AMC question such as "What is HDFC Mutual Fund?" is an overview
    # request, not an ambiguous scheme-level fact request.
    if not fact_type and scheme_slugs == ["hdfc-mutual-fund"]:
        fact_type = "amc_overview"
        fact_types = ["amc_overview"]

    # "HDFC AMC Large Cap Fund" names the AMC as the fund house, not a second
    # scheme. Drop the AMC match when a real scheme and a scheme-level fact
    # are present.
    if len(scheme_slugs) > 1 and "hdfc-mutual-fund" in scheme_slugs:
        non_amc = [slug for slug in scheme_slugs if slug != "hdfc-mutual-fund"]
        if fact_type in AMC_FACTS:
            scheme_slugs = ["hdfc-mutual-fund"]
        elif non_amc:
            scheme_slugs = non_amc

    if len(scheme_slugs) > 1:
        return QueryPlan(
            intent="ambiguous",
            fact_type=fact_type,
            fact_types=fact_types,
            needs_clarification=True,
            clarification=CLARIFY_SCHEME,
            confidence=0.35,
            matched_terms=matched_terms,
        )

    if not fact_type:
        return QueryPlan(
            intent="ambiguous",
            scheme=scheme_labels.get(scheme_slugs[0]) if scheme_slugs else None,
            scheme_slug=scheme_slugs[0] if scheme_slugs else None,
            needs_clarification=True,
            clarification=CLARIFY_FACT,
            confidence=0.4,
            matched_terms=matched_terms,
        )

    scheme_slug = scheme_slugs[0] if scheme_slugs else None
    if fact_type in AMC_FACTS:
        scheme_slug = "hdfc-mutual-fund"

    if (scheme_slug is None or scheme_slug == "hdfc-mutual-fund") and fact_type in SCHEME_FACTS:
        return QueryPlan(
            intent="ambiguous",
            fact_type=fact_type,
            fact_types=fact_types,
            needs_clarification=True,
            clarification=CLARIFY_SCHEME,
            confidence=0.45,
            matched_terms=matched_terms,
        )

    scheme = scheme_labels.get(scheme_slug) if scheme_slug else None
    if scheme_slug == "hdfc-mutual-fund":
        scheme = "HDFC Mutual Fund"

    return QueryPlan(
        intent="factual",
        scheme=scheme,
        scheme_slug=scheme_slug,
        fact_type=fact_type,
        fact_types=fact_types,
        needs_clarification=False,
        confidence=0.95 if scheme_slug else 0.7,
        matched_terms=matched_terms,
    )
