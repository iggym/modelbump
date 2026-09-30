"""Thresholds, verdicts, and exit-code policy (F-THR-1, F-THR-2, B3).

Every threshold has a documented meaning and a default. Per-tag overrides let a
team say "legal cases must not drift at all" without loosening everything else.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any

from modelbump.config import Config


@dataclass
class Thresholds:
    max_drift_rate: float = 0.25
    """Fraction of cases allowed to carry a drift flag."""

    min_schema_valid_rate: float = 0.98
    """Absolute floor for schema-valid rate on model B."""

    max_schema_valid_drop: float = 0.02
    """Allowed absolute drop in schema-valid rate from A to B."""

    max_expected_hit_drop: float = 0.05
    """Allowed absolute drop in expected-hit rate from A to B."""

    max_refusal_increase: float = 0.05
    """Allowed absolute increase in refusal rate from A to B."""

    max_error_rate: float = 0.02
    """Absolute error-rate ceiling on model B."""

    max_cost_increase: float = 1.0
    """Allowed relative cost increase (1.0 == up to 2x)."""

    max_latency_increase: float = 1.0
    """Allowed relative p50 latency increase (1.0 == up to 2x)."""

    max_judge_prefer_a_rate: float = 0.6
    """Fraction of judged cases that may prefer A before we call it a regression."""

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def merged(self, overrides: dict[str, Any] | None) -> Thresholds:
        if not overrides:
            return self
        known = {f.name for f in fields(self)}
        unknown = set(overrides) - known
        if unknown:
            raise ValueError(
                f"unknown threshold key(s): {', '.join(sorted(unknown))}. "
                f"Valid keys: {', '.join(sorted(known))}"
            )
        data = self.to_dict()
        data.update(overrides)
        return Thresholds(**data)


@dataclass
class Violation:
    rule: str
    message: str
    observed: float | None = None
    limit: float | None = None
    scope: str = "suite"
    severity: str = "violation"  # "violation" | "warning"

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "message": self.message,
            "observed": self.observed,
            "limit": self.limit,
            "scope": self.scope,
            "severity": self.severity,
        }


@dataclass
class Verdict:
    passed: bool
    violations: list[Violation] = field(default_factory=list)
    warnings: list[Violation] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pass": self.passed,
            "violations": [v.to_dict() for v in self.violations],
            "warnings": [v.to_dict() for v in self.warnings],
        }


def thresholds_from_config(config: Config, overrides: dict[str, Any] | None = None) -> Thresholds:
    base = Thresholds().merged(config.thresholds)
    if overrides:
        base = base.merged(overrides)
    return base


def thresholds_for_tag(base: Thresholds, config: Config, tag: str) -> Thresholds:
    override = config.tag_thresholds.get(tag)
    if not override:
        return base
    return base.merged(override)


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def evaluate(
    summary: dict[str, Any],
    thresholds: Thresholds,
    tag_summaries: list[dict[str, Any]] | None = None,
    tag_thresholds: dict[str, Thresholds] | None = None,
) -> Verdict:
    """Turn a suite summary into a pass/fail verdict with explicit numbers."""
    violations: list[Violation] = []
    warnings: list[Violation] = []

    a = summary.get("a", {})
    b = summary.get("b", {})

    def compare(
        rule: str,
        observed: float,
        limit: float,
        message: str,
        scope: str = "suite",
        warn_only: bool = False,
    ) -> None:
        if observed > limit:
            v = Violation(
                rule=rule,
                message=message,
                observed=observed,
                limit=limit,
                scope=scope,
                severity="warning" if warn_only else "violation",
            )
            (warnings if warn_only else violations).append(v)

    drift_rate = float(summary.get("drift_rate", 0.0))
    compare(
        "max_drift_rate",
        drift_rate,
        thresholds.max_drift_rate,
        f"drift rate {_pct(drift_rate)} exceeds max {_pct(thresholds.max_drift_rate)} "
        f"({summary.get('flagged_cases', 0)}/{summary.get('total_cases', 0)} cases flagged)",
    )

    schema_b = float(b.get("schema_valid_rate", 1.0)) if b.get("schema_cases") else 1.0
    if b.get("schema_cases"):
        compare(
            "min_schema_valid_rate",
            -schema_b,
            -thresholds.min_schema_valid_rate,
            f"model B schema-valid rate {_pct(schema_b)} is below floor "
            f"{_pct(thresholds.min_schema_valid_rate)}",
        )
        drop = float(a.get("schema_valid_rate", 1.0)) - schema_b
        compare(
            "max_schema_valid_drop",
            drop,
            thresholds.max_schema_valid_drop,
            f"schema-valid rate dropped {_pct(drop)} (A {_pct(a.get('schema_valid_rate', 1.0))} "
            f"→ B {_pct(schema_b)}), max drop {_pct(thresholds.max_schema_valid_drop)}",
        )

    if summary.get("expected_cases"):
        drop = float(a.get("expected_hit_rate", 1.0)) - float(b.get("expected_hit_rate", 1.0))
        compare(
            "max_expected_hit_drop",
            drop,
            thresholds.max_expected_hit_drop,
            f"expected-hit rate dropped {_pct(drop)} (A {_pct(a.get('expected_hit_rate', 1.0))} "
            f"→ B {_pct(b.get('expected_hit_rate', 1.0))}), max drop "
            f"{_pct(thresholds.max_expected_hit_drop)}",
        )

    refusal_increase = float(b.get("refusal_rate", 0.0)) - float(a.get("refusal_rate", 0.0))
    compare(
        "max_refusal_increase",
        refusal_increase,
        thresholds.max_refusal_increase,
        f"refusal rate increased {_pct(refusal_increase)} (A {_pct(a.get('refusal_rate', 0.0))} "
        f"→ B {_pct(b.get('refusal_rate', 0.0))}), max increase "
        f"{_pct(thresholds.max_refusal_increase)}",
    )

    error_rate = float(b.get("error_rate", 0.0))
    compare(
        "max_error_rate",
        error_rate,
        thresholds.max_error_rate,
        f"model B error rate {_pct(error_rate)} exceeds max {_pct(thresholds.max_error_rate)}",
    )

    if a.get("avg_cost_usd") is not None and b.get("avg_cost_usd") is not None:
        a_cost = float(a["avg_cost_usd"])
        b_cost = float(b["avg_cost_usd"])
        if a_cost > 0:
            increase = (b_cost - a_cost) / a_cost
            compare(
                "max_cost_increase",
                increase,
                thresholds.max_cost_increase,
                f"average cost increased {_pct(increase)} (A ${a_cost:.4f} → B ${b_cost:.4f}), "
                f"max increase {_pct(thresholds.max_cost_increase)}",
            )

    if a.get("p50_latency_ms") and b.get("p50_latency_ms"):
        a_lat = float(a["p50_latency_ms"])
        b_lat = float(b["p50_latency_ms"])
        increase = (b_lat - a_lat) / a_lat
        compare(
            "max_latency_increase",
            increase,
            thresholds.max_latency_increase,
            f"p50 latency increased {_pct(increase)} (A {a_lat:.0f}ms → B {b_lat:.0f}ms), "
            f"max increase {_pct(thresholds.max_latency_increase)}",
        )

    judge = summary.get("judge") or {}
    judged = int(judge.get("judged", 0) or 0)
    if judged:
        prefer_a = float(judge.get("prefer_a", 0)) / judged
        compare(
            "max_judge_prefer_a_rate",
            prefer_a,
            thresholds.max_judge_prefer_a_rate,
            f"judge prefers model A in {_pct(prefer_a)} of {judged} judged cases, "
            f"max {_pct(thresholds.max_judge_prefer_a_rate)}",
        )

    # Per-tag rules
    if tag_summaries and tag_thresholds:
        for tag_summary in tag_summaries:
            tag = tag_summary["tag"]
            tag_t = tag_thresholds.get(tag)
            if tag_t is None:
                continue
            tag_drift = float(tag_summary.get("drift_rate", 0.0))
            if tag_drift > tag_t.max_drift_rate:
                violations.append(
                    Violation(
                        rule=f"tag.{tag}.max_drift_rate",
                        message=(
                            f"tag '{tag}' drift rate {_pct(tag_drift)} exceeds its "
                            f"max {_pct(tag_t.max_drift_rate)} "
                            f"({tag_summary.get('flagged_cases', 0)}/{tag_summary.get('cases', 0)} cases)"
                        ),
                        observed=tag_drift,
                        limit=tag_t.max_drift_rate,
                        scope=f"tag:{tag}",
                    )
                )
            tag_refusal = float(tag_summary.get("b", {}).get("refusal_rate", 0.0)) - float(
                tag_summary.get("a", {}).get("refusal_rate", 0.0)
            )
            if tag_refusal > tag_t.max_refusal_increase:
                violations.append(
                    Violation(
                        rule=f"tag.{tag}.max_refusal_increase",
                        message=(
                            f"tag '{tag}' refusal rate increased {_pct(tag_refusal)}, "
                            f"max {_pct(tag_t.max_refusal_increase)}"
                        ),
                        observed=tag_refusal,
                        limit=tag_t.max_refusal_increase,
                        scope=f"tag:{tag}",
                    )
                )
            if tag_summary.get("expected_cases"):
                tag_drop = float(tag_summary["a"].get("expected_hit_rate", 1.0)) - float(
                    tag_summary["b"].get("expected_hit_rate", 1.0)
                )
                if tag_drop > tag_t.max_expected_hit_drop:
                    violations.append(
                        Violation(
                            rule=f"tag.{tag}.max_expected_hit_drop",
                            message=(
                                f"tag '{tag}' expected-hit rate dropped {_pct(tag_drop)}, "
                                f"max {_pct(tag_t.max_expected_hit_drop)}"
                            ),
                            observed=tag_drop,
                            limit=tag_t.max_expected_hit_drop,
                            scope=f"tag:{tag}",
                        )
                    )

    return Verdict(passed=not violations, violations=violations, warnings=warnings)
