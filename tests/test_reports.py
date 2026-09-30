"""Report writer tests: JSON schema, Markdown, HTML, JUnit (spec §9)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from modelbump.report import render_html, render_markdown, write_html, write_json, write_junit


@pytest.fixture
def report_payload() -> dict:
    return {
        "schema_version": "1.0",
        "modelbump": {"version": "0.1.0"},
        "generated_at": "2026-01-01T00:00:00Z",
        "from": {"spec": "mock:stable", "provider": "mock", "model": "stable"},
        "to": {"spec": "mock:drifty", "provider": "mock", "model": "drifty"},
        "suite": {"path": "suite.jsonl", "cases": 2, "warnings": []},
        "samples": 3,
        "params": {"temperature": 0, "drift_threshold": 0.35, "concurrency": 8},
        "thresholds": {"max_drift_rate": 0.25},
        "tag_thresholds": {},
        "judge_model": None,
        "rubric_hash": "deadbeef",
        "structured_output": {"a": "json_schema", "b": None},
        "reproduce": {
            "command": "modelbump diff --from mock:stable --to mock:drifty",
            "cache_hits": 6,
            "cache_misses": 0,
        },
        "summary": {
            "total_cases": 2,
            "flagged_cases": 1,
            "drift_rate": 0.5,
            "mean_drift_score": 0.4,
            "max_drift_score": 0.8,
            "flag_histogram": {"expected": 1, "semantic": 1},
            "flag_rules": {"expected": "B missed an expectation A hit"},
            "a": {"schema_valid_rate": 1.0, "expected_hit_rate": 1.0, "p50_latency_ms": 100},
            "b": {"schema_valid_rate": 0.5, "expected_hit_rate": 0.5, "p50_latency_ms": 120},
            "tags": [
                {
                    "tag": "extraction",
                    "cases": 2,
                    "flagged_cases": 1,
                    "drift_rate": 0.5,
                }
            ],
            "judge": None,
        },
        "cases": [
            {
                "id": "case-one",
                "tags": ["extraction"],
                "flags": ["expected", "semantic"],
                "flag_details": {"expected": "B missed an expectation A hit"},
                "drift_score": 0.8,
                "cross_similarity": 0.2,
                "noise_floor": 1.0,
                "kind": "text",
                "judge": None,
                "a": {
                    "outputs": ["Paris"],
                    "expected_hit_rate": 1.0,
                    "p50_latency_ms": 100,
                    "sample_metrics": [],
                    "tool_calls": [],
                },
                "b": {
                    "outputs": ["Lyon"],
                    "expected_hit_rate": 0.0,
                    "p50_latency_ms": 120,
                    "sample_metrics": [],
                    "tool_calls": [],
                },
            },
            {
                "id": "case-two",
                "tags": ["classification"],
                "flags": [],
                "flag_details": {},
                "drift_score": 0.0,
                "cross_similarity": 1.0,
                "noise_floor": 1.0,
                "kind": "text",
                "judge": None,
                "a": {"outputs": ["positive"], "sample_metrics": [], "tool_calls": []},
                "b": {"outputs": ["positive"], "sample_metrics": [], "tool_calls": []},
            },
        ],
        "verdict": {
            "pass": False,
            "violations": [
                {
                    "rule": "max_drift_rate",
                    "message": "drift rate 50.0% exceeds max 25.0%",
                    "observed": 0.5,
                    "limit": 0.25,
                    "scope": "suite",
                    "severity": "violation",
                }
            ],
            "warnings": [],
        },
    }


def test_write_json_roundtrip(tmp_path: Path, report_payload):
    path = tmp_path / "report.json"
    write_json(report_payload, path)
    restored = json.loads(path.read_text())
    assert restored["cases"][0]["id"] == "case-one"
    assert restored["schema_version"] == "1.0"


def test_markdown_contains_verdict_and_worst_cases(report_payload):
    markdown = render_markdown(report_payload)
    assert "FAIL" in markdown
    # The PR-comment renderer lists flagged cases in full.
    assert "case-one" in markdown
    assert "max_drift_rate" in markdown
    assert "<!-- modelbump-report -->" in markdown


def test_markdown_is_pr_comment_safe(report_payload):
    markdown = render_markdown(report_payload)
    # No unclosed HTML tags that would break a PR comment.
    assert markdown.count("<details>") == markdown.count("</details>")
    assert markdown.count("<summary>") == markdown.count("</summary>")


def test_markdown_includes_reproduce_command(report_payload):
    markdown = render_markdown(report_payload)
    assert "modelbump diff --from mock:stable --to mock:drifty" in markdown


def test_html_contains_every_case_id(report_payload):
    document = render_html(report_payload)
    for case in report_payload["cases"]:
        assert case["id"] in document


def test_html_is_self_contained(report_payload):
    document = render_html(report_payload)
    # No external scripts or stylesheets: the report must work offline.
    assert not re.search(r"<script[^>]+src=", document)
    assert not re.search(r"<link[^>]+stylesheet", document)
    assert "<style>" in document


def test_html_has_balanced_structure(report_payload):
    document = render_html(report_payload)
    for tag in ("html", "head", "body", "script", "style"):
        opened = len(re.findall(rf"<{tag}[ >]", document))
        assert opened == document.count(f"</{tag}>"), tag


def test_html_embeds_filterable_data(report_payload):
    document = render_html(report_payload)
    assert 'id="modelbump-data"' in document
    assert "data-flag" in document or "flagFilter" in document


def test_html_has_light_dark_toggle(report_payload):
    document = render_html(report_payload)
    # Dark by default, with a persisted light override and a toggle button.
    assert 'data-theme="dark"' in document
    assert 'html[data-theme="light"]' in document
    assert 'id="theme"' in document
    assert "localStorage" in document


def test_write_html_writes_file(tmp_path: Path, report_payload):
    path = tmp_path / "report.html"
    write_html(report_payload, path)
    assert path.exists()
    assert "case-one" in path.read_text()


def test_junit_valid_xml(tmp_path: Path, report_payload):
    path = tmp_path / "report.xml"
    write_junit(report_payload, path)
    tree = ET.parse(path)
    root = tree.getroot()
    assert root.tag == "testsuite"
    assert root.get("tests") == "2"
    assert root.get("failures") == "1"


def test_junit_testcase_names_are_case_ids(tmp_path: Path, report_payload):
    path = tmp_path / "report.xml"
    write_junit(report_payload, path)
    root = ET.parse(path).getroot()
    names = [tc.get("name") for tc in root.iter("testcase")]
    assert "case-one" in names
    assert "case-two" in names


def test_html_survives_minimal_report():
    minimal = {
        "from": {"spec": "a"},
        "to": {"spec": "b"},
        "cases": [],
        "summary": {},
        "verdict": {"pass": True, "violations": []},
    }
    document = render_html(minimal)
    assert "<html" in document


def test_markdown_survives_minimal_report():
    minimal = {
        "from": {"spec": "a"},
        "to": {"spec": "b"},
        "cases": [],
        "summary": {},
        "verdict": {"pass": True},
    }
    markdown = render_markdown(minimal)
    assert "PASS" in markdown
