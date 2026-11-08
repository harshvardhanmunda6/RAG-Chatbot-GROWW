"""Small artifact writers shared by the answer service."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

ANSWER_EVAL_FIELDS = [
    "trace_id",
    "created_at",
    "question_hash",
    "intent",
    "answer_type",
    "scheme_slug",
    "fact_type",
    "citation_source_id",
    "citation_url",
    "last_updated",
    "fallback_used",
    "fallback_reason",
    "provider",
    "model",
    "validation_passed",
    "validation_reason",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def write_json(path: Path, payload: BaseModel | dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, BaseModel):
        text = payload.model_dump_json(indent=2)
    else:
        text = json.dumps(payload, indent=2, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def append_answer_eval(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    cleaned = {field: row.get(field, "") for field in ANSWER_EVAL_FIELDS}
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=ANSWER_EVAL_FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow(cleaned)
