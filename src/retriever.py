"""Guardrailed dense retrieval with MMR, an evidence gate, and traces."""

from __future__ import annotations

import csv
import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from src.config import Settings
from src.embedder import embed_query
from src.guardrails import check_question
from src.models import FactCard, GuardrailResult, QueryPlan, RetrievalTrace, SearchHit
from src.router import route
from src.vector_store import VectorStoreError, query_citable

OFFICIAL_SOURCE_TYPES = {"amc", "sebi", "amfi", "registrar"}

PERFORMANCE_RE = re.compile(
    r"\b(?:returns?|cagr|annuali[sz]ed|alpha|beta|sharpe|ranking|ranked)\b",
    re.IGNORECASE,
)

FACT_TEXT_HINTS: dict[str, tuple[str, ...]] = {
    "expense_ratio": ("expense ratio", "total expense", "ter"),
    "exit_load": ("exit load", "redemption fee"),
    "min_sip": ("minimum sip", "min sip", "sip amount", "sip"),
    "lock_in": ("lock-in", "lock in", "lockin"),
    "riskometer": ("riskometer", "risk level"),
    "benchmark": ("benchmark", "index"),
    "statement_process": ("statement", "capital gains", "cas"),
    "amc_overview": ("registrar", "fund house", "amc", "aum", "scheme count"),
}

EVAL_FIELDS = [
    "trace_id",
    "created_at",
    "question_hash",
    "question_redacted",
    "intent",
    "scheme_slug",
    "fact_type",
    "needs_clarification",
    "candidate_count",
    "selected_count",
    "top_similarity",
    "evidence_passed",
    "no_answer_reason",
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000.0, 3)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def load_facts(settings: Settings) -> list[FactCard]:
    path = settings.path("chunks") / "facts.jsonl"
    if not path.exists():
        return []
    facts: list[FactCard] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                facts.append(FactCard.model_validate_json(line))
    return facts


def canonical_fact(
    facts: list[FactCard], plan: QueryPlan, *, verified_only: bool = False
) -> FactCard | None:
    candidates = [
        fact
        for fact in facts
        if fact.scheme_slug == plan.scheme_slug and fact.fact_type == plan.fact_type
    ]
    if verified_only:
        candidates = [fact for fact in candidates if fact.verified]
    else:
        verified = [fact for fact in candidates if fact.verified]
        if verified:
            candidates = verified
    return candidates[0] if candidates else None


def _load_vectors(settings: Settings) -> tuple[np.ndarray, dict[str, int]]:
    embeddings_path = settings.path("embeddings") / "embeddings.npy"
    map_path = settings.path("embeddings") / "chunk_id_map.json"
    if not embeddings_path.exists() or not map_path.exists():
        return np.zeros((0, 384), dtype=np.float32), {}
    vectors = np.load(embeddings_path)
    mapping = _read_json(map_path) or {}
    return vectors, dict(mapping.get("index", {}))


def _normalize_value(value: str) -> str:
    normalized = value.lower().replace("₹", "").replace(",", "")
    normalized = re.sub(r"\brs\.?\b", "", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _new_trace(
    guardrail: GuardrailResult,
    plan: QueryPlan | None = None,
    report: dict[str, Any] | None = None,
) -> RetrievalTrace:
    filters: dict[str, Any] = {}
    if plan:
        filters = {
            "citable": True,
            "scheme_slug": plan.scheme_slug,
            "fact_type": plan.fact_type,
        }
    return RetrievalTrace(
        trace_id="tr_" + _now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:10],
        created_at=_now(),
        question_hash=guardrail.question_hash,
        question_redacted=guardrail.redacted_question,
        pii_blocked=guardrail.pii_blocked,
        pii_types=guardrail.pii_types,
        intent=plan.intent if plan else guardrail.intent,
        scheme=plan.scheme if plan else None,
        scheme_slug=plan.scheme_slug if plan else None,
        fact_type=plan.fact_type if plan else None,
        fact_types=plan.fact_types if plan else [],
        needs_clarification=plan.needs_clarification if plan else False,
        clarification=plan.clarification if plan else None,
        filters=filters,
        embedding_model=(
            str(report.get("embedding_model")) if report and report.get("embedding_model") else None
        ),
        manifest_hash=(
            str(report.get("manifest_hash")) if report and report.get("manifest_hash") else None
        ),
        config_hash=(
            str(report.get("config_hash")) if report and report.get("config_hash") else None
        ),
        collection_name=(
            str(report.get("collection_name")) if report and report.get("collection_name") else None
        ),
    )


def _mark_no_answer(
    trace: RetrievalTrace,
    reason: str,
    *,
    detail: str | None = None,
    threshold: float | None = None,
) -> None:
    trace.no_answer_reason = reason
    gate: dict[str, Any] = {
        "passed": False,
        "reason": reason,
    }
    if detail:
        gate["detail"] = detail
    if threshold is not None:
        gate["threshold"] = threshold
    trace.evidence_gate = gate
    trace.selected = []
    trace.selected_hits = []


def _write_trace(trace: RetrievalTrace, settings: Settings) -> Path:
    out_dir = settings.path("retrieval")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{trace.trace_id}.json"
    path.write_text(trace.model_dump_json(indent=2), encoding="utf-8")
    _append_eval(trace, out_dir)
    return path


def _append_eval(trace: RetrievalTrace, out_dir: Path) -> None:
    path = out_dir / "retrieval_eval.csv"
    exists = path.exists()
    top_similarity = max((hit.similarity for hit in trace.candidates), default=None)
    row = {
        "trace_id": trace.trace_id,
        "created_at": trace.created_at.isoformat(),
        "question_hash": trace.question_hash,
        "question_redacted": trace.question_redacted,
        "intent": trace.intent,
        "scheme_slug": trace.scheme_slug or "",
        "fact_type": trace.fact_type or "",
        "needs_clarification": trace.needs_clarification,
        "candidate_count": len(trace.candidates),
        "selected_count": len(trace.selected),
        "top_similarity": "" if top_similarity is None else f"{top_similarity:.6f}",
        "evidence_passed": bool(trace.evidence_gate.get("passed", False)),
        "no_answer_reason": trace.no_answer_reason or "",
    }
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EVAL_FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def _persist(trace: RetrievalTrace, settings: Settings, start: float) -> RetrievalTrace:
    trace.timings_ms["total"] = _ms(start)
    _write_trace(trace, settings)
    return trace


def _merge_candidates(
    existing: list[dict[str, Any]], rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    merged = {str(candidate["chunk_id"]): candidate for candidate in existing}
    for row in rows:
        chunk_id = str(row["chunk_id"])
        current = merged.get(chunk_id)
        if current is None or float(row["similarity"]) > float(current["similarity"]):
            merged[chunk_id] = row
    return list(merged.values())


def _attempt_filters(plan: QueryPlan) -> list[dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    if plan.scheme_slug and plan.fact_type:
        attempts.append(
            {
                "citable": True,
                "scheme_slug": plan.scheme_slug,
                "fact_type": plan.fact_type,
            }
        )
    if plan.scheme_slug:
        attempts.append({"citable": True, "scheme_slug": plan.scheme_slug})
    if plan.fact_type:
        attempts.append({"citable": True, "fact_type": plan.fact_type})
    attempts.append({"citable": True})
    return attempts


def _apply_exact_fact_boost(
    candidates: list[dict[str, Any]],
    fact: FactCard | None,
    boost: float,
) -> None:
    for candidate in candidates:
        candidate["exact_fact_boost"] = 0.0
        candidate["score"] = float(candidate["similarity"])
    if not fact or not fact.verified or not fact.chunk_id:
        return
    for candidate in candidates:
        if str(candidate["chunk_id"]) == fact.chunk_id:
            candidate["exact_fact_boost"] = boost
            candidate["score"] = float(candidate["similarity"]) + boost


def _vector_for(
    candidate: dict[str, Any], vectors: np.ndarray, row_index: dict[str, int]
) -> np.ndarray | None:
    row = row_index.get(str(candidate["chunk_id"]))
    if row is None or row >= vectors.shape[0]:
        return None
    return vectors[row]


def _mmr(
    candidates: list[dict[str, Any]],
    vectors: np.ndarray,
    row_index: dict[str, int],
    *,
    k: int,
    lambda_: float,
) -> list[dict[str, Any]]:
    if not candidates or k <= 0:
        return []
    pool = sorted(candidates, key=lambda item: float(item["score"]), reverse=True)
    selected: list[dict[str, Any]] = []
    while pool and len(selected) < k:
        best: dict[str, Any] | None = None
        best_score = float("-inf")
        for candidate in pool:
            relevance = float(candidate["score"])
            diversity = 0.0
            candidate_vector = _vector_for(candidate, vectors, row_index)
            if selected and candidate_vector is not None:
                similarities: list[float] = []
                for chosen in selected:
                    chosen_vector = _vector_for(chosen, vectors, row_index)
                    if chosen_vector is not None:
                        similarities.append(float(np.dot(candidate_vector, chosen_vector)))
                if similarities:
                    diversity = max(similarities)
            mmr_score = lambda_ * relevance - (1.0 - lambda_) * diversity
            if mmr_score > best_score:
                best = candidate
                best_score = mmr_score
        if best is None:
            break
        selected.append(best)
        pool.remove(best)
    return selected


def _candidate_failure(
    candidate: dict[str, Any],
    plan: QueryPlan,
    fact: FactCard | None,
) -> str | None:
    metadata = dict(candidate.get("metadata") or {})
    text = str(candidate.get("document") or "")
    text_lower = text.lower()

    if metadata.get("citable") is not True:
        return "not_citable"
    if str(metadata.get("source_type", "")) not in OFFICIAL_SOURCE_TYPES:
        return "source_not_official"
    if plan.scheme_slug and metadata.get("scheme_slug") != plan.scheme_slug:
        return "scheme_mismatch"

    if plan.fact_type:
        actual_fact = str(metadata.get("fact_type", ""))
        if actual_fact != plan.fact_type:
            section = str(metadata.get("section", ""))
            hints = FACT_TEXT_HINTS.get(plan.fact_type, ())
            haystack = f"{section} {text_lower}".lower()
            if not any(hint in haystack for hint in hints):
                return "fact_type_mismatch"

    # A statement process is a multi-step description, not a short canonical
    # value. Multiple approved official pages can describe the same process,
    # so requiring the full canonical text to appear in every candidate would
    # reject the capital-gains statement page when the canonical card came
    # from the account-statement page.
    if (
        fact
        and fact.verified
        and fact.value_text
        and plan.fact_type != "statement_process"
    ):
        if _normalize_value(fact.value_text) not in _normalize_value(text):
            return "value_not_in_chunk"

    # Index names legitimately contain "Total Return(s) Index". That is a
    # benchmark label, not a performance claim, so benchmark facts are exempt
    # from the performance-content gate.
    if plan.fact_type != "benchmark" and PERFORMANCE_RE.search(text):
        return "performance_content"

    return None


def _evidence_gate(
    selected: list[dict[str, Any]],
    plan: QueryPlan,
    fact: FactCard | None,
    *,
    min_similarity: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidate_top_score = max(
        (float(item["similarity"]) for item in selected), default=0.0
    )
    checks: list[dict[str, Any]] = []
    passing: list[dict[str, Any]] = []
    for candidate in selected:
        failure = _candidate_failure(candidate, plan, fact)
        checks.append(
            {
                "chunk_id": str(candidate["chunk_id"]),
                "similarity": round(float(candidate["similarity"]), 6),
                "score": round(float(candidate["score"]), 6),
                "reason": failure or "passed",
            }
        )
        if failure is None:
            passing.append(candidate)

    # The threshold must be met by evidence that actually passed the gate.
    # A high-scoring candidate that fails a scheme/fact/citability check must
    # not rescue an otherwise unsupported answer.
    top_score = max((float(item["similarity"]) for item in passing), default=0.0)
    if not passing:
        return [], {
            "passed": False,
            "reason": "no_candidate_passed_gate",
            "top_score": round(top_score, 6),
            "candidate_top_score": round(candidate_top_score, 6),
            "threshold": min_similarity,
            "checks": checks,
        }
    if top_score < min_similarity:
        return [], {
            "passed": False,
            "reason": "top_score_below_threshold",
            "top_score": round(top_score, 6),
            "candidate_top_score": round(candidate_top_score, 6),
            "threshold": min_similarity,
            "checks": checks,
        }
    return passing, {
        "passed": True,
        "reason": "top_score_above_threshold",
        "top_score": round(top_score, 6),
        "candidate_top_score": round(candidate_top_score, 6),
        "threshold": min_similarity,
        "checks": checks,
    }


def _source_name(config: dict[str, Any]) -> str:
    return str(config.get("model", "sentence-transformers/all-MiniLM-L6-v2"))


def _retrieval_error_reason(exc: Exception) -> str:
    message = str(exc).lower()
    if "no citable" in message:
        return "no_citable_index"
    if "not built" in message:
        return "collection_missing"
    if "config hash" in message or "manifest hash" in message:
        return "stale_index"
    return "retrieval_error"


def retrieve(
    question: str,
    settings: Settings,
    *,
    model=None,
) -> RetrievalTrace:
    """Run guardrails, routing, retrieval, ranking, and the evidence gate."""
    start = time.perf_counter()
    timings: dict[str, float] = {}

    guard_start = time.perf_counter()
    guardrail = check_question(question, settings)
    timings["guardrails"] = _ms(guard_start)

    if not guardrail.allowed:
        trace = _new_trace(guardrail)
        trace.timings_ms = dict(timings)
        _mark_no_answer(trace, guardrail.reason or "blocked")
        return _persist(trace, settings, start)

    report = _read_json(settings.path("vector") / "collection_report.json")

    route_start = time.perf_counter()
    plan = route(guardrail.redacted_question)
    timings["routing"] = _ms(route_start)
    trace = _new_trace(guardrail, plan, report)

    if plan.needs_clarification:
        trace.timings_ms = dict(timings)
        _mark_no_answer(trace, "needs_clarification")
        return _persist(trace, settings, start)

    if report is None:
        trace.timings_ms = dict(timings)
        _mark_no_answer(trace, "vector_store_missing")
        return _persist(trace, settings, start)

    if report.get("config_hash") != settings.config_hash:
        trace.timings_ms = dict(timings)
        _mark_no_answer(trace, "stale_index")
        return _persist(trace, settings, start)

    if report.get("status") != "citable_ready" or int(report.get("collection_count", 0)) < 1:
        trace.timings_ms = dict(timings)
        _mark_no_answer(trace, "no_citable_index")
        return _persist(trace, settings, start)

    facts = load_facts(settings)
    fact = canonical_fact(facts, plan)
    if fact:
        if fact.conflict or fact.status == "conflict":
            trace.timings_ms = dict(timings)
            _mark_no_answer(trace, "conflicting_official_facts")
            return _persist(trace, settings, start)
        if fact.status == "missing":
            trace.timings_ms = dict(timings)
            _mark_no_answer(trace, "fact_missing")
            return _persist(trace, settings, start)

    retrieval_config = settings.data.get("retrieval", {})
    top_k = int(retrieval_config.get("top_k", 12))
    final_k = int(retrieval_config.get("final_k", 4))
    min_similarity = float(retrieval_config.get("min_similarity", 0.30))
    mmr_lambda = float(retrieval_config.get("mmr_lambda", 0.70))
    boost = float(retrieval_config.get("exact_fact_boost", 0.05))

    embed_start = time.perf_counter()
    try:
        query_vector = embed_query(guardrail.redacted_question, settings, model=model)
    except Exception as exc:  # noqa: BLE001 - fail closed
        timings["embedding"] = _ms(embed_start)
        trace.timings_ms = dict(timings)
        _mark_no_answer(trace, "embedding_error", detail=str(exc))
        return _persist(trace, settings, start)
    timings["embedding"] = _ms(embed_start)

    attempts = _attempt_filters(plan)
    trace.filter_attempts = attempts
    candidates: list[dict[str, Any]] = []
    retrieval_start = time.perf_counter()
    try:
        for filters in attempts:
            rows = query_citable(
                settings,
                query_vector,
                scheme_slug=filters.get("scheme_slug"),
                fact_type=filters.get("fact_type"),
                n_results=top_k,
            )
            candidates = _merge_candidates(candidates, rows)
            if len(candidates) >= 3:
                break
    except VectorStoreError as exc:
        timings["retrieval"] = _ms(retrieval_start)
        trace.timings_ms = dict(timings)
        _mark_no_answer(trace, _retrieval_error_reason(exc), detail=str(exc))
        return _persist(trace, settings, start)
    timings["retrieval"] = _ms(retrieval_start)

    if not candidates:
        trace.timings_ms = dict(timings)
        _mark_no_answer(trace, "no_candidates")
        return _persist(trace, settings, start)

    ranking_start = time.perf_counter()
    _apply_exact_fact_boost(candidates, fact, boost)
    vectors, row_index = _load_vectors(settings)
    selected = _mmr(
        candidates,
        vectors,
        row_index,
        k=final_k,
        lambda_=mmr_lambda,
    )
    trace.candidates = [
        SearchHit(
            chunk_id=str(candidate["chunk_id"]),
            text=str(candidate.get("document") or ""),
            metadata=dict(candidate.get("metadata") or {}),
            similarity=round(float(candidate["similarity"]), 6),
            score=round(float(candidate["score"]), 6),
            exact_fact_boost=round(float(candidate.get("exact_fact_boost", 0.0)), 6),
        )
        for candidate in candidates
    ]

    verified_fact = canonical_fact(facts, plan, verified_only=True) if fact else None
    passing, gate = _evidence_gate(
        selected,
        plan,
        verified_fact,
        min_similarity=min_similarity,
    )
    timings["ranking"] = _ms(ranking_start)
    trace.timings_ms = dict(timings)
    trace.evidence_gate = gate
    if not passing:
        trace.no_answer_reason = str(gate["reason"])
        trace.selected = []
        trace.selected_hits = []
        return _persist(trace, settings, start)

    trace.selected_hits = [
        SearchHit(
            chunk_id=str(candidate["chunk_id"]),
            text=str(candidate.get("document") or ""),
            metadata=dict(candidate.get("metadata") or {}),
            similarity=round(float(candidate["similarity"]), 6),
            score=round(float(candidate["score"]), 6),
            exact_fact_boost=round(float(candidate.get("exact_fact_boost", 0.0)), 6),
        )
        for candidate in passing
    ]
    trace.selected = [hit.chunk_id for hit in trace.selected_hits]
    trace.no_answer_reason = None
    trace.embedding_model = trace.embedding_model or _source_name(
        settings.data.get("embedding", {})
    )
    return _persist(trace, settings, start)
