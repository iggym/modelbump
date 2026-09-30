"""The public Python API: run_diff(...) -> DiffResult (spec §6.9)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from modelbump import DiffResult, run_diff


@pytest.fixture
def suite_file(tmp_path: Path) -> Path:
    path = tmp_path / "suite.jsonl"
    cases = [
        {"id": "a", "tags": ["t1"], "input": "one"},
        {"id": "b", "tags": ["t1", "t2"], "input": "two"},
        {"id": "c", "tags": ["t2"], "input": "three", "expected": "x"},
    ]
    path.write_text("\n".join(json.dumps(c) for c in cases) + "\n")
    return path


def test_run_diff_returns_a_typed_result(suite_file, tmp_path):
    result = run_diff(
        from_model="mock:stable",
        to_model="mock:stable",
        suite=suite_file,
        samples=2,
        cache_dir=str(tmp_path / "cache"),
        progress=False,
    )
    assert isinstance(result, DiffResult)
    assert result.passed is True
    assert result.drift_rate == 0.0
    assert len(result.results) == 3


def test_run_diff_fails_on_drift(suite_file, tmp_path):
    result = run_diff(
        from_model="mock:stable",
        to_model="mock:drifty",
        suite=suite_file,
        samples=2,
        cache_dir=str(tmp_path / "cache"),
        progress=False,
    )
    assert result.passed is False
    assert result.drift_rate > 0.0
    assert result.verdict.violations
    assert any(v.rule == "max_drift_rate" for v in result.verdict.violations)


def test_run_diff_tag_filter(suite_file, tmp_path):
    result = run_diff(
        from_model="mock:stable",
        to_model="mock:drifty",
        suite=suite_file,
        samples=1,
        tags=["t2"],
        cache_dir=str(tmp_path / "cache"),
        progress=False,
    )
    assert {r.case.id for r in result.results} == {"b", "c"}


def test_run_diff_limit(suite_file, tmp_path):
    result = run_diff(
        from_model="mock:stable",
        to_model="mock:drifty",
        suite=suite_file,
        samples=1,
        limit=2,
        cache_dir=str(tmp_path / "cache"),
        progress=False,
    )
    assert len(result.results) == 2


def test_worst_returns_cases_sorted_by_drift(suite_file, tmp_path):
    result = run_diff(
        from_model="mock:stable",
        to_model="mock:drifty",
        suite=suite_file,
        samples=2,
        cache_dir=str(tmp_path / "cache"),
        progress=False,
    )
    worst = result.worst(2)
    assert len(worst) <= 2
    scores = [r.drift_score for r in worst]
    assert scores == sorted(scores, reverse=True)


def test_result_serialises_to_the_report_schema(suite_file, tmp_path):
    result = run_diff(
        from_model="mock:stable",
        to_model="mock:drifty",
        suite=suite_file,
        samples=1,
        cache_dir=str(tmp_path / "cache"),
        progress=False,
    )
    payload = result.to_dict()
    assert payload["schema_version"] == "1.0"
    assert payload["from"]["spec"] == "mock:stable"
    assert payload["to"]["spec"] == "mock:drifty"
    assert payload["verdict"]["pass"] is False
    # The serialised report must be JSON-clean.
    json.dumps(payload)


def test_run_diff_writes_reports_when_out_is_set(suite_file, tmp_path):
    out = tmp_path / "out"
    run_diff(
        from_model="mock:stable",
        to_model="mock:drifty",
        suite=suite_file,
        samples=1,
        out=str(out),
        cache_dir=str(tmp_path / "cache"),
        progress=False,
    )
    for name in ("report.json", "report.md", "report.html"):
        assert (out / name).exists(), name


def test_run_diff_respects_max_cost(suite_file, tmp_path):
    from modelbump.errors import BudgetExceeded

    with pytest.raises(BudgetExceeded):
        run_diff(
            from_model="gpt-4.1",
            to_model="gpt-5",
            suite=suite_file,
            samples=3,
            max_cost=0.0001,
            cache_dir=str(tmp_path / "cache"),
            progress=False,
        )
