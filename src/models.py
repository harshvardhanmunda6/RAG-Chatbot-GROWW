"""Pydantic models for the loading and cleaning stages."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class SourceRecord(BaseModel):
    source_id: str
    url: str
    title: str
    publisher: str
    source_type: str
    purpose: str
    scheme_slugs: list[str] = Field(default_factory=list)
    status: str
    citable: bool
    http_status: int | None = None
    fetched_at: datetime | None = None
    last_modified: datetime | None = None
    content_hash: str | None = None
    parser: str | None = None
    notes: str = ""


class SourceSnapshot(BaseModel):
    source_id: str
    requested_url: str
    final_url: str
    http_status: int
    content_type: str
    fetched_at: datetime
    content_hash: str
    raw_path: str
    meta_path: str
    bytes_written: int
    redirect_history: list[str] = Field(default_factory=list)


class CleanSection(BaseModel):
    section_id: str
    heading: str
    text: str
    order: int


class CleanDocument(BaseModel):
    source_id: str
    url: str
    final_url: str
    title: str
    publisher: str
    source_type: str
    citable: bool = False
    scheme_slugs: list[str] = Field(default_factory=list)
    status: str
    http_status: int | None = None
    fetched_at: datetime | None = None
    content_hash: str | None = None
    clean_text_hash: str
    sections: list[CleanSection] = Field(default_factory=list)
    removed_sections: list[str] = Field(default_factory=list)
    clean_text: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class Chunk(BaseModel):
    chunk_id: str
    source_id: str
    source_url: str
    source_type: str
    citable: bool
    page_type: str
    scheme: str
    scheme_slug: str
    fact_type: str
    section: str
    page: int | None = None
    text: str
    token_count: int
    char_count: int
    content_hash: str
    chunker_version: str
    retrieved_at: datetime | None = None


class FactCard(BaseModel):
    fact_id: str
    chunk_id: str | None = None
    scheme: str
    scheme_slug: str
    fact_type: str
    value_text: str | None = None
    value_normalized: float | int | str | None = None
    unit: str | None = None
    as_of: str | None = None
    source_id: str
    source_url: str
    source_type: str
    verified: bool = False
    conflict: bool = False
    status: str = "unverified"
    crosscheck: dict[str, Any] | None = None
    details: dict[str, Any] | None = None
    retrieved_at: datetime | None = None


class GuardrailResult(BaseModel):
    allowed: bool
    intent: str
    reason: str | None = None
    pii_blocked: bool = False
    pii_types: list[str] = Field(default_factory=list)
    prompt_injection: bool = False
    question_hash: str
    redacted_question: str


class QueryPlan(BaseModel):
    intent: str = "factual"
    scheme: str | None = None
    scheme_slug: str | None = None
    fact_type: str | None = None
    fact_types: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    clarification: str | None = None
    confidence: float = 0.0
    matched_terms: list[str] = Field(default_factory=list)


class SearchHit(BaseModel):
    chunk_id: str
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    similarity: float
    score: float
    exact_fact_boost: float = 0.0


class RetrievalTrace(BaseModel):
    trace_id: str
    created_at: datetime
    question_hash: str
    question_redacted: str
    pii_blocked: bool = False
    pii_types: list[str] = Field(default_factory=list)
    intent: str
    scheme: str | None = None
    scheme_slug: str | None = None
    fact_type: str | None = None
    fact_types: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    clarification: str | None = None
    filters: dict[str, Any] = Field(default_factory=dict)
    filter_attempts: list[dict[str, Any]] = Field(default_factory=list)
    candidates: list[SearchHit] = Field(default_factory=list)
    selected: list[str] = Field(default_factory=list)
    selected_hits: list[SearchHit] = Field(default_factory=list)
    evidence_gate: dict[str, Any] = Field(default_factory=dict)
    no_answer_reason: str | None = None
    timings_ms: dict[str, float] = Field(default_factory=dict)
    embedding_model: str | None = None
    manifest_hash: str | None = None
    config_hash: str | None = None
    collection_name: str | None = None


class LLMAnswer(BaseModel):
    """Strict JSON contract requested from the provider."""

    answer_type: Literal["fact", "no_answer"]
    answer: str
    citation_chunk_id: str | None = None
    facts_used: list[str] = Field(default_factory=list)


class Citation(BaseModel):
    source_id: str
    chunk_id: str | None = None
    title: str
    url: str
    source_type: str
    publisher: str = ""
    last_updated: str


class LLMResult(BaseModel):
    ok: bool
    provider: str | None = None
    model: str | None = None
    parsed: LLMAnswer | None = None
    raw_responses: list[str] = Field(default_factory=list)
    error: str | None = None
    attempts: int = 0
    latency_ms: float = 0.0
    fallback_used: bool = False
    fallback_reason: str | None = None


class ValidationResult(BaseModel):
    passed: bool
    reason: str | None = None
    sentence_count: int = 0
    checks: list[dict[str, Any]] = Field(default_factory=list)


class AnswerResponse(BaseModel):
    trace_id: str
    created_at: datetime
    question_hash: str
    question_redacted: str
    answer_type: str
    answer: str | None = None
    citation: Citation | None = None
    last_updated: str
    source_badge: str | None = None
    fallback_used: bool = False
    fallback_reason: str | None = None
    provider: str | None = None
    model: str | None = None
    prompt_hash: str | None = None
    validation: dict[str, Any] = Field(default_factory=dict)
    retrieval_trace_id: str | None = None
    intent: str = "factual"
    scheme: str | None = None
    scheme_slug: str | None = None
    fact_type: str | None = None
    timings_ms: dict[str, float] = Field(default_factory=dict)
