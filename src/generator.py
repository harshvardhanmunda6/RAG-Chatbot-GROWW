"""Provider calls, strict JSON parsing, and one JSON repair attempt."""

from __future__ import annotations

import json
import re
import time
from typing import Any

from src.config import Settings
from src.models import LLMAnswer, LLMResult
from src.prompt_builder import PromptBundle
from src.providers.base import (
    MissingAPIKeyError,
    ModelValidationError,
    ProviderError,
)
from src.providers import resolve_provider

_VALIDATED_MODELS: set[tuple[str, str]] = set()

JSON_REPAIR_INSTRUCTION = (
    "Your previous response was not valid JSON. Return only one JSON object with "
    'keys "answer_type", "answer", "citation_chunk_id", and "facts_used". '
    "Do not use markdown fences or add commentary."
)


def ensure_model(provider, model: str) -> None:
    key = (str(getattr(provider, "name", "provider")), model)
    if key in _VALIDATED_MODELS:
        return
    provider.validate_model(model)
    _VALIDATED_MODELS.add(key)


def extract_json_object(raw: str) -> dict[str, Any]:
    """Parse a JSON object, tolerating code fences and surrounding prose."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty response")
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("no JSON object found")
        text = text[start : end + 1]
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("JSON response is not an object")
    return payload


def _call_provider(provider, prompt: PromptBundle, settings: Settings, model: str, user: str) -> str:
    config = settings.data.get("llm", {})
    return provider.generate(
        prompt.system,
        user,
        model=model,
        temperature=float(config.get("temperature", 0.1)),
        max_tokens=int(config.get("max_tokens", 220)),
        timeout_s=float(config.get("timeout_s", 30)),
    )


def _result(
    *,
    ok: bool,
    provider,
    model: str | None,
    raw: list[str],
    parsed: LLMAnswer | None = None,
    error: str | None = None,
    start: float,
) -> LLMResult:
    return LLMResult(
        ok=ok,
        provider=getattr(provider, "name", None),
        model=model,
        parsed=parsed,
        raw_responses=raw,
        error=error,
        attempts=len(raw),
        latency_ms=round((time.perf_counter() - start) * 1000.0, 3),
    )


def generate_llm(
    prompt: PromptBundle,
    settings: Settings,
    *,
    provider=None,
    model: str | None = None,
    repair_reason: str | None = None,
) -> LLMResult:
    """Call the provider and return a parsed answer or a typed failure."""
    start = time.perf_counter()
    resolved_error: str | None = None
    if provider is None:
        provider, resolved_model, resolved_error = resolve_provider(settings)
        model = model or resolved_model
    if provider is None:
        return LLMResult(
            ok=False,
            model=model,
            error=resolved_error or "llm_unavailable",
            attempts=0,
            latency_ms=round((time.perf_counter() - start) * 1000.0, 3),
        )
    if not model:
        return LLMResult(
            ok=False,
            provider=getattr(provider, "name", None),
            error="missing_model",
            attempts=0,
            latency_ms=round((time.perf_counter() - start) * 1000.0, 3),
        )

    try:
        ensure_model(provider, model)
    except (MissingAPIKeyError, ModelValidationError, ProviderError) as exc:
        return LLMResult(
            ok=False,
            provider=getattr(provider, "name", None),
            model=model,
            error=f"model_invalid:{exc}",
            attempts=0,
            latency_ms=round((time.perf_counter() - start) * 1000.0, 3),
        )

    user = prompt.user
    if repair_reason:
        user += (
            "\n\nYOUR PREVIOUS ANSWER WAS REJECTED: "
            f"{repair_reason}. Return a corrected JSON object only."
        )

    raw_responses: list[str] = []
    try:
        raw = _call_provider(provider, prompt, settings, model, user)
        raw_responses.append(raw)
        try:
            parsed = LLMAnswer.model_validate(extract_json_object(raw))
            return _result(
                ok=True,
                provider=provider,
                model=model,
                raw=raw_responses,
                parsed=parsed,
                start=start,
            )
        except (ValueError, TypeError):
            repair_user = f"{user}\n\n{JSON_REPAIR_INSTRUCTION}"
            raw_repair = _call_provider(provider, prompt, settings, model, repair_user)
            raw_responses.append(raw_repair)
            try:
                parsed = LLMAnswer.model_validate(extract_json_object(raw_repair))
            except (ValueError, TypeError):
                return _result(
                    ok=False,
                    provider=provider,
                    model=model,
                    raw=raw_responses,
                    error="invalid_json",
                    start=start,
                )
            return _result(
                ok=True,
                provider=provider,
                model=model,
                raw=raw_responses,
                parsed=parsed,
                start=start,
            )
    except (MissingAPIKeyError, ModelValidationError, ProviderError) as exc:
        return _result(
            ok=False,
            provider=provider,
            model=model,
            raw=raw_responses,
            error=f"provider_error:{exc}",
            start=start,
        )
    except Exception as exc:  # noqa: BLE001 - fail closed to extractive mode
        return _result(
            ok=False,
            provider=provider,
            model=model,
            raw=raw_responses,
            error=f"provider_error:{exc}",
            start=start,
        )
