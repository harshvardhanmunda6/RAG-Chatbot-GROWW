"""Question guardrails: length, PII, advice, performance, and scope."""

from __future__ import annotations

import hashlib
import re

from src.config import Settings
from src.models import GuardrailResult

PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "pan": re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", re.IGNORECASE),
    "aadhaar": re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", re.IGNORECASE),
    "phone": re.compile(r"(?<!\d)(?:\+91[\s-]?)?[6-9]\d{9}(?!\d)"),
    "otp": re.compile(
        r"\b(?:otp|one[\s-]?time\s+password|verification\s+code|code)\b"
        r"[^\n]{0,30}?\b\d{4,8}\b",
        re.IGNORECASE,
    ),
    "account": re.compile(
        r"\b(?:account|folio|client\s*id|ucc)\b"
        r"[^\n]{0,20}?\b(?:number|no\.?|id|is|:)\s*[:#-]?\s*"
        r"(?=[A-Z0-9/\-]*\d)([A-Z0-9][A-Z0-9/\-]{5,})\b"
        r"|"
        r"\b(?:account|folio|client\s*id|ucc)\b"
        r"[^\n]{0,20}?\b(?=[A-Z0-9/\-]*\d)([A-Z0-9][A-Z0-9/\-]{5,})\b",
        re.IGNORECASE,
    ),
    "credential": re.compile(
        r"\b(?:password|passcode|pin|credential|credentials)\b\s*[:=]?\s*\S+",
        re.IGNORECASE,
    ),
}

ADVICE_PATTERNS = [
    re.compile(r"\b(?:buy|sell|hold|invest|switch|stop)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:recommend|recommendation|best|better|compare|comparison|ranking|rank)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:portfolio|allocation|suitability|suitable|risk\s+profile)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bshould\s+i\b", re.IGNORECASE),
    re.compile(r"\bsafe\b", re.IGNORECASE),
    re.compile(r"\bwill\s+(?:it|this)\s+grow\b", re.IGNORECASE),
    re.compile(r"\bguaranteed\b", re.IGNORECASE),
    re.compile(r"\bwhich\s+fund\b", re.IGNORECASE),
    re.compile(r"\bgood\s+fund\b", re.IGNORECASE),
]

PERFORMANCE_PATTERNS = [
    re.compile(r"\breturns?\b", re.IGNORECASE),
    re.compile(r"\bcagr\b", re.IGNORECASE),
    re.compile(r"\babsolute\s+return\b", re.IGNORECASE),
    re.compile(r"\bannuali[sz]ed\b", re.IGNORECASE),
    re.compile(r"\bprofit\b", re.IGNORECASE),
    re.compile(r"\bloss\b", re.IGNORECASE),
    re.compile(r"\bwill\s+(?:it|this|my\s+investment)\s+double\b", re.IGNORECASE),
    re.compile(r"\bwill\s+(?:it|this)\s+give\b", re.IGNORECASE),
    re.compile(r"\bhow\s+much\s+will\s+i\s+(?:get|earn|make)\b", re.IGNORECASE),
    re.compile(r"\bnav\s+history\b", re.IGNORECASE),
    re.compile(r"\bperformance\b", re.IGNORECASE),
    re.compile(r"\b(?:alpha|beta|sharpe)\b", re.IGNORECASE),
    re.compile(r"\bcompare\s+(?:the\s+)?returns?\b", re.IGNORECASE),
]

OTHER_AMC_RE = re.compile(
    r"\b(?:sbi|icici|axis|kotak|nippon|uti|aditya\s+birla|tata|dsp|franklin|"
    r"quant|mirae|parag\s+parikh|canara|hsbc|bandhan|edelweiss|navi|"
    r"360\s+one)\b",
    re.IGNORECASE,
)

OUT_OF_SCOPE_PATTERNS = [
    re.compile(r"\breal[\s-]?time\s+nav\b", re.IGNORECASE),
    re.compile(r"\btoday'?s\s+nav\b", re.IGNORECASE),
    re.compile(r"\btax\s+filing\b", re.IGNORECASE),
    re.compile(r"\bfile\s+(?:my\s+)?tax\b", re.IGNORECASE),
    re.compile(r"\btax\s+advice\b", re.IGNORECASE),
    re.compile(r"\bhow\s+much\s+tax\b", re.IGNORECASE),
    re.compile(r"\bsav(?:e|ing)\s+tax\b", re.IGNORECASE),
    re.compile(r"\baccount\s+balance\b", re.IGNORECASE),
    re.compile(r"\bmy\s+(?:account|folio)\b", re.IGNORECASE),
    re.compile(r"\bredeem\s+my\b", re.IGNORECASE),
]

INJECTION_PATTERNS = [
    re.compile(r"\bignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?\b", re.IGNORECASE),
    re.compile(r"\bsystem\s+prompt\b", re.IGNORECASE),
    re.compile(r"\breveal\s+(?:the\s+)?prompt\b", re.IGNORECASE),
    re.compile(r"\bact\s+as\b", re.IGNORECASE),
    re.compile(r"\bjailbreak\b", re.IGNORECASE),
    re.compile(r"\bdeveloper\s+message\b", re.IGNORECASE),
]


def _hash(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _redact(text: str) -> tuple[str, list[str]]:
    redacted = text
    found: list[str] = []
    for pii_type, pattern in PII_PATTERNS.items():
        if pattern.search(redacted):
            found.append(pii_type)
            redacted = pattern.sub(f"[REDACTED_{pii_type.upper()}]", redacted)
    return redacted, found


def _matches(patterns: list[re.Pattern[str]], text: str) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def check_question(question: str, settings: Settings) -> GuardrailResult:
    """Apply the fixed pre-retrieval guardrail order."""
    raw = (question or "").strip()
    question_hash = _hash(raw)
    redacted, pii_types = _redact(raw)

    if not raw:
        return GuardrailResult(
            allowed=False,
            intent="invalid",
            reason="empty_question",
            question_hash=question_hash,
            redacted_question="",
        )

    max_chars = int(settings.data.get("app", {}).get("max_question_chars", 500))
    if len(raw) > max_chars:
        return GuardrailResult(
            allowed=False,
            intent="invalid",
            reason="too_long",
            question_hash=question_hash,
            redacted_question=redacted[:max_chars],
        )

    if pii_types:
        return GuardrailResult(
            allowed=False,
            intent="pii",
            reason="pii_detected",
            pii_blocked=True,
            pii_types=sorted(set(pii_types)),
            question_hash=question_hash,
            redacted_question=redacted,
        )

    if _matches(INJECTION_PATTERNS, raw):
        return GuardrailResult(
            allowed=False,
            intent="out_of_scope",
            reason="prompt_injection",
            prompt_injection=True,
            question_hash=question_hash,
            redacted_question=redacted,
        )

    if _matches(ADVICE_PATTERNS, raw):
        return GuardrailResult(
            allowed=False,
            intent="advice",
            reason="advice_request",
            question_hash=question_hash,
            redacted_question=redacted,
        )

    if _matches(PERFORMANCE_PATTERNS, raw):
        return GuardrailResult(
            allowed=False,
            intent="performance",
            reason="performance_request",
            question_hash=question_hash,
            redacted_question=redacted,
        )

    if OTHER_AMC_RE.search(raw) or _matches(OUT_OF_SCOPE_PATTERNS, raw):
        return GuardrailResult(
            allowed=False,
            intent="out_of_scope",
            reason="out_of_scope",
            question_hash=question_hash,
            redacted_question=redacted,
        )

    return GuardrailResult(
        allowed=True,
        intent="factual",
        question_hash=question_hash,
        redacted_question=redacted,
    )
