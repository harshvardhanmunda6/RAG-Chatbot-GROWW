"""Gold-set evaluation for factual answers, refusals, and guardrails.

The evaluator runs the same public service path used by the CLI and UI. It
persists only redacted questions. A synthetic PII marker is injected at runtime
for the PII case so the marker itself is never written to the gold set or
evaluation results.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import Settings
from src.embedder import load_model
from src.models import AnswerResponse
from src.service import answer_question
from src.source_registry import is_url_allowed
from src.validator import count_sentences

PII_TEST_VALUES = {
    "{{PII_TEST_PAN}}": "ABCDE1234F",
    "{{PII_TEST_AADHAAR}}": "1234 5678 9012",
    "{{PII_TEST_EMAIL}}": "demo.user@example.com",
    "{{PII_TEST_PHONE}}": "9876543210",
    "{{PII_TEST_ACCOUNT}}": "123456789012",
    "{{PII_TEST_OTP}}": "482913",
}
PII_PLACEHOLDER = "{{PII_TEST_PAN}}"
OFFICIAL_SOURCE_TYPES = {"amc", "sebi", "amfi", "registrar"}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _normalize(value: str) -> str:
    text = value.lower()
    text = text.replace("₹", "").replace(",", "")
    text = re.sub(r"\brs\.?\b", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _contains_any(answer: str, values: list[str]) -> bool:
    normalized = _normalize(answer)
    return any(_normalize(value) in normalized for value in values if value)


def _contains_all(answer: str, values: list[str]) -> bool:
    normalized = _normalize(answer)
    return all(_normalize(value) in normalized for value in values if value)


def _is_official_citation(response: AnswerResponse, settings: Settings) -> bool:
    citation = response.citation
    if citation is None:
        return False
    allowed_types = set(
        settings.sources.get(
            "allowed_citation_types", ["amc", "sebi", "amfi", "registrar"]
        )
    )
    return (
        citation.source_type in OFFICIAL_SOURCE_TYPES
        and citation.source_type in allowed_types
        and citation.url.startswith("https://")
        and is_url_allowed(citation.url, settings.sources.get("allowed_domains", []))
    )


def _check_row(
    row: dict[str, Any],
    response: AnswerResponse,
    settings: Settings,
    *,
    injected_pii: list[str],
) -> dict[str, Any]:
    expected = row.get("expect", {})
    checks: dict[str, bool] = {}

    checks["answer_type"] = response.answer_type == expected.get("answer_type")
    if expected.get("intent"):
        checks["intent"] = response.intent == expected["intent"]
    if expected.get("scheme_slug"):
        checks["scheme"] = response.scheme_slug == expected["scheme_slug"]
    if expected.get("fact_type"):
        checks["fact_type"] = response.fact_type == expected["fact_type"]

    answer = response.answer or ""
    if expected.get("value_any"):
        checks["expected_value"] = _contains_any(answer, list(expected["value_any"]))
    if expected.get("value_all"):
        checks["expected_value"] = _contains_all(answer, list(expected["value_all"]))

    validate_answer = expected.get("validation_passed")
    if validate_answer is not None:
        checks["validation"] = bool(response.validation.get("passed")) is validate_answer

    citation_required = expected.get("citation")
    if citation_required is True:
        checks["one_official_citation"] = _is_official_citation(response, settings)
    elif citation_required is False:
        checks["citation_absent"] = response.citation is None

    if response.answer_type in {"fact", "refusal"}:
        checks["sentence_limit"] = count_sentences(answer) <= 3
    if response.answer_type == "fact":
        checks["dated"] = bool(
            re.fullmatch(r"\d{4}-\d{2}-\d{2}", response.last_updated or "")
        )

    if expected.get("pii_blocked"):
        checks["pii_blocked"] = response.intent == "pii"
        serialized = json.dumps(response.model_dump(mode="json"), ensure_ascii=False)
        checks["pii_absent"] = bool(injected_pii) and all(
            value not in serialized for value in injected_pii
        )

    passed = bool(checks) and all(checks.values())
    return {"checks": checks, "passed": passed}


def _result_row(
    row: dict[str, Any],
    response: AnswerResponse,
    check: dict[str, Any],
    *,
    latency_ms: float,
) -> dict[str, Any]:
    expected = row.get("expect", {})
    citation = response.citation
    return {
        "id": row.get("id"),
        "trace_id": response.trace_id,
        "question_redacted": response.question_redacted,
        "expected_type": expected.get("answer_type"),
        "actual_type": response.answer_type,
        "expected_scheme": expected.get("scheme_slug") or "",
        "actual_scheme": response.scheme_slug or "",
        "expected_fact": expected.get("fact_type") or "",
        "actual_fact": response.fact_type or "",
        "intent": response.intent,
        "answer": response.answer or "",
        "citation_source_id": citation.source_id if citation else "",
        "citation_url": citation.url if citation else "",
        "last_updated": response.last_updated,
        "validation_passed": bool(response.validation.get("passed")),
        "validation_reason": response.validation.get("reason") or "",
        "fallback_used": response.fallback_used,
        "checks": check["checks"],
        "passed": check["passed"],
        "latency_ms": round(latency_ms, 3),
    }


def _metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    passed = sum(1 for row in results if row["passed"])
    facts = [row for row in results if row["expected_type"] == "fact"]
    refusals = [row for row in results if row["expected_type"] == "refusal"]
    pii = [row for row in results if "pii_blocked" in row["checks"]]

    def rate(rows: list[dict[str, Any]], key: str) -> float | None:
        if not rows:
            return None
        return round(sum(1 for row in rows if row["checks"].get(key)) / len(rows), 4)

    def passed_rate(rows: list[dict[str, Any]]) -> float | None:
        if not rows:
            return None
        return round(sum(1 for row in rows if row["passed"]) / len(rows), 4)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gold_questions": total,
        "results": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "fact_questions": len(facts),
        "fact_pass_rate": passed_rate(facts),
        "refusal_questions": len(refusals),
        "refusal_pass_rate": passed_rate(refusals),
        "pii_cases": len(pii),
        "pii_blocked_rate": rate(pii, "pii_blocked"),
        "pii_absent_rate": rate(pii, "pii_absent"),
        "citation_rate": rate(results, "one_official_citation"),
        "sentence_limit_rate": rate(results, "sentence_limit"),
        "validation_rate": rate(facts, "validation"),
        "fallback_answers": sum(1 for row in results if row["fallback_used"]),
        "avg_latency_ms": round(
            sum(float(row["latency_ms"]) for row in results) / total, 3
        )
        if total
        else 0.0,
    }


def run_evaluation(
    settings: Settings,
    *,
    gold_path: Path | None = None,
    limit: int | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run the gold set, write artifacts, and return metrics plus results."""
    eval_dir = settings.path("evaluation")
    gold_path = gold_path or eval_dir / "gold_set.jsonl"
    rows = _read_jsonl(gold_path)
    if not rows:
        raise ValueError(f"Gold set not found or empty: {gold_path}")
    if limit is not None:
        rows = rows[: max(0, limit)]

    model = load_model(settings)
    results: list[dict[str, Any]] = []
    provider_name: str | None = None
    model_name: str | None = None

    for row in rows:
        question = str(row.get("question", ""))
        injected_pii: list[str] = []
        for placeholder, synthetic_value in PII_TEST_VALUES.items():
            if placeholder in question:
                injected_pii.append(synthetic_value)
                question = question.replace(placeholder, synthetic_value)

        start = time.perf_counter()
        response = answer_question(question, settings, embed_model=model)
        latency_ms = (time.perf_counter() - start) * 1000.0
        provider_name = response.provider or provider_name
        model_name = response.model or model_name
        check = _check_row(
            row, response, settings, injected_pii=injected_pii
        )
        results.append(
            _result_row(row, response, check, latency_ms=latency_ms)
        )

    metrics = _metrics(results)
    metrics["provider"] = provider_name or ""
    metrics["model"] = model_name or ""
    metrics["gold_set"] = str(gold_path)

    _write_jsonl(eval_dir / "results.jsonl", results)
    _write_json(eval_dir / "metrics.json", metrics)
    report = _render_report(metrics, results)
    (eval_dir / "eval_report.md").write_text(report, encoding="utf-8")
    (settings.root / "eval_report.md").write_text(report, encoding="utf-8")
    return metrics, results


def _render_report(metrics: dict[str, Any], results: list[dict[str, Any]]) -> str:
    lines = [
        "# Evaluation report",
        "",
        f"Generated: {metrics['generated_at']}",
        f"Provider/model: {metrics.get('provider') or 'extractive'} / "
        f"{metrics.get('model') or 'n/a'}",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---|",
    ]
    for key in [
        "gold_questions",
        "passed",
        "failed",
        "pass_rate",
        "fact_pass_rate",
        "refusal_pass_rate",
        "pii_blocked_rate",
        "pii_absent_rate",
        "citation_rate",
        "sentence_limit_rate",
        "validation_rate",
        "fallback_answers",
        "avg_latency_ms",
    ]:
        lines.append(f"| {key} | {metrics.get(key)} |")
    lines += [
        "",
        "## Results",
        "",
        "| ID | Expected | Actual | Passed | Citation |",
        "|---|---|---|---|---|",
    ]
    for row in results:
        lines.append(
            f"| {row['id']} | {row['expected_type']} | {row['actual_type']} | "
            f"{'yes' if row['passed'] else 'no'} | {row['citation_url'] or '—'} |"
        )
    failures = [row for row in results if not row["passed"]]
    if failures:
        lines += ["", "## Failed checks", ""]
        for row in failures:
            failed = [key for key, passed in row["checks"].items() if not passed]
            lines.append(
                f"- `{row['id']}` ({row.get('trace_id', 'no trace')}): "
                f"{', '.join(failed)}"
            )
    lines.append("")
    return "\n".join(lines)
