"""Performance guardrails (spec §9).

300 cases × 3 samples against the mock provider must complete in < 5 s
excluding sleeps, and report rendering must stay under 1 s.
"""

from __future__ import annotations

import time

import pytest

from modelbump.report import render_html, render_markdown
from modelbump.runner import RunParams, run_suite


@pytest.mark.asyncio
async def test_300_case_run_under_five_seconds(tmp_path, big_suite):
    from modelbump.suite import load_file

    cases = load_file(big_suite).cases
    assert len(cases) == 300
    params = RunParams(
        samples=3,
        cache_dir=str(tmp_path / "cache"),
        cache_enabled=False,
        concurrency=32,
    )
    started = time.perf_counter()
    result = await run_suite(cases, "mock:stable", "mock:drifty", params, progress=False)
    elapsed = time.perf_counter() - started
    assert len(result.cases) == 300
    assert elapsed < 5.0, f"300-case run took {elapsed:.2f}s"


def test_report_render_under_one_second(big_suite):
    from modelbump.suite import load_file

    cases = load_file(big_suite).cases

    # Build a synthetic report payload with 300 cases.
    def side(i: int) -> dict:
        return {"outputs": [f"topic {i}"], "sample_metrics": [], "tool_calls": []}

    payload = {
        "from": {"spec": "mock:stable"},
        "to": {"spec": "mock:drifty"},
        "samples": 3,
        "summary": {
            "total_cases": 300,
            "flagged_cases": 10,
            "drift_rate": 0.03,
            "a": {},
            "b": {},
            "tags": [{"tag": "t1", "cases": 300, "flagged_cases": 10, "drift_rate": 0.03}],
        },
        "cases": [
            {
                "id": case.id,
                "tags": case.tags,
                "flags": ["expected"] if i % 30 == 0 else [],
                "flag_details": {},
                "drift_score": 0.4 if i % 30 == 0 else 0.0,
                "cross_similarity": 0.6,
                "noise_floor": 1.0,
                "kind": "text",
                "a": side(i),
                "b": side(i),
            }
            for i, case in enumerate(cases)
        ],
        "verdict": {"pass": True, "violations": []},
        "reproduce": {"command": "modelbump diff"},
    }
    started = time.perf_counter()
    markdown = render_markdown(payload)
    html = render_html(payload)
    elapsed = time.perf_counter() - started
    assert markdown and html
    assert elapsed < 1.0, f"report render took {elapsed:.2f}s"
