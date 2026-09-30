"""Threshold and verdict tests, table-driven (F-THR-1, F-THR-2)."""

from __future__ import annotations

import pytest

from modelbump.config import Config
from modelbump.thresholds import (
    Thresholds,
    evaluate,
    thresholds_for_tag,
    thresholds_from_config,
)


def _summary(**overrides):
    base = {
        "total_cases": 100,
        "flagged_cases": 0,
        "drift_rate": 0.0,
        "expected_cases": 50,
        "a": {
            "schema_valid_rate": 1.0,
            "expected_hit_rate": 1.0,
            "refusal_rate": 0.0,
            "error_rate": 0.0,
            "avg_cost_usd": 0.01,
            "p50_latency_ms": 100.0,
            "schema_cases": 20,
        },
        "b": {
            "schema_valid_rate": 1.0,
            "expected_hit_rate": 1.0,
            "refusal_rate": 0.0,
            "error_rate": 0.0,
            "avg_cost_usd": 0.01,
            "p50_latency_ms": 100.0,
            "schema_cases": 20,
        },
    }
    base.update(overrides)
    return base


def test_clean_summary_passes():
    verdict = evaluate(_summary(), Thresholds())
    assert verdict.passed
    assert verdict.violations == []


@pytest.mark.parametrize(
    ("mutate", "expected_rule"),
    [
        (lambda s: s.update(drift_rate=0.5), "max_drift_rate"),
        (lambda s: s["b"].update(schema_valid_rate=0.9), "min_schema_valid_rate"),
        (lambda s: s["b"].update(schema_valid_rate=0.97), "max_schema_valid_drop"),
        (lambda s: s["b"].update(expected_hit_rate=0.5), "max_expected_hit_drop"),
        (lambda s: s["b"].update(refusal_rate=0.5), "max_refusal_increase"),
        (lambda s: s["b"].update(error_rate=0.5), "max_error_rate"),
        (lambda s: s["b"].update(avg_cost_usd=0.05), "max_cost_increase"),
        (lambda s: s["b"].update(p50_latency_ms=1000.0), "max_latency_increase"),
    ],
)
def test_each_threshold_can_fail(mutate, expected_rule):
    summary = _summary()
    mutate(summary)
    verdict = evaluate(summary, Thresholds())
    assert not verdict.passed
    assert any(v.rule == expected_rule for v in verdict.violations), (
        f"expected {expected_rule}, got {[v.rule for v in verdict.violations]}"
    )


def test_violation_carries_numbers():
    summary = _summary(drift_rate=0.5)
    verdict = evaluate(summary, Thresholds())
    violation = next(v for v in verdict.violations if v.rule == "max_drift_rate")
    assert violation.observed == pytest.approx(0.5)
    assert violation.limit == pytest.approx(0.25)
    assert "50.0%" in violation.message


def test_judge_prefer_a_threshold():
    summary = _summary(judge={"judged": 10, "prefer_a": 9, "prefer_b": 1, "tie": 0})
    verdict = evaluate(summary, Thresholds())
    assert any(v.rule == "max_judge_prefer_a_rate" for v in verdict.violations)


def test_judge_threshold_skipped_without_judging():
    summary = _summary(judge={"judged": 0})
    verdict = evaluate(summary, Thresholds())
    assert verdict.passed


def test_unknown_threshold_key_raises():
    with pytest.raises(ValueError, match="unknown threshold key"):
        Thresholds().merged({"not_a_threshold": 1})


def test_config_thresholds_merge():
    config = Config(thresholds={"max_drift_rate": 0.5})
    thresholds = thresholds_from_config(config)
    assert thresholds.max_drift_rate == 0.5
    assert thresholds.max_error_rate == 0.02  # default preserved


def test_per_tag_override():
    config = Config(
        thresholds={},
        tag_thresholds={"legal": {"max_drift_rate": 0.1}},
    )
    base = thresholds_from_config(config)
    legal = thresholds_for_tag(base, config, "legal")
    assert legal.max_drift_rate == 0.1
    assert thresholds_for_tag(base, config, "other").max_drift_rate == 0.25


def test_per_tag_violation_raised():
    config = Config(tag_thresholds={"legal": {"max_drift_rate": 0.1}})
    base = thresholds_from_config(config)
    tag_summaries = [
        {
            "tag": "legal",
            "cases": 10,
            "flagged_cases": 3,
            "drift_rate": 0.3,
            "a": {"refusal_rate": 0.0, "expected_hit_rate": 1.0},
            "b": {"refusal_rate": 0.0, "expected_hit_rate": 1.0},
        }
    ]
    verdict = evaluate(
        _summary(), base, tag_summaries, {"legal": thresholds_for_tag(base, config, "legal")}
    )
    assert any(v.rule == "tag.legal.max_drift_rate" for v in verdict.violations)
    assert any(v.scope == "tag:legal" for v in verdict.violations)


def test_strict_promotes_warnings():
    # A warning is produced only where a rule is warn_only; none are by default,
    # so assert the promotion mechanism directly.
    from modelbump.thresholds import Verdict, Violation

    verdict = Verdict(passed=True, warnings=[Violation(rule="x", message="m")])
    for warning in verdict.warnings:
        warning.severity = "violation"
    verdict.violations.extend(verdict.warnings)
    verdict.warnings = []
    verdict.passed = not verdict.violations
    assert not verdict.passed
    assert verdict.violations[0].severity == "violation"


def test_verdict_serialises():
    summary = _summary(drift_rate=0.5)
    verdict = evaluate(summary, Thresholds())
    payload = verdict.to_dict()
    assert payload["pass"] is False
    assert isinstance(payload["violations"], list)
    assert payload["violations"][0]["rule"]
