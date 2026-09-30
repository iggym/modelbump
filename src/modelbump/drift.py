"""Drift flags and per-case results (F-MET, F-MET flags).

Every flag has one documented rule. Flags are *evidence*, not opinions: each is
derived from the cheap per-sample metrics, and the report shows the numbers that
triggered it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from modelbump.case import Case
from modelbump.metrics import (
    ModelCaseStats,
    aggregate_model_case,
    cross_similarity,
    drift_score,
    output_kind,
)
from modelbump.providers.base import Response

# Flag → one-line rule, surfaced in the report so the numbers are auditable.
FLAG_RULES: dict[str, str] = {
    "errors": "model B error rate > 0 while model A had none, or B's error rate exceeds A's by >5pp",
    "refusal": "refusal rate rose by >10pp from A to B (or B refuses where A never did)",
    "schema": "schema-valid rate fell below 100% on B, or dropped by any amount from A",
    "strict_json": "strict-JSON rate fell on B (output gained prose or code fences)",
    "expected": "expected-hit rate fell on B",
    "expected_tool": "expected tool was called less often on B",
    "tool_choice": "the set of tool names called differs between A and B",
    "tool_args": "tool arguments became invalid against the tool schema on B",
    "semantic": "free-text drift score >= --drift-threshold (default 0.35)",
    "length": "average output length changed by more than 60%",
    "language": "the detected output language changed between A and B",
    "latency": "p50 latency rose by more than 2x AND more than +500ms",
    "truncated": "finish_reason=length appears only in B",
}

# Flags that count toward the suite drift rate (they represent real change).
DRIFT_FLAGS = (
    "errors",
    "refusal",
    "schema",
    "strict_json",
    "expected",
    "expected_tool",
    "tool_choice",
    "tool_args",
    "semantic",
    "length",
    "language",
    "truncated",
)


@dataclass
class CaseResult:
    case: Case
    kind: str
    a: ModelCaseStats
    b: ModelCaseStats
    flags: list[str] = field(default_factory=list)
    flag_details: dict[str, str] = field(default_factory=dict)
    cross_similarity: float = 0.0
    noise_floor: float | None = None
    drift_score: float = 0.0
    judge: dict[str, Any] | None = None

    @property
    def id(self) -> str:
        return self.case.id

    @property
    def drifted(self) -> bool:
        return any(flag in DRIFT_FLAGS for flag in self.flags)

    def to_dict(self, include_outputs: bool = True, include_raw: bool = False) -> dict[str, Any]:
        return {
            "id": self.case.id,
            "tags": list(self.case.tags),
            "kind": self.kind,
            "flags": list(self.flags),
            "flag_details": dict(self.flag_details),
            "drift_score": self.drift_score,
            "cross_similarity": self.cross_similarity,
            "noise_floor": self.noise_floor,
            "judge": self.judge,
            "a": self.a.to_dict(include_outputs=include_outputs),
            "b": self.b.to_dict(include_outputs=include_outputs),
        }


def compute_case_result(
    case: Case,
    responses_a: list[Response],
    responses_b: list[Response],
    *,
    model_a: str,
    model_b: str,
    drift_threshold: float = 0.35,
    refusal_re: re.Pattern[str] | None = None,
) -> CaseResult:
    kind = _case_kind(case, responses_a, responses_b)
    stats_a = aggregate_model_case(case, responses_a, model_name=model_a, kind=kind, refusal_re=refusal_re)
    stats_b = aggregate_model_case(case, responses_b, model_name=model_b, kind=kind, refusal_re=refusal_re)

    cross_sim = cross_similarity(responses_a, responses_b, kind)
    noise = _noise_floor(stats_a.self_similarity, stats_b.self_similarity)
    score = drift_score(cross_sim, stats_a.self_similarity, stats_b.self_similarity)

    result = CaseResult(
        case=case,
        kind=kind,
        a=stats_a,
        b=stats_b,
        cross_similarity=cross_sim,
        noise_floor=noise,
        drift_score=score,
    )
    _apply_flags(result, drift_threshold=drift_threshold)
    return result


def _case_kind(case: Case, a: list[Response], b: list[Response]) -> str:
    kinds = [output_kind(case, r) for r in (a + b)]
    if "tool" in kinds:
        return "tool"
    if "json" in kinds:
        return "json"
    return "text"


def _noise_floor(self_a: float | None, self_b: float | None) -> float | None:
    candidates = [s for s in (self_a, self_b) if s is not None]
    return min(candidates) if candidates else None


def _rate_delta(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return b - a


def _apply_flags(result: CaseResult, *, drift_threshold: float) -> None:
    a, b = result.a, result.b
    flags: list[str] = []
    details: dict[str, str] = {}

    def add(flag: str, detail: str) -> None:
        if flag not in flags:
            flags.append(flag)
        details[flag] = detail

    # errors
    if b.error_rate > 0 and (a.error_rate == 0 or b.error_rate - a.error_rate > 0.05):
        add(
            "errors",
            f"B error rate {b.error_rate:.0%} vs A {a.error_rate:.0%}",
        )

    # refusal
    if b.refusal_rate - a.refusal_rate > 0.10 or (b.refusal_rate > 0 and a.refusal_rate == 0):
        add("refusal", f"B refuses {b.refusal_rate:.0%} vs A {a.refusal_rate:.0%}")

    # schema — relative only. The absolute floor is a threshold, not a drift
    # signal: a model that never satisfied the schema is not drifting from itself.
    if b.schema_valid_rate is not None and a.schema_valid_rate is not None:
        if b.schema_valid_rate < a.schema_valid_rate:
            add(
                "schema",
                f"schema-valid B {b.schema_valid_rate:.0%} vs A "
                f"{a.schema_valid_rate:.0%}",
            )

    # strict_json
    if b.strict_json_rate is not None and a.strict_json_rate is not None:
        if b.strict_json_rate < a.strict_json_rate:
            add(
                "strict_json",
                f"strict-JSON B {b.strict_json_rate:.0%} vs A {a.strict_json_rate:.0%}",
            )

    # expected
    if b.expected_hit_rate is not None and a.expected_hit_rate is not None:
        if b.expected_hit_rate < a.expected_hit_rate:
            add(
                "expected",
                f"expected-hit B {b.expected_hit_rate:.0%} vs A {a.expected_hit_rate:.0%}",
            )

    # expected_tool
    if b.expected_tool_hit_rate is not None and a.expected_tool_hit_rate is not None:
        if b.expected_tool_hit_rate < a.expected_tool_hit_rate:
            add(
                "expected_tool",
                f"expected-tool-hit B {b.expected_tool_hit_rate:.0%} vs A "
                f"{a.expected_tool_hit_rate:.0%}",
            )

    # tool_choice / tool_args
    names_a = _tool_names(a)
    names_b = _tool_names(b)
    if names_a != names_b:
        add("tool_choice", f"A called {sorted(names_a)}; B called {sorted(names_b)}")
    if b.tool_args_valid_rate is not None and a.tool_args_valid_rate is not None:
        if b.tool_args_valid_rate < a.tool_args_valid_rate:
            add(
                "tool_args",
                f"valid tool args B {b.tool_args_valid_rate:.0%} vs A {a.tool_args_valid_rate:.0%}",
            )

    # semantic — free-text cases only
    if result.kind == "text" and result.drift_score >= drift_threshold:
        add(
            "semantic",
            f"drift {result.drift_score:.2f} >= threshold {drift_threshold:.2f} "
            f"(cross-sim {result.cross_similarity:.2f}, noise "
            f"{result.noise_floor if result.noise_floor is not None else 'n/a'})",
        )

    # length
    if a.avg_chars and b.avg_chars:
        change = abs(b.avg_chars - a.avg_chars) / a.avg_chars
        if change > 0.60:
            add(
                "length",
                f"avg chars {a.avg_chars:.0f} → {b.avg_chars:.0f} ({change:+.0%})",
            )

    # language
    langs_a = set(a.languages) - {"unknown"}
    langs_b = set(b.languages) - {"unknown"}
    if langs_a and langs_b and not (langs_a & langs_b):
        add("language", f"A produced {sorted(langs_a)}; B produced {sorted(langs_b)}")

    # latency
    if a.p50_latency_ms and b.p50_latency_ms:
        ratio = b.p50_latency_ms / a.p50_latency_ms
        delta = b.p50_latency_ms - a.p50_latency_ms
        if ratio > 2.0 and delta > 500:
            add(
                "latency",
                f"p50 {a.p50_latency_ms:.0f}ms → {b.p50_latency_ms:.0f}ms "
                f"({ratio:.1f}x, +{delta:.0f}ms)",
            )

    # truncated
    if b.truncated_any and not a.truncated_any:
        add("truncated", "finish_reason=length appears only in B")

    result.flags = flags
    result.flag_details = details


def _tool_names(stats: ModelCaseStats) -> set[str]:
    names: set[str] = set()
    for sample in stats.samples:
        names.update(sample.tool_names)
    return names
