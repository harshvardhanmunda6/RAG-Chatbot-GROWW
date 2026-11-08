"""Phase 6 answer-generation tests: generation, validation, citation, fallback."""

from __future__ import annotations

import csv
import json
import re
import zlib

import numpy as np

from src.config import Settings
from src.embedder import embed_chunks, write_embedding_artifacts
from src.models import Chunk, Citation, FactCard, QueryPlan, SearchHit
from src.prompt_builder import build_prompt
from src.service import answer_question
from src.validator import count_sentences, validate_answer
from src.vector_store import build_vector_store

LARGE = "hdfc-large-cap-fund-direct-growth"
MID = "hdfc-mid-cap-fund-direct-growth"
LARGE_NAME = "HDFC Large Cap Fund Direct Growth"
MID_NAME = "HDFC Mid Cap Fund Direct Growth"


class FakeMiniLM:
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


class FakeProvider:
    name = "fake"
    api_key_env = "FAKE_API_KEY"
    default_base_url = "https://fake.invalid"

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[dict[str, str]] = []

    def validate_model(self, model: str) -> None:
        self.calls.append({"validate": model})

    def generate(self, system, user, *, model, temperature, max_tokens, timeout_s):
        self.calls.append({"system": system, "user": user, "model": model})
        if not self.responses:
            raise AssertionError("unexpected provider call")
        return self.responses.pop(0)


def _settings(tmp_path) -> Settings:
    return Settings(
        root=tmp_path,
        data={
            "app": {"max_question_chars": 500, "max_answer_sentences": 3},
            "paths": {
                "chunks": "data/03_chunks",
                "embeddings": "data/04_embeddings",
                "vector": "data/05_vector",
                "retrieval": "data/06_retrieval",
                "answers": "data/07_answers",
            },
            "sources": {
                "registry": "data/00_sources/sources.csv",
                "strict_seed_only": False,
                "min_total_sources": 0,
                "max_total_sources": 100,
                "min_official_sources": 0,
                "allowed_domains": ["hdfcfund.com", "sebi.gov.in"],
                "allowed_citation_types": ["amc", "sebi", "amfi", "registrar"],
            },
            "embedding": {
                "model": "sentence-transformers/all-MiniLM-L6-v2",
                "device": "cpu",
                "batch_size": 32,
                "normalize": True,
                "max_seq_length": 256,
            },
            "vector_db": {"collection_prefix": "mf_facts_v1", "distance": "cosine"},
            "retrieval": {
                "top_k": 12,
                "final_k": 4,
                "min_similarity": 0.30,
                "mmr_lambda": 0.70,
                "exact_fact_boost": 0.05,
            },
            "llm": {
                "provider": "groq",
                "provider_env": "LLM_PROVIDER",
                "model_env": "LLM_MODEL",
                "api_key_env": "GROQ_API_KEY",
                "temperature": 0.1,
                "max_tokens": 220,
                "timeout_s": 5,
            },
            "answer": {"require_citation": True, "allow_extractive_fallback": True},
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
) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        source_id="source_" + chunk_id,
        source_url="https://www.hdfcfund.com/fact-sheet",
        source_type="amc" if citable else "seed",
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


def _fixture() -> tuple[list[Chunk], list[FactCard]]:
    chunks = [
        _chunk(
            "large_expense",
            f"{LARGE_NAME} expense ratio is 1.02%.",
            citable=True,
            scheme_slug=LARGE,
            scheme=LARGE_NAME,
            fact_type="expense_ratio",
        ),
        _chunk(
            "large_exit",
            f"{LARGE_NAME} exit load is 1% for redemption within one year.",
            citable=True,
            scheme_slug=LARGE,
            scheme=LARGE_NAME,
            fact_type="exit_load",
        ),
        _chunk(
            "mid_expense",
            f"{MID_NAME} expense ratio is 0.85%.",
            citable=True,
            scheme_slug=MID,
            scheme=MID_NAME,
            fact_type="expense_ratio",
        ),
    ]
    facts = [
        _fact(chunks[0], "1.02%"),
        _fact(chunks[1], "1%"),
        _fact(chunks[2], "0.85%"),
    ]
    return chunks, facts


def _write_registry(settings: Settings, *, large_expense_citable: bool = True) -> None:
    path = settings.root / settings.sources["registry"]
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "source_id": "source_large_expense",
            "url": "https://www.hdfcfund.com/funds/hdfc-large-cap",
            "title": "HDFC Large Cap Fund",
            "publisher": "HDFC Asset Management Company",
            "source_type": "amc",
            "purpose": "scheme facts",
            "scheme_slugs": LARGE,
            "status": "approved",
            "citable": "true" if large_expense_citable else "false",
            "http_status": "200",
            "fetched_at": "2026-10-30T10:00:00Z",
            "last_modified": "",
            "content_hash": "sha256:large",
            "parser": "html",
            "notes": "",
        },
        {
            "source_id": "source_large_exit",
            "url": "https://www.hdfcfund.com/funds/hdfc-large-cap-exit-load",
            "title": "HDFC Large Cap Fund exit load",
            "publisher": "HDFC Asset Management Company",
            "source_type": "amc",
            "purpose": "scheme facts",
            "scheme_slugs": LARGE,
            "status": "approved",
            "citable": "true",
            "http_status": "200",
            "fetched_at": "2026-10-30T10:00:00Z",
            "last_modified": "",
            "content_hash": "sha256:exit",
            "parser": "html",
            "notes": "",
        },
        {
            "source_id": "source_mid_expense",
            "url": "https://www.hdfcfund.com/funds/hdfc-mid-cap",
            "title": "HDFC Mid Cap Fund",
            "publisher": "HDFC Asset Management Company",
            "source_type": "amc",
            "purpose": "scheme facts",
            "scheme_slugs": MID,
            "status": "approved",
            "citable": "true",
            "http_status": "200",
            "fetched_at": "2026-10-30T10:00:00Z",
            "last_modified": "",
            "content_hash": "sha256:mid",
            "parser": "html",
            "notes": "",
        },
        {
            "source_id": "source_sebi_education",
            "url": "https://www.sebi.gov.in/investor-education.html",
            "title": "SEBI Investor Education",
            "publisher": "SEBI",
            "source_type": "sebi",
            "purpose": "investor education",
            "scheme_slugs": "",
            "status": "approved",
            "citable": "true",
            "http_status": "200",
            "fetched_at": "2026-10-29T09:00:00Z",
            "last_modified": "",
            "content_hash": "sha256:sebi",
            "parser": "html",
            "notes": "",
        },
        {
            "source_id": "source_amc_overview",
            "url": "https://www.hdfcfund.com/about-us",
            "title": "HDFC Mutual Fund Overview",
            "publisher": "HDFC Asset Management Company",
            "source_type": "amc",
            "purpose": "AMC overview",
            "scheme_slugs": "hdfc-mutual-fund",
            "status": "approved",
            "citable": "true",
            "http_status": "200",
            "fetched_at": "2026-10-28T09:00:00Z",
            "last_modified": "",
            "content_hash": "sha256:overview",
            "parser": "html",
            "notes": "",
        },
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _prepare(tmp_path, *, large_expense_citable: bool = True) -> Settings:
    settings = _settings(tmp_path)
    _write_registry(settings, large_expense_citable=large_expense_citable)
    chunks, facts = _fixture()
    all_vectors, citable_vectors, manifest = embed_chunks(
        chunks, settings, model=FakeMiniLM(), model_revision="test-revision"
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


def _llm_json(answer: str, chunk_id: str = "large_expense", answer_type: str = "fact") -> str:
    return json.dumps(
        {
            "answer_type": answer_type,
            "answer": answer,
            "citation_chunk_id": chunk_id,
            "facts_used": ["expense_ratio"],
        }
    )


def test_valid_llm_answer_has_one_citation_and_date(tmp_path):
    settings = _prepare(tmp_path)
    provider = FakeProvider(
        [_llm_json("The expense ratio of HDFC Large Cap Fund Direct Growth is 1.02%.")]
    )

    response = answer_question(
        "What is the expense ratio of HDFC Large Cap Fund Direct Growth?",
        settings,
        provider=provider,
        model="fake-model",
        embed_model=FakeMiniLM(),
    )

    assert response.answer_type == "fact"
    assert response.citation is not None
    assert response.citation.url == "https://www.hdfcfund.com/funds/hdfc-large-cap"
    assert response.citation.last_updated == "2026-10-30"
    assert response.source_badge == "AMC"
    assert response.fallback_used is False
    assert response.validation["passed"] is True
    assert response.answer.count("https://") == 0
    assert len([response.citation]) == 1

    answers_dir = settings.path("answers")
    artifact = json.loads(
        (answers_dir / f"{response.trace_id}.json").read_text(encoding="utf-8")
    )
    assert artifact["citation"]["url"] == response.citation.url
    prompt = (answers_dir / f"{response.trace_id}.prompt.txt").read_text(encoding="utf-8")
    assert "<chunk id=" in prompt
    assert "https://" not in prompt
    raw = json.loads(
        (answers_dir / f"{response.trace_id}.llm_raw.json").read_text(encoding="utf-8")
    )
    assert raw["provider"] == "fake"
    assert len(raw["responses"]) == 1


def test_invalid_json_is_repaired_once(tmp_path):
    settings = _prepare(tmp_path)
    provider = FakeProvider(
        [
            "this is not json",
            _llm_json("The expense ratio of HDFC Large Cap Fund Direct Growth is 1.02%."),
        ]
    )

    response = answer_question(
        "What is the expense ratio of HDFC Large Cap Fund Direct Growth?",
        settings,
        provider=provider,
        model="fake-model",
        embed_model=FakeMiniLM(),
    )

    assert response.answer_type == "fact"
    assert response.fallback_used is False
    calls = [call for call in provider.calls if "user" in call]
    assert len(calls) == 2
    assert "not valid JSON" in calls[1]["user"]


def test_invalid_json_twice_uses_extractive_fallback(tmp_path):
    settings = _prepare(tmp_path)
    provider = FakeProvider(["nope", "still nope"])

    response = answer_question(
        "What is the expense ratio of HDFC Large Cap Fund Direct Growth?",
        settings,
        provider=provider,
        model="fake-model",
        embed_model=FakeMiniLM(),
    )

    assert response.answer_type == "fact"
    assert response.fallback_used is True
    assert response.fallback_reason == "invalid_json"
    assert response.answer == (
        "The expense ratio of HDFC Large Cap Fund Direct Growth is 1.02%."
    )
    assert response.citation is not None
    assert response.validation["passed"] is True


def test_missing_model_uses_extractive_without_provider_call(tmp_path, monkeypatch):
    settings = _prepare(tmp_path)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    response = answer_question(
        "What is the expense ratio of HDFC Large Cap Fund Direct Growth?",
        settings,
        embed_model=FakeMiniLM(),
    )

    assert response.answer_type == "fact"
    assert response.fallback_used is True
    assert response.fallback_reason == "missing_model"
    assert response.provider is None
    assert response.answer == (
        "The expense ratio of HDFC Large Cap Fund Direct Growth is 1.02%."
    )


def test_numeric_hallucination_is_rejected_and_falls_back(tmp_path):
    settings = _prepare(tmp_path)
    wrong = _llm_json("The expense ratio of HDFC Large Cap Fund Direct Growth is 2.50%.")
    provider = FakeProvider([wrong, wrong])

    response = answer_question(
        "What is the expense ratio of HDFC Large Cap Fund Direct Growth?",
        settings,
        provider=provider,
        model="fake-model",
        embed_model=FakeMiniLM(),
    )

    assert response.answer_type == "fact"
    assert response.fallback_used is True
    assert response.fallback_reason == "validation_failed:numbers_grounded"
    assert "2.50%" not in response.answer
    assert "1.02%" in response.answer
    assert len([call for call in provider.calls if "user" in call]) == 2


def test_advice_leak_is_rejected_and_falls_back(tmp_path):
    settings = _prepare(tmp_path)
    bad = _llm_json("You should buy HDFC Large Cap Fund now.")
    provider = FakeProvider([bad, bad])

    response = answer_question(
        "What is the expense ratio of HDFC Large Cap Fund Direct Growth?",
        settings,
        provider=provider,
        model="fake-model",
        embed_model=FakeMiniLM(),
    )

    assert response.fallback_reason == "validation_failed:no_advice"
    assert "buy" not in response.answer.lower()
    assert response.validation["passed"] is True


def test_sentence_limit_is_rejected_and_falls_back(tmp_path):
    settings = _prepare(tmp_path)
    bad = _llm_json(
        "The expense ratio is 1.02%. It is a factual figure. It comes from the source. "
        "This extra sentence breaks the limit."
    )
    provider = FakeProvider([bad, bad])

    response = answer_question(
        "What is the expense ratio of HDFC Large Cap Fund Direct Growth?",
        settings,
        provider=provider,
        model="fake-model",
        embed_model=FakeMiniLM(),
    )

    assert response.fallback_reason == "validation_failed:sentence_limit"
    assert count_sentences(response.answer) <= 3


def test_seed_source_citation_is_rejected(tmp_path):
    settings = _prepare(tmp_path, large_expense_citable=False)
    provider = FakeProvider(
        [
            _llm_json("The expense ratio of HDFC Large Cap Fund Direct Growth is 1.02%."),
            _llm_json("The expense ratio of HDFC Large Cap Fund Direct Growth is 1.02%."),
        ]
    )

    response = answer_question(
        "What is the expense ratio of HDFC Large Cap Fund Direct Growth?",
        settings,
        provider=provider,
        model="fake-model",
        embed_model=FakeMiniLM(),
    )

    assert response.answer_type == "no_answer"
    assert response.citation is not None
    assert response.citation.source_id != "source_large_expense"
    assert response.citation.url == "https://www.hdfcfund.com/about-us"
    assert response.validation["passed"] is False


def test_refusal_skips_provider_and_uses_education_citation(tmp_path):
    settings = _prepare(tmp_path)
    provider = FakeProvider([])

    response = answer_question(
        "Should I buy HDFC Mid Cap Fund?",
        settings,
        provider=provider,
        model="fake-model",
        embed_model=BoomModel(),
    )

    assert response.answer_type == "refusal"
    assert response.intent == "advice"
    assert response.fallback_used is False
    assert response.citation is not None
    assert response.citation.source_type == "sebi"
    assert response.citation.url == "https://www.sebi.gov.in/investor-education.html"
    assert response.citation.last_updated == "2026-10-29"
    assert provider.calls == []


def test_pii_is_not_written_to_answer_artifacts(tmp_path):
    settings = _prepare(tmp_path)

    response = answer_question(
        "My PAN is ABCDE1234F",
        settings,
        embed_model=BoomModel(),
    )

    assert response.answer_type == "refusal"
    assert response.intent == "pii"
    assert "ABCDE1234F" not in json.dumps(
        response.model_dump(mode="json"), ensure_ascii=False
    )
    answers_dir = settings.path("answers")
    artifact = (answers_dir / f"{response.trace_id}.json").read_text(encoding="utf-8")
    eval_csv = (answers_dir / "answers_eval.csv").read_text(encoding="utf-8")
    assert "ABCDE1234F" not in artifact
    assert "ABCDE1234F" not in eval_csv
    assert "[REDACTED_PAN]" in artifact


def test_missing_scheme_clarifies_without_citation(tmp_path):
    settings = _prepare(tmp_path)
    provider = FakeProvider([])

    response = answer_question(
        "What is the expense ratio?",
        settings,
        provider=provider,
        model="fake-model",
        embed_model=BoomModel(),
    )

    assert response.answer_type == "clarification"
    assert response.citation is None
    assert response.last_updated
    assert provider.calls == []


def test_prompt_hash_is_stable_and_context_has_no_urls():
    settings = _settings(__import__("pathlib").Path("/tmp/unused"))
    plan = QueryPlan(
        intent="factual",
        scheme=LARGE_NAME,
        scheme_slug=LARGE,
        fact_type="expense_ratio",
        fact_types=["expense_ratio"],
    )
    evidence = [
        SearchHit(
            chunk_id="large_expense",
            text="HDFC Large Cap Fund Direct Growth expense ratio is 1.02%.",
            metadata={
                "source_type": "amc",
                "scheme": LARGE_NAME,
                "scheme_slug": LARGE,
                "fact_type": "expense_ratio",
                "citable": True,
            },
            similarity=0.9,
            score=0.9,
        )
    ]

    first = build_prompt("What is the expense ratio?", plan, evidence, settings)
    second = build_prompt("What is the expense ratio?", plan, evidence, settings)

    assert first.prompt_hash == second.prompt_hash
    assert first.evidence_ids == ["large_expense"]
    assert "https://" not in first.text


def test_validator_requires_citation_and_grounded_numbers():
    settings = _settings(__import__("pathlib").Path("/tmp/unused"))
    plan = QueryPlan(
        intent="factual",
        scheme=LARGE_NAME,
        scheme_slug=LARGE,
        fact_type="expense_ratio",
        fact_types=["expense_ratio"],
    )
    evidence = [
        SearchHit(
            chunk_id="large_expense",
            text="HDFC Large Cap Fund Direct Growth expense ratio is 1.02%.",
            metadata={
                "source_type": "amc",
                "scheme": LARGE_NAME,
                "scheme_slug": LARGE,
                "fact_type": "expense_ratio",
                "citable": True,
                "section": "Key facts",
            },
            similarity=0.9,
            score=0.9,
        )
    ]

    missing = validate_answer("The ratio is 1.02%.", None, evidence, plan, settings)
    assert missing.passed is False
    assert missing.reason == "citation_resolved"

    wrong = validate_answer(
        "The ratio is 2.50%.",
        __import__("src.models", fromlist=["Citation"]).Citation(
            source_id="source_large_expense",
            chunk_id="large_expense",
            title="HDFC Large Cap Fund",
            url="https://www.hdfcfund.com/funds/hdfc-large-cap",
            source_type="amc",
            publisher="HDFC Asset Management Company",
            last_updated="2026-10-30",
        ),
        evidence,
        plan,
        settings,
    )
    assert wrong.passed is False
    assert wrong.reason == "numbers_grounded"


def test_validator_allows_total_return_index_benchmark_names():
    settings = _settings(__import__("pathlib").Path("/tmp/unused"))
    plan = QueryPlan(
        intent="factual",
        scheme=LARGE_NAME,
        scheme_slug=LARGE,
        fact_type="benchmark",
        fact_types=["benchmark"],
    )
    evidence = [
        SearchHit(
            chunk_id="large_benchmark",
            text=(
                "HDFC Large Cap Fund Direct Growth benchmark is "
                "NIFTY 100 Total Returns Index."
            ),
            metadata={
                "source_type": "amc",
                "scheme": LARGE_NAME,
                "scheme_slug": LARGE,
                "fact_type": "benchmark",
                "citable": True,
                "section": "Key facts",
            },
            similarity=0.9,
            score=0.9,
        )
    ]
    citation = Citation(
        source_id="source_large_benchmark",
        chunk_id="large_benchmark",
        title="HDFC Large Cap Fund Direct Growth",
        url="https://www.hdfcfund.com/explore/mutual-funds/hdfc-large-cap-fund/direct",
        source_type="amc",
        publisher="HDFC Asset Management Company Limited",
        last_updated="2026-11-07",
    )

    result = validate_answer(
        "The benchmark for HDFC Large Cap Fund Direct Growth is NIFTY 100 Total Returns Index.",
        citation,
        evidence,
        plan,
        settings,
    )

    assert result.passed is True


def test_sentence_counter_handles_decimals_and_abbreviations():
    assert count_sentences("The ratio is 1.02%. It applies now.") == 2
    assert count_sentences("The AMC is HDFC Ltd. It is registered.") == 2
