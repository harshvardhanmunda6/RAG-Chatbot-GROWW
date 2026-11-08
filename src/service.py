"""End-to-end answer service: retrieve, generate, validate, cite, persist."""

from __future__ import annotations

import re
import time

from src.citation_resolver import (
    resolve_citation,
    resolve_overview_citation,
    resolve_support_citation,
)
from src.config import Settings
from src.generator import generate_llm
from src.models import AnswerResponse, Citation, QueryPlan, RetrievalTrace, SearchHit
from src.prompt_builder import build_prompt
from src.providers import resolve_provider
from src.retriever import canonical_fact, load_facts, retrieve
from src.router import route
from src.tracing import append_answer_eval, utc_now, write_json, write_text
from src.validator import count_sentences, split_sentences, validate_answer

BADGE = {
    "amc": "AMC",
    "sebi": "SEBI",
    "amfi": "AMFI",
    "registrar": "Registrar",
}

TEMPLATES = {
    "expense_ratio": "The expense ratio of {scheme} is {value}.",
    "exit_load": "The exit load for {scheme} is {value}.",
    "min_sip": "The minimum SIP for {scheme} is {value}.",
    "lock_in": "{scheme} has a lock-in of {value}.",
    "riskometer": "The riskometer level for {scheme} is {value}.",
    "benchmark": "The benchmark for {scheme} is {value}.",
}


def _today() -> str:
    return utc_now().date().isoformat()


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000.0, 3)


def _source_badge(citation: Citation | None) -> str | None:
    return BADGE.get(citation.source_type) if citation else None


def _ensure_period(text: str) -> str:
    value = (text or "").strip()
    if not value:
        return value
    return value if value[-1] in ".!?" else value + "."


def _clean_chunk_text(text: str) -> str:
    parts = [part.strip() for part in (text or "").split("|")]
    while parts and re.match(
        r"^(?:scheme|amc|page|fact|section|steps?)\s*:", parts[0], re.IGNORECASE
    ):
        parts.pop(0)
    cleaned = " | ".join(part for part in parts if part).strip()
    return re.sub(
        r"^(?:expense ratio|exit load|minimum sip|lock-in|riskometer|"
        r"benchmark|statement process|amc overview)\s*:\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()


def _first_sentences(text: str, limit: int = 3) -> str:
    sentences = split_sentences(text)
    return " ".join(sentences[:limit]).strip()


def _extractive_answer(
    plan: QueryPlan, fact, evidence: list[SearchHit]
) -> tuple[str, str | None]:
    if not evidence:
        return "", None
    by_id = {hit.chunk_id: hit for hit in evidence}
    fact_chunk = fact.chunk_id if fact and fact.chunk_id in by_id else None
    chunk_id = fact_chunk or evidence[0].chunk_id
    hit = by_id[chunk_id]
    scheme = plan.scheme or str(hit.metadata.get("scheme") or "the scheme")
    value = (fact.value_text or "").strip() if fact and fact_chunk else ""

    if value and plan.fact_type in TEMPLATES:
        answer = TEMPLATES[plan.fact_type].format(scheme=scheme, value=value)
    elif value and plan.fact_type == "amc_overview":
        answer = f"{value.replace(';', ',')}."
    elif plan.fact_type == "statement_process" or not value:
        answer = _first_sentences(_clean_chunk_text(hit.text), 3)
    else:
        answer = _first_sentences(_clean_chunk_text(hit.text), 3)

    return _ensure_period(answer), chunk_id


def _refusal_text(intent: str, reason: str | None, has_citation: bool) -> tuple[str, str]:
    if intent == "pii":
        text = (
            "I can't accept PAN, Aadhaar, account, OTP, email, or phone details. "
            "Please ask a factual question without personal information."
        )
        if has_citation:
            text += " For official investor education, see the source below."
        return "refusal", text
    if intent == "advice":
        text = "I can only share factual scheme information, not investment advice."
        if has_citation:
            text += " For official investor education, see the source below."
        return "refusal", text
    if intent == "performance":
        text = "I can only share factual scheme information, not returns or performance."
        if has_citation:
            text += " For the official factsheet, see the source below."
        return "refusal", text
    if intent == "out_of_scope":
        text = (
            "I can only answer factual questions about the listed HDFC schemes "
            "or HDFC Mutual Fund."
        )
        if has_citation:
            text += " See the AMC overview in the source below."
        return "refusal", text
    if reason == "too_long":
        return "invalid", "Please keep the question under 500 characters."
    if reason == "empty_question":
        return "invalid", "Enter a question."
    text = "I don't have approved official evidence to answer that fact yet, so I won't guess."
    if has_citation:
        text += " See the AMC overview in the source below."
    return "no_answer", text


def _new_response(trace: RetrievalTrace) -> AnswerResponse:
    return AnswerResponse(
        trace_id=trace.trace_id,
        created_at=utc_now(),
        question_hash=trace.question_hash,
        question_redacted=trace.question_redacted,
        answer_type="no_answer",
        last_updated=_today(),
        retrieval_trace_id=trace.trace_id,
        intent=trace.intent,
        scheme=trace.scheme,
        scheme_slug=trace.scheme_slug,
        fact_type=trace.fact_type,
    )


def _persist(
    response: AnswerResponse,
    settings: Settings,
    *,
    prompt_text: str | None = None,
    raw: list[str] | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> None:
    out_dir = settings.path("answers")
    write_json(out_dir / f"{response.trace_id}.json", response)
    if prompt_text:
        write_text(out_dir / f"{response.trace_id}.prompt.txt", prompt_text)
        write_text(out_dir / "prompt.txt", prompt_text)
    if raw:
        payload = {"provider": provider, "model": model, "responses": raw}
        write_json(out_dir / f"{response.trace_id}.llm_raw.json", payload)
        write_json(out_dir / "llm_raw.json", payload)
    append_answer_eval(
        out_dir / "answers_eval.csv",
        {
            "trace_id": response.trace_id,
            "created_at": response.created_at.isoformat(),
            "question_hash": response.question_hash,
            "intent": response.intent,
            "answer_type": response.answer_type,
            "scheme_slug": response.scheme_slug or "",
            "fact_type": response.fact_type or "",
            "citation_source_id": response.citation.source_id if response.citation else "",
            "citation_url": response.citation.url if response.citation else "",
            "last_updated": response.last_updated,
            "fallback_used": response.fallback_used,
            "fallback_reason": response.fallback_reason or "",
            "provider": response.provider or "",
            "model": response.model or "",
            "validation_passed": response.validation.get("passed", False),
            "validation_reason": response.validation.get("reason", ""),
        },
    )


def _non_factual_response(
    trace: RetrievalTrace, settings: Settings, start: float
) -> AnswerResponse:
    response = _new_response(trace)
    if trace.needs_clarification:
        response.answer_type = "clarification"
        response.answer = trace.clarification
        response.validation = {
            "passed": True,
            "reason": "needs_clarification",
            "sentence_count": count_sentences(trace.clarification or ""),
            "checks": [],
        }
    else:
        citation = resolve_support_citation(trace.intent, settings)
        if citation is None and trace.intent not in {"invalid", "ambiguous"}:
            citation = resolve_overview_citation(settings)
        answer_type, answer = _refusal_text(
            trace.intent, trace.no_answer_reason, citation is not None
        )
        response.answer_type = answer_type
        response.answer = answer
        response.citation = citation
        response.source_badge = _source_badge(citation)
        response.last_updated = citation.last_updated if citation else _today()
        response.validation = {
            "passed": True,
            "reason": trace.no_answer_reason or answer_type,
            "sentence_count": count_sentences(answer),
            "checks": [],
        }
    response.timings_ms = dict(trace.timings_ms)
    response.timings_ms["total"] = _ms(start)
    _persist(response, settings)
    return response


def _fact_response(
    trace: RetrievalTrace,
    settings: Settings,
    *,
    answer: str,
    citation: Citation,
    validation,
    fallback_used: bool,
    fallback_reason: str | None,
    provider: str | None,
    model: str | None,
    prompt_hash: str | None,
    start: float,
) -> AnswerResponse:
    response = _new_response(trace)
    response.answer_type = "fact"
    response.answer = answer
    response.citation = citation
    response.source_badge = _source_badge(citation)
    response.last_updated = citation.last_updated
    response.fallback_used = fallback_used
    response.fallback_reason = fallback_reason
    response.provider = provider
    response.model = model
    response.prompt_hash = prompt_hash
    response.validation = validation.model_dump()
    response.timings_ms = dict(trace.timings_ms)
    response.timings_ms["total"] = _ms(start)
    return response


def answer_question(
    question: str,
    settings: Settings,
    *,
    provider=None,
    model: str | None = None,
    embed_model=None,
) -> AnswerResponse:
    """Answer one question with the fixed retrieval → generate → validate order."""
    start = time.perf_counter()
    trace = retrieve(question, settings, model=embed_model)

    if trace.no_answer_reason or not trace.evidence_gate.get("passed"):
        return _non_factual_response(trace, settings, start)

    plan = route(trace.question_redacted)
    prompt = build_prompt(trace.question_redacted, plan, trace.selected_hits, settings)
    prompt_text = prompt.text
    write_text(settings.path("answers") / f"{trace.trace_id}.prompt.txt", prompt_text)
    write_text(settings.path("answers") / "prompt.txt", prompt_text)

    active_provider = provider
    active_model = model
    resolve_error: str | None = None
    if active_provider is None:
        active_provider, resolved_model, resolve_error = resolve_provider(settings)
        active_model = active_model or resolved_model
    elif active_model is None:
        active_model = "injected-model"

    fallback_reason: str | None = None
    raw_responses: list[str] = []
    provider_name = getattr(active_provider, "name", None)
    llm_result = None

    if active_provider is None or not active_model:
        fallback_reason = resolve_error or "llm_unavailable"
    else:
        llm_result = generate_llm(
            prompt,
            settings,
            provider=active_provider,
            model=active_model,
        )
        raw_responses.extend(llm_result.raw_responses)
        if not llm_result.ok or llm_result.parsed is None:
            fallback_reason = llm_result.error or "llm_failed"
        elif llm_result.parsed.answer_type == "no_answer":
            response = _new_response(trace)
            response.answer_type = "no_answer"
            response.answer = _ensure_period(llm_result.parsed.answer)
            response.validation = {
                "passed": True,
                "reason": "llm_no_answer",
                "sentence_count": count_sentences(response.answer),
                "checks": [],
            }
            response.provider = llm_result.provider
            response.model = llm_result.model
            response.prompt_hash = prompt.prompt_hash
            response.timings_ms = dict(trace.timings_ms)
            response.timings_ms["total"] = _ms(start)
            _persist(
                response,
                settings,
                prompt_text=prompt_text,
                raw=raw_responses,
                provider=llm_result.provider,
                model=llm_result.model,
            )
            return response
        else:
            citation, cite_reason = resolve_citation(
                llm_result.parsed.citation_chunk_id, trace.selected_hits, settings, plan
            )
            validation = validate_answer(
                llm_result.parsed.answer, citation, trace.selected_hits, plan, settings
            )
            if not validation.passed:
                repair_result = generate_llm(
                    prompt,
                    settings,
                    provider=active_provider,
                    model=active_model,
                    repair_reason=validation.reason or "validation_failed",
                )
                raw_responses.extend(repair_result.raw_responses)
                if repair_result.ok and repair_result.parsed is not None:
                    repair_citation, repair_cite_reason = resolve_citation(
                        repair_result.parsed.citation_chunk_id,
                        trace.selected_hits,
                        settings,
                        plan,
                    )
                    repair_validation = validate_answer(
                        repair_result.parsed.answer,
                        repair_citation,
                        trace.selected_hits,
                        plan,
                        settings,
                    )
                    if repair_validation.passed and repair_citation is not None:
                        response = _fact_response(
                            trace,
                            settings,
                            answer=repair_result.parsed.answer,
                            citation=repair_citation,
                            validation=repair_validation,
                            fallback_used=False,
                            fallback_reason=None,
                            provider=repair_result.provider,
                            model=repair_result.model,
                            prompt_hash=prompt.prompt_hash,
                            start=start,
                        )
                        _persist(
                            response,
                            settings,
                            prompt_text=prompt_text,
                            raw=raw_responses,
                            provider=repair_result.provider,
                            model=repair_result.model,
                        )
                        return response
                    cite_reason = repair_cite_reason or cite_reason
                    validation = repair_validation
                fallback_reason = "validation_failed:" + (
                    validation.reason or cite_reason or "unknown"
                )
            elif citation is None:
                fallback_reason = "citation_rejected:" + (cite_reason or "unknown")
            else:
                response = _fact_response(
                    trace,
                    settings,
                    answer=llm_result.parsed.answer,
                    citation=citation,
                    validation=validation,
                    fallback_used=False,
                    fallback_reason=None,
                    provider=llm_result.provider,
                    model=llm_result.model,
                    prompt_hash=prompt.prompt_hash,
                    start=start,
                )
                _persist(
                    response,
                    settings,
                    prompt_text=prompt_text,
                    raw=raw_responses,
                    provider=llm_result.provider,
                    model=llm_result.model,
                )
                return response

    facts = load_facts(settings)
    fact = canonical_fact(facts, plan, verified_only=True)
    answer, chunk_id = _extractive_answer(plan, fact, trace.selected_hits)
    if not answer or not chunk_id:
        response = _non_factual_response(trace, settings, start)
        response.validation["reason"] = "extractive_unavailable"
        return response

    citation, cite_reason = resolve_citation(chunk_id, trace.selected_hits, settings, plan)
    validation = validate_answer(answer, citation, trace.selected_hits, plan, settings)
    if not validation.passed or citation is None:
        response = _non_factual_response(trace, settings, start)
        response.answer_type = "no_answer"
        response.answer = (
            "I found matching text, but its citation could not be verified, so I won't answer."
        )
        response.validation = validation.model_dump()
        response.validation["reason"] = (
            validation.reason or cite_reason or "citation_rejected"
        )
        response.fallback_used = True
        response.fallback_reason = fallback_reason or "citation_rejected"
        response.provider = provider_name
        response.model = active_model
        response.prompt_hash = prompt.prompt_hash
        _persist(
            response,
            settings,
            prompt_text=prompt_text,
            raw=raw_responses,
            provider=provider_name,
            model=active_model,
        )
        return response

    response = _fact_response(
        trace,
        settings,
        answer=answer,
        citation=citation,
        validation=validation,
        fallback_used=True,
        fallback_reason=fallback_reason or "extractive",
        provider=provider_name,
        model=active_model,
        prompt_hash=prompt.prompt_hash,
        start=start,
    )
    _persist(
        response,
        settings,
        prompt_text=prompt_text,
        raw=raw_responses,
        provider=provider_name,
        model=active_model,
    )
    return response
