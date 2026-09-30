"""PII redaction for imported traces (F-SUITE-4).

Regex-based and deliberately conservative. It is a *safety net*, not a
compliance boundary — documented as such in the docs, and it never claims to be
exhaustive. Every pattern is listed here so users can reason about exactly what
gets scrubbed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Each entry: (label, compiled pattern, replacement)
_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "email",
        re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
        "[REDACTED:email]",
    ),
    (
        "credit_card",
        # 13-19 digits in groups of 4 separated by space/dash, with Luhn check applied below.
        re.compile(r"\b(?:\d[ \-]?){12,18}\d\b"),
        "[REDACTED:card]",
    ),
    (
        "iban",
        # Country code + check digits, then groups of alphanumerics that may be
        # space-separated (the common written form).
        re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{2,4}){3,8}\b"),
        "[REDACTED:iban]",
    ),
    (
        "us_ssn",
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "[REDACTED:ssn]",
    ),
    (
        "phone_us",
        re.compile(
            r"(?<!\d)(?:\+?1[\s.\-]?)?(?:\(\d{3}\)|\d{3})[\s.\-]?\d{3}[\s.\-]?\d{4}(?!\d)"
        ),
        "[REDACTED:phone]",
    ),
    (
        "phone_intl",
        re.compile(r"(?<!\d)\+\d{1,3}[\s.\-]?\d{2,4}[\s.\-]?\d{3,4}[\s.\-]?\d{3,4}(?!\d)"),
        "[REDACTED:phone]",
    ),
    (
        "api_key_like",
        re.compile(
            r"\b(?:sk|pk|rk|ghp|gho|ghs|xox[baprs])[-_][A-Za-z0-9\-_]{16,}\b"
        ),
        "[REDACTED:secret]",
    ),
]

REDACTION_RULES = [
    {"label": label, "pattern": pattern.pattern, "replacement": replacement}
    for label, pattern, replacement in _PATTERNS
]


@dataclass
class RedactionReport:
    counts: dict[str, int]

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def to_dict(self) -> dict[str, object]:
        return {"total": self.total, "by_type": dict(self.counts)}


def _luhn(number: str) -> bool:
    digits = [int(d) for d in number if d.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    total = 0
    for i, digit in enumerate(reversed(digits)):
        if i % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def redact(text: str, report: RedactionReport | None = None) -> str:
    """Scrub PII from ``text``. Returns the redacted string."""
    if not isinstance(text, str) or not text:
        return text
    for label, pattern, replacement in _PATTERNS:
        if label == "credit_card":

            def _card(
                match: re.Match[str], _label: str = label, _rep: str = replacement
            ) -> str:
                if _luhn(match.group(0)):
                    if report is not None:
                        report.counts[_label] = report.counts.get(_label, 0) + 1
                    return _rep
                return match.group(0)

            text = pattern.sub(_card, text)
        else:

            def _sub(match: re.Match[str], _label: str = label, _rep: str = replacement) -> str:
                if report is not None:
                    report.counts[_label] = report.counts.get(_label, 0) + 1
                return _rep

            text = pattern.sub(_sub, text)
    return text


def redact_value(value: object, report: RedactionReport | None = None) -> object:
    """Recursively redact strings inside nested JSON-like structures."""
    if isinstance(value, str):
        return redact(value, report)
    if isinstance(value, list):
        return [redact_value(v, report) for v in value]
    if isinstance(value, dict):
        return {k: redact_value(v, report) for k, v in value.items()}
    return value


def contains_pii(text: str) -> bool:
    """True when any redaction rule matches (used by tests)."""
    if not isinstance(text, str):
        return False
    for label, pattern, _ in _PATTERNS:
        for match in pattern.finditer(text):
            if label == "credit_card" and not _luhn(match.group(0)):
                continue
            return True
    return False
