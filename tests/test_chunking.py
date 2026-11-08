"""Tests for page-type-aware chunking and fact-card extraction."""

from __future__ import annotations

from datetime import datetime, timezone

from src.chunker import CHUNKER_VERSION, chunk_document, detect_conflicts
from src.models import Chunk, CleanDocument, CleanSection, FactCard

CFG = {
    "fact_card_max_tokens": 96,
    "narrative_tokens": 200,
    "overlap_tokens": 30,
    "hard_max_tokens": 240,
}


def _elss_doc() -> CleanDocument:
    sections = [
        CleanSection(
            section_id="overview",
            heading="Scheme overview",
            text=(
                "HDFC ELSS Tax Saver Fund Direct Plan Growth ELSS • 3Y Lock-in "
                "Equity ELSS Very High Risk"
            ),
            order=1,
        ),
        CleanSection(
            section_id="key_facts",
            heading="Key facts",
            text="NAV: 30 Oct '26 ₹1,434.92 Min. for SIP ₹500 Expense ratio 1.20%",
            order=2,
        ),
        CleanSection(
            section_id="minimum_investments",
            heading="Minimum investments",
            text="Minimum investments Min. for 1st investment ₹500 Min. for SIP ₹500",
            order=3,
        ),
        CleanSection(
            section_id="exit_load",
            heading="Exit load and stamp duty",
            text="Exit load Nil Stamp duty on investment: 0.005%",
            order=4,
        ),
        CleanSection(
            section_id="about_benchmark",
            heading="About the scheme and benchmark",
            text=(
                "About the fund. Fund benchmark NIFTY 500 Total Return Index "
                "Scheme Information Document(SID)"
            ),
            order=5,
        ),
    ]
    clean_text = "\n\n".join(f"{s.heading}: {s.text}" for s in sections)
    return CleanDocument(
        source_id="seed_hdfc_elss",
        url="https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth",
        final_url="https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth",
        title="HDFC ELSS Tax Saver Fund Direct Plan Growth",
        publisher="Groww",
        source_type="seed",
        citable=False,
        scheme_slugs=["hdfc-elss-tax-saver-fund-direct-plan-growth"],
        status="cleaned",
        http_status=200,
        fetched_at=datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc),
        content_hash="sha256:test",
        clean_text_hash="sha256:test-clean",
        sections=sections,
        removed_sections=[],
        clean_text=clean_text,
    )


def test_chunking_is_deterministic_and_bounded():
    doc = _elss_doc()
    first_chunks, first_facts = chunk_document(doc, CFG)
    second_chunks, second_facts = chunk_document(doc, CFG)

    assert [c.chunk_id for c in first_chunks] == [c.chunk_id for c in second_chunks]
    assert [f.fact_id for f in first_facts] == [f.fact_id for f in second_facts]
    assert first_chunks
    assert max(chunk.token_count for chunk in first_chunks) <= 240
    assert all(chunk.citable is False for chunk in first_chunks)
    assert all(chunk.chunker_version == CHUNKER_VERSION for chunk in first_chunks)


def test_elss_lock_in_and_required_facts_are_explicit():
    _, facts = chunk_document(_elss_doc(), CFG)
    by_type = {fact.fact_type: fact for fact in facts}

    assert by_type["expense_ratio"].value_text == "1.20%"
    assert by_type["min_sip"].value_text == "₹500"
    assert by_type["exit_load"].value_text == "Nil"
    assert by_type["lock_in"].value_text == "3 years"
    assert by_type["lock_in"].value_normalized == 36
    assert by_type["riskometer"].value_text == "Very High Risk"
    assert by_type["benchmark"].value_text == "NIFTY 500 Total Return Index"
    assert "statement_process" not in by_type
    assert all(fact.verified is False for fact in facts)
    assert all(fact.status in {"seed_only", "missing"} for fact in facts)


def test_every_chunk_and_fact_passes_schema_validation():
    chunks, facts = chunk_document(_elss_doc(), CFG)

    for chunk in chunks:
        assert Chunk.model_validate(chunk.model_dump()) == chunk
    for fact in facts:
        assert FactCard.model_validate(fact.model_dump()) == fact


def test_conflicting_official_facts_are_flagged():
    base = {
        "scheme": "HDFC Large Cap Fund Direct Growth",
        "scheme_slug": "hdfc-large-cap-fund-direct-growth",
        "fact_type": "expense_ratio",
        "source_type": "amc",
        "verified": True,
        "status": "verified",
        "unit": "percent",
        "source_url": "https://www.hdfcfund.com/example",
        "retrieved_at": datetime(2026, 11, 1, tzinfo=timezone.utc),
    }
    one = FactCard(
        **base,
        fact_id="a__expense_ratio",
        source_id="amc_a",
        value_text="1.02%",
        value_normalized=1.02,
    )
    two = FactCard(
        **base,
        fact_id="b__expense_ratio",
        source_id="amc_b",
        value_text="1.05%",
        value_normalized=1.05,
    )

    conflicted = detect_conflicts([one, two])

    assert conflicted == {"a__expense_ratio", "b__expense_ratio"}
    assert one.conflict is True and two.conflict is True
    assert one.status == "conflict" and two.status == "conflict"
    assert one.chunk_id is None and two.chunk_id is None
