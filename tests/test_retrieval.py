"""Tests for guardrails, routing, retrieval, ranking, and traces."""

from __future__ import annotations

import json
import re
import zlib

import numpy as np

from src.config import Settings
from src.embedder import embed_chunks, write_embedding_artifacts
from src.guardrails import check_question
from src.models import Chunk, FactCard
from src.retriever import retrieve
from src.router import route
from src.vector_store import build_vector_store


class FakeMiniLM:
    """Deterministic bag-of-words model for stable retrieval tests."""

    def get_embedding_dimension(self) -> int:
        return 384

    def encode(self, texts, **kwargs):
        vectors = np.zeros((len(texts), 384), dtype=np.float32)
        for row, text in enumerate(texts):
            for token in re.findall(r"[a-z0-9]+", text.lower()):
                vectors[row, zlib.crc32(token.encode("utf-8")) % 384] += 1.0
            if not vectors[row].any():
                vectors[row, 0] = 1.0
        return vectors


class BoomModel:
    def get_embedding_dimension(self) -> int:
        return 384

    def encode(self, texts, **kwargs):
        raise AssertionError("embedding model should not be called")


class GateModel:
    """Query matches the wrong-scheme chunk, never the supported chunk."""

    def get_embedding_dimension(self) -> int:
        return 384

    def encode(self, texts, **kwargs):
        vectors = np.zeros((len(texts), 384), dtype=np.float32)
        for row, text in enumerate(texts):
            if text.startswith("RIGHT"):
                vectors[row, 1] = 1.0
            else:
                vectors[row, 0] = 1.0
        return vectors


def _settings(tmp_path) -> Settings:
    return Settings(
        root=tmp_path,
        data={
            "app": {"max_question_chars": 500},
            "paths": {
                "chunks": "data/03_chunks",
                "embeddings": "data/04_embeddings",
                "vector": "data/05_vector",
                "retrieval": "data/06_retrieval",
            },
            "sources": {},
            "embedding": {
                "model": "sentence-transformers/all-MiniLM-L6-v2",
                "device": "cpu",
                "batch_size": 32,
                "normalize": True,
                "max_seq_length": 256,
            },
            "vector_db": {
                "collection_prefix": "mf_facts_v1",
                "distance": "cosine",
            },
            "retrieval": {
                "top_k": 12,
                "final_k": 4,
                "min_similarity": 0.30,
                "mmr_lambda": 0.70,
                "exact_fact_boost": 0.05,
            },
        },
    )


def _chunk(
    chunk_id: str,
    text: str,
    *,
    citable: bool,
    scheme_slug: str,
    scheme: str,
    fact_type: str,
    source_type: str | None = None,
) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        source_id="source_" + chunk_id,
        source_url="https://www.hdfcfund.com/fact-sheet",
        source_type=source_type or ("amc" if citable else "seed"),
        citable=citable,
        page_type="amc_overview" if scheme_slug == "hdfc-mutual-fund" else "scheme_page",
        scheme=scheme,
        scheme_slug=scheme_slug,
        fact_type=fact_type,
        section="Key facts",
        text=text,
        token_count=len(text.split()),
        char_count=len(text),
        content_hash="sha256:" + chunk_id,
        chunker_version="1.0",
    )


def _fact(chunk: Chunk, value_text: str | None = None) -> FactCard:
    return FactCard(
        fact_id=f"{chunk.scheme_slug}__{chunk.fact_type}",
        chunk_id=chunk.chunk_id,
        scheme=chunk.scheme,
        scheme_slug=chunk.scheme_slug,
        fact_type=chunk.fact_type,
        value_text=value_text,
        source_id=chunk.source_id,
        source_url=chunk.source_url,
        source_type=chunk.source_type,
        verified=chunk.citable,
        status="verified" if chunk.citable else "seed_only",
    )


def _prepare(tmp_path, chunks: list[Chunk], facts: list[FactCard], model=None) -> Settings:
    settings = _settings(tmp_path)
    all_vectors, citable_vectors, manifest = embed_chunks(
        chunks, settings, model=model or FakeMiniLM(), model_revision="test-revision"
    )
    write_embedding_artifacts(chunks, all_vectors, citable_vectors, manifest, settings)
    chunks_dir = settings.path("chunks")
    chunks_dir.mkdir(parents=True, exist_ok=True)
    with (chunks_dir / "chunks.jsonl").open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(chunk.model_dump_json() + "\n")
    with (chunks_dir / "facts.jsonl").open("w", encoding="utf-8") as handle:
        for fact in facts:
            handle.write(fact.model_dump_json() + "\n")
    build_vector_store(settings)
    return settings


def _citable_fixture() -> tuple[list[Chunk], list[FactCard]]:
    large = "hdfc-large-cap-fund-direct-growth"
    mid = "hdfc-mid-cap-fund-direct-growth"
    chunks = [
        _chunk(
            "large_expense",
            "HDFC Large Cap Fund Direct Growth expense ratio is 1.02%.",
            citable=True,
            scheme_slug=large,
            scheme="HDFC Large Cap Fund Direct Growth",
            fact_type="expense_ratio",
        ),
        _chunk(
            "large_exit",
            "HDFC Large Cap Fund Direct Growth exit load is 1% for redemption within one year.",
            citable=True,
            scheme_slug=large,
            scheme="HDFC Large Cap Fund Direct Growth",
            fact_type="exit_load",
        ),
        _chunk(
            "mid_expense",
            "HDFC Mid Cap Fund Direct Growth expense ratio is 0.85%.",
            citable=True,
            scheme_slug=mid,
            scheme="HDFC Mid Cap Fund Direct Growth",
            fact_type="expense_ratio",
        ),
    ]
    facts = [
        _fact(chunks[0], "1.02%"),
        _fact(chunks[1], "1%"),
        _fact(chunks[2], "0.85%"),
    ]
    return chunks, facts


def test_guardrails_block_pii_advice_and_performance(tmp_path):
    settings = _settings(tmp_path)

    pii = check_question("My PAN is ABCDE1234F", settings)
    assert pii.allowed is False
    assert pii.intent == "pii"
    assert pii.pii_types == ["pan"]
    assert "ABCDE1234F" not in pii.redacted_question

    aadhaar = check_question("Aadhaar 1234 5678 9012", settings)
    assert aadhaar.allowed is False and "aadhaar" in aadhaar.pii_types

    phone = check_question("Call me on +91 9876543210", settings)
    assert phone.allowed is False and "phone" in phone.pii_types

    email = check_question("Email me at user@example.com", settings)
    assert email.allowed is False and "email" in email.pii_types

    otp = check_question("My OTP is 123456", settings)
    assert otp.allowed is False and "otp" in otp.pii_types

    account = check_question("Account number is 12345678", settings)
    assert account.allowed is False and "account" in account.pii_types

    advice = check_question("Should I buy HDFC Mid Cap?", settings)
    assert advice.allowed is False and advice.intent == "advice"

    performance = check_question("What was the 3-year return of HDFC Large Cap?", settings)
    assert performance.allowed is False and performance.intent == "performance"

    safety = check_question("Is HDFC ELSS safe?", settings)
    assert safety.allowed is False and safety.intent == "advice"

    doubling = check_question("Will this double my money?", settings)
    assert doubling.allowed is False and doubling.intent == "performance"

    tax = check_question("How much tax will I save?", settings)
    assert tax.allowed is False and tax.intent == "out_of_scope"

    factual = check_question("What is the expense ratio of HDFC Large Cap?", settings)
    assert factual.allowed is True and factual.intent == "factual"

    account_holders = check_question("Where can account holders download statements?", settings)
    assert account_holders.allowed is True and account_holders.pii_blocked is False

    sip_amount = check_question("Is the minimum SIP 5000?", settings)
    assert sip_amount.allowed is True and sip_amount.pii_blocked is False


def test_router_resolves_aliases_and_asks_for_missing_entities():
    large = route("What is the expense ratio of HDFC Large Cap Fund Direct Growth?")
    assert large.scheme_slug == "hdfc-large-cap-fund-direct-growth"
    assert large.fact_type == "expense_ratio"
    assert large.needs_clarification is False

    flexi = route("What is the lock-in for HDFC Equity Fund?")
    assert flexi.scheme_slug == "hdfc-flexi-cap-direct-plan-growth"
    assert flexi.fact_type == "lock_in"

    elss = route("What is the ELSS tax saver exit load?")
    assert elss.scheme_slug == "hdfc-elss-tax-saver-fund-direct-plan-growth"
    assert elss.fact_type == "exit_load"

    amc = route("How do I download a capital gains statement?")
    assert amc.scheme_slug == "hdfc-mutual-fund"
    assert amc.fact_type == "statement_process"

    overview = route("What is HDFC Mutual Fund?")
    assert overview.scheme_slug == "hdfc-mutual-fund"
    assert overview.fact_type == "amc_overview"

    amc_fact = route("Who is the registrar of HDFC Large Cap Fund?")
    assert amc_fact.scheme_slug == "hdfc-mutual-fund"
    assert amc_fact.fact_type == "amc_overview"

    amc_name = route("What is the expense ratio of HDFC AMC Large Cap Fund?")
    assert amc_name.scheme_slug == "hdfc-large-cap-fund-direct-growth"

    amc_of_scheme = route("What is the AMC of HDFC Large Cap Fund?")
    assert amc_of_scheme.scheme_slug == "hdfc-mutual-fund"
    assert amc_of_scheme.fact_type == "amc_overview"

    missing_scheme = route("What is the expense ratio?")
    assert missing_scheme.needs_clarification is True
    assert missing_scheme.scheme_slug is None

    amc_scope = route("What is the expense ratio of HDFC Mutual Fund?")
    assert amc_scope.needs_clarification is True

    multiple = route("Compare HDFC Large Cap and HDFC Mid Cap expense ratio")
    assert multiple.needs_clarification is True
    assert "Which scheme" in (multiple.clarification or "")

    primary = route("What is the exit load and expense ratio of HDFC ELSS?")
    assert primary.fact_type == "exit_load"
    assert primary.fact_types == ["exit_load", "expense_ratio"]


def test_retrieval_selects_exact_fact_and_writes_trace(tmp_path):
    chunks, facts = _citable_fixture()
    settings = _prepare(tmp_path, chunks, facts)

    trace = retrieve(
        "What is the expense ratio of HDFC Large Cap Fund Direct Growth?",
        settings,
        model=FakeMiniLM(),
    )

    assert trace.intent == "factual"
    assert trace.scheme_slug == "hdfc-large-cap-fund-direct-growth"
    assert trace.fact_type == "expense_ratio"
    assert trace.evidence_gate["passed"] is True
    assert trace.no_answer_reason is None
    assert trace.selected == ["large_expense"]
    assert trace.selected_hits[0].text.startswith("HDFC Large Cap Fund Direct Growth expense")
    assert trace.selected_hits[0].exact_fact_boost == 0.05
    assert "large_exit" not in trace.selected
    assert "mid_expense" not in trace.selected
    assert len(trace.filter_attempts) >= 2

    trace_path = settings.path("retrieval") / f"{trace.trace_id}.json"
    assert trace_path.exists()
    stored = json.loads(trace_path.read_text(encoding="utf-8"))
    assert stored["selected"] == ["large_expense"]
    assert stored["evidence_gate"]["passed"] is True
    assert stored["timings_ms"]["total"] >= 0

    eval_path = settings.path("retrieval") / "retrieval_eval.csv"
    assert eval_path.exists()
    assert trace.trace_id in eval_path.read_text(encoding="utf-8")


def test_evidence_gate_failure_keeps_candidate_checks(tmp_path):
    settings = _prepare(tmp_path, *_citable_fixture())

    trace = retrieve(
        "What is the benchmark of HDFC Large Cap Fund Direct Growth?",
        settings,
        model=FakeMiniLM(),
    )

    assert trace.no_answer_reason == "no_candidate_passed_gate"
    assert trace.evidence_gate["passed"] is False
    assert trace.evidence_gate["checks"]
    assert all(check["reason"] != "passed" for check in trace.evidence_gate["checks"])
    assert trace.selected == []


def test_pii_is_redacted_from_trace_and_retrieval_is_skipped(tmp_path):
    settings = _prepare(tmp_path, *_citable_fixture())

    trace = retrieve("My PAN is ABCDE1234F", settings, model=BoomModel())

    assert trace.intent == "pii"
    assert trace.pii_blocked is True
    assert trace.pii_types == ["pan"]
    assert trace.no_answer_reason == "pii_detected"
    assert trace.candidates == []
    assert "ABCDE1234F" not in trace.question_redacted

    trace_path = settings.path("retrieval") / f"{trace.trace_id}.json"
    raw = trace_path.read_text(encoding="utf-8")
    assert "ABCDE1234F" not in raw
    assert "[REDACTED_PAN]" in raw


def test_advice_and_performance_traces_skip_retrieval(tmp_path):
    settings = _settings(tmp_path)

    advice = retrieve("Should I buy HDFC Mid Cap?", settings, model=BoomModel())
    assert advice.intent == "advice"
    assert advice.no_answer_reason == "advice_request"
    assert advice.candidates == []

    performance = retrieve(
        "What was the 3-year return of HDFC Large Cap?", settings, model=BoomModel()
    )
    assert performance.intent == "performance"
    assert performance.no_answer_reason == "performance_request"
    assert performance.candidates == []


def test_no_citable_index_fails_closed_without_embedding(tmp_path):
    chunks = [
        _chunk(
            "seed_expense",
            "Seed expense ratio is 1.05%.",
            citable=False,
            scheme_slug="hdfc-large-cap-fund-direct-growth",
            scheme="HDFC Large Cap Fund Direct Growth",
            fact_type="expense_ratio",
        )
    ]
    settings = _prepare(tmp_path, chunks, [])

    trace = retrieve(
        "What is the expense ratio of HDFC Large Cap Fund Direct Growth?",
        settings,
        model=BoomModel(),
    )

    assert trace.intent == "factual"
    assert trace.no_answer_reason == "no_citable_index"
    assert trace.evidence_gate["passed"] is False
    assert trace.candidates == []
    assert (settings.path("retrieval") / f"{trace.trace_id}.json").exists()


def test_clarification_stops_before_embedding(tmp_path):
    settings = _prepare(tmp_path, *_citable_fixture())

    trace = retrieve("What is the expense ratio?", settings, model=BoomModel())

    assert trace.intent == "ambiguous"
    assert trace.needs_clarification is True
    assert trace.clarification and "Which scheme" in trace.clarification
    assert trace.no_answer_reason == "needs_clarification"
    assert trace.candidates == []
    assert (settings.path("retrieval") / f"{trace.trace_id}.json").exists()


def test_gate_ignores_high_similarity_candidate_that_fails_checks(tmp_path):
    chunks = [
        _chunk(
            "right_expense",
            "RIGHT supported expense ratio sentence for the requested scheme.",
            citable=True,
            scheme_slug="hdfc-large-cap-fund-direct-growth",
            scheme="HDFC Large Cap Fund Direct Growth",
            fact_type="expense_ratio",
        ),
        _chunk(
            "wrong_expense",
            "WRONG unsupported expense ratio sentence for another scheme.",
            citable=True,
            scheme_slug="hdfc-mid-cap-fund-direct-growth",
            scheme="HDFC Mid Cap Fund Direct Growth",
            fact_type="expense_ratio",
        ),
    ]
    settings = _prepare(tmp_path, chunks, [], model=GateModel())

    trace = retrieve(
        "What is the expense ratio of HDFC Large Cap Fund Direct Growth?",
        settings,
        model=GateModel(),
    )

    assert trace.candidates
    assert trace.evidence_gate["passed"] is False
    assert trace.no_answer_reason == "top_score_below_threshold"
    assert trace.evidence_gate["top_score"] < 0.30
    assert trace.evidence_gate["candidate_top_score"] > 0.90
    reasons = {check["chunk_id"]: check["reason"] for check in trace.evidence_gate["checks"]}
    assert reasons["wrong_expense"] == "scheme_mismatch"
    assert reasons["right_expense"] == "passed"
    assert trace.selected == []
