"""Suite-level aggregation: per-side aggregates, drift rate, flag histogram,
and per-tag breakdown sorted by drift rate (F-MET suite summary)."""

from __future__ import annotations

from typing import Any

from modelbump.drift import FLAG_RULES, CaseResult
from modelbump.metrics import percentile


def _side_aggregate(results: list[CaseResult], side: str) -> dict[str, Any]:
    if not results:
        return {}
    stats = [getattr(r, side) for r in results]

    def mean_rate(attr: str) -> float | None:
        values = [getattr(s, attr) for s in stats if getattr(s, attr) is not None]
        if not values:
            return None
        return sum(values) / len(values)

    latencies = [s.p50_latency_ms for s in stats if s.p50_latency_ms is not None]
    costs = [s.avg_cost_usd for s in stats if s.avg_cost_usd is not None]
    sample_latencies = [
        sm.latency_ms for s in stats for sm in s.samples if sm.latency_ms is not None
    ]
    total_errors = sum(1 for s in stats for sm in s.samples if sm.error)
    total_samples = sum(len(s.samples) for s in stats) or 1
    total_refusals = sum(1 for s in stats for sm in s.samples if sm.refused)
    total_truncated = sum(1 for s in stats for sm in s.samples if sm.truncated)
    total_tokens_in = sum(sm.tokens_in or 0 for s in stats for sm in s.samples)
    total_tokens_out = sum(sm.tokens_out or 0 for s in stats for sm in s.samples)

    return {
        "cases": len(results),
        "samples": total_samples,
        "schema_cases": sum(1 for s in stats if s.schema_valid_rate is not None),
        "expected_cases": sum(1 for s in stats if s.expected_hit_rate is not None),
        "tool_cases": sum(1 for s in stats if s.expected_tool_hit_rate is not None),
        "error_rate": total_errors / total_samples,
        "refusal_rate": total_refusals / total_samples,
        "truncated_rate": total_truncated / total_samples,
        "schema_valid_rate": mean_rate("schema_valid_rate"),
        "strict_json_rate": mean_rate("strict_json_rate"),
        "expected_hit_rate": mean_rate("expected_hit_rate"),
        "expected_tool_hit_rate": mean_rate("expected_tool_hit_rate"),
        "tool_args_valid_rate": mean_rate("tool_args_valid_rate"),
        "avg_cost_usd": sum(costs) / len(costs) if costs else None,
        "total_cost_usd": sum(
            sm.cost_usd for s in stats for sm in s.samples if sm.cost_usd is not None
        )
        if any(sm.cost_usd is not None for s in stats for sm in s.samples)
        else None,
        "p50_latency_ms": percentile(sample_latencies, 0.5),
        "p95_latency_ms": percentile(sample_latencies, 0.95),
        "p50_case_latency_ms": percentile(latencies, 0.5),
        "avg_self_similarity": _mean(
            [s.self_similarity for s in stats if s.self_similarity is not None]
        ),
        "tokens_in": total_tokens_in,
        "tokens_out": total_tokens_out,
    }


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def summarise(
    results: list[CaseResult],
    *,
    judge_summary: dict[str, Any] | None = None,
    include_outputs: bool = True,
) -> dict[str, Any]:
    flagged = [r for r in results if r.drifted]
    total = len(results) or 1

    histogram: dict[str, int] = {flag: 0 for flag in FLAG_RULES}
    for result in results:
        for flag in result.flags:
            histogram[flag] = histogram.get(flag, 0) + 1

    worst = sorted(results, key=lambda r: -r.drift_score)[:5]

    summary: dict[str, Any] = {
        "total_cases": len(results),
        "flagged_cases": len(flagged),
        "drift_rate": len(flagged) / total,
        "mean_drift_score": _mean([r.drift_score for r in results]) or 0.0,
        "max_drift_score": max((r.drift_score for r in results), default=0.0),
        "flag_histogram": {k: v for k, v in histogram.items() if v},
        "flag_rules": FLAG_RULES,
        "a": _side_aggregate(results, "a"),
        "b": _side_aggregate(results, "b"),
        "worst_cases": [
            {
                "id": r.case.id,
                "tags": r.case.tags,
                "drift_score": r.drift_score,
                "flags": r.flags,
            }
            for r in worst
            if r.drift_score > 0
        ],
        "judge": judge_summary,
    }
    summary["tags"] = per_tag_summary(results)
    return summary


def per_tag_summary(results: list[CaseResult]) -> list[dict[str, Any]]:
    """Per-tag breakdown, sorted by drift rate descending."""
    tags: dict[str, list[CaseResult]] = {}
    for result in results:
        for tag in result.case.tags or ["(untagged)"]:
            tags.setdefault(str(tag), []).append(result)

    rows: list[dict[str, Any]] = []
    for tag, tagged in sorted(tags.items()):
        flagged = [r for r in tagged if r.drifted]
        histogram: dict[str, int] = {}
        for result in tagged:
            for flag in result.flags:
                histogram[flag] = histogram.get(flag, 0) + 1
        rows.append(
            {
                "tag": tag,
                "cases": len(tagged),
                "flagged_cases": len(flagged),
                "drift_rate": len(flagged) / (len(tagged) or 1),
                "mean_drift_score": _mean([r.drift_score for r in tagged]) or 0.0,
                "flag_histogram": histogram,
                "a": _side_aggregate(tagged, "a"),
                "b": _side_aggregate(tagged, "b"),
            }
        )
    rows.sort(key=lambda row: (-row["drift_rate"], -row["mean_drift_score"], row["tag"]))
    return rows


def tag_thresholds_map(config_tags: dict[str, dict[str, Any]], base) -> dict[str, Any]:
    out = {}
    for tag, overrides in config_tags.items():
        out[tag] = base.merged(overrides)
    return out
