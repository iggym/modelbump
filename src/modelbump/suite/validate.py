"""Suite validation and statistics (F-SUITE-3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from modelbump.case import Case, validate_case
from modelbump.suite.loader import load


@dataclass
class SuiteIssue:
    case_id: str
    problems: list[str]

    def to_dict(self) -> dict[str, object]:
        return {"case_id": self.case_id, "problems": self.problems}


@dataclass
class ValidationReport:
    path: str
    total: int = 0
    issues: list[SuiteIssue] = field(default_factory=list)
    duplicate_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tag_counts: dict[str, int] = field(default_factory=dict)
    with_schema: int = 0
    with_tools: int = 0
    with_expected: int = 0
    multiturn: int = 0
    largest_input_chars: int = 0

    @property
    def ok(self) -> bool:
        return not self.issues and not self.duplicate_ids

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "ok": self.ok,
            "total": self.total,
            "duplicate_ids": self.duplicate_ids,
            "issues": [i.to_dict() for i in self.issues],
            "warnings": self.warnings,
            "stats": {
                "tag_counts": self.tag_counts,
                "with_schema": self.with_schema,
                "with_tools": self.with_tools,
                "with_expected": self.with_expected,
                "multiturn": self.multiturn,
                "largest_input_chars": self.largest_input_chars,
            },
        }


def validate_suite(path: str | Path) -> ValidationReport:
    p = Path(path)
    report = ValidationReport(path=str(p))
    result = load(p)
    report.warnings = list(result.warnings)
    report.total = len(result.cases)

    seen: dict[str, int] = {}
    for case in result.cases:
        seen[case.id] = seen.get(case.id, 0) + 1
        problems = validate_case(case)
        if problems:
            report.issues.append(SuiteIssue(case_id=case.id, problems=problems))
        for tag in case.tags:
            report.tag_counts[str(tag)] = report.tag_counts.get(str(tag), 0) + 1
        if case.schema:
            report.with_schema += 1
        if case.tools:
            report.with_tools += 1
        if case.expected is not None:
            report.with_expected += 1
        if case.is_multiturn:
            report.multiturn += 1
        report.largest_input_chars = max(report.largest_input_chars, case.input_chars)

    report.duplicate_ids = sorted(cid for cid, count in seen.items() if count > 1)
    return report


def suite_stats(cases: list[Case]) -> dict[str, object]:
    tag_counts: dict[str, int] = {}
    for case in cases:
        for tag in case.tags:
            tag_counts[str(tag)] = tag_counts.get(str(tag), 0) + 1
    return {
        "total": len(cases),
        "tag_counts": dict(sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "with_schema": sum(1 for c in cases if c.schema),
        "with_tools": sum(1 for c in cases if c.tools),
        "with_expected": sum(1 for c in cases if c.expected is not None),
        "multiturn": sum(1 for c in cases if c.is_multiturn),
        "total_input_chars": sum(c.input_chars for c in cases),
        "largest_input_chars": max((c.input_chars for c in cases), default=0),
    }
