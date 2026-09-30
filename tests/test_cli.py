"""End-to-end CLI integration tests (spec §9).

These drive the real CLI entry point through ``main()`` so exit codes,
argument parsing, and the report writers are all exercised together.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from modelbump.cli import main


def _run(argv, capsys):
    try:
        code = main(argv)
    except SystemExit as exc:  # argparse errors and explicit exits
        code = exc.code if isinstance(exc.code, int) else 1
    captured = capsys.readouterr()
    return code, captured.out + captured.err


@pytest.fixture
def demo_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    code, _ = _run(["init"], None) if False else (0, "")
    # Use the CLI itself to scaffold, matching the real user path.
    import modelbump.cli as cli

    cli.main(["init", "."])
    return tmp_path


def test_version_flag(capsys):
    code, out = _run(["--version"], capsys)
    assert code == 0
    assert "modelbump" in out


def test_init_creates_a_valid_suite(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    code, out = _run(["init", "."], capsys)
    assert code == 0
    assert (tmp_path / "suite.jsonl").exists()
    assert (tmp_path / "modelbump.toml").exists()
    # The generated suite must validate cleanly.
    code, out = _run(["suite", "validate", "suite.jsonl"], capsys)
    assert code == 0
    assert "valid" in out.lower()


def test_init_suite_has_at_least_twelve_cases(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _run(["init", "."], capsys)
    lines = [line for line in (tmp_path / "suite.jsonl").read_text().splitlines() if line.strip()]
    assert len(lines) >= 12
    for line in lines:
        json.loads(line)  # every line must be valid JSON


def test_init_suite_covers_required_categories(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _run(["init", "."], capsys)
    text = (tmp_path / "suite.jsonl").read_text().lower()
    for tag in [
        "extraction",
        "classification",
        "summarization",
        "tool-use",
        "multi-turn",
        "safety",
        "reasoning",
        "formatting",
        "long-context",
        "non-english",
    ]:
        assert tag in text, f"example suite missing {tag} coverage"


def test_validate_exits_nonzero_on_bad_suite(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "bad.jsonl").write_text('{"input": "ok"}\n{oops}\n')
    code, out = _run(["suite", "validate", "bad.jsonl"], capsys)
    assert code != 0


def test_diff_stable_vs_stable_passes(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _run(["init", "."], capsys)
    code, out = _run(
        [
            "diff",
            "--from", "mock:stable",
            "--to", "mock:stable",
            "--suite", "suite.jsonl",
            "--samples", "2",
            "--no-progress",
            "--out", "out",
        ],
        capsys,
    )
    assert code == 0
    report = json.loads((tmp_path / "out" / "report.json").read_text())
    assert report["verdict"]["pass"] is True
    assert report["summary"]["drift_rate"] == 0.0


def test_diff_stable_vs_drifty_fails_with_expected_violations(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _run(["init", "."], capsys)
    code, out = _run(
        [
            "diff",
            "--from", "mock:stable",
            "--to", "mock:drifty",
            "--suite", "suite.jsonl",
            "--samples", "2",
            "--no-progress",
            "--out", "out",
        ],
        capsys,
    )
    assert code == 1
    report = json.loads((tmp_path / "out" / "report.json").read_text())
    assert report["verdict"]["pass"] is False
    rules = {v["rule"] for v in report["verdict"]["violations"]}
    assert "max_drift_rate" in rules
    assert "min_schema_valid_rate" in rules
    assert "max_schema_valid_drop" in rules
    assert report["summary"]["drift_rate"] > 0.25


def test_diff_writes_all_three_reports(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _run(["init", "."], capsys)
    _run(
        [
            "diff", "--from", "mock:stable", "--to", "mock:drifty",
            "--suite", "suite.jsonl", "--samples", "1", "--no-progress", "--out", "out",
        ],
        capsys,
    )
    for name in ("report.json", "report.md", "report.html"):
        assert (tmp_path / "out" / name).exists(), name


def test_diff_junit_output(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _run(["init", "."], capsys)
    _run(
        [
            "diff", "--from", "mock:stable", "--to", "mock:drifty",
            "--suite", "suite.jsonl", "--samples", "1", "--no-progress",
            "--out", "out", "--junit", "out/report.xml",
        ],
        capsys,
    )
    assert (tmp_path / "out" / "report.xml").exists()


def test_diff_json_format_prints_report(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _run(["init", "."], capsys)
    code, out = _run(
        [
            "diff", "--from", "mock:stable", "--to", "mock:drifty",
            "--suite", "suite.jsonl", "--samples", "1", "--no-progress",
            "--format", "json",
        ],
        capsys,
    )
    payload = json.loads(out)
    assert payload["from"]["spec"] == "mock:stable"
    assert payload["verdict"]["pass"] is False


def test_diff_limit_and_tag_filter(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _run(["init", "."], capsys)
    code, out = _run(
        [
            "diff", "--from", "mock:stable", "--to", "mock:drifty",
            "--suite", "suite.jsonl", "--samples", "1", "--no-progress",
            "--tag", "extraction", "--format", "json",
        ],
        capsys,
    )
    payload = json.loads(out)
    assert payload["summary"]["total_cases"] == 2
    assert all("extraction" in c["tags"] for c in payload["cases"])


def test_diff_dry_run_prints_projection(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _run(["init", "."], capsys)
    code, out = _run(
        ["diff", "--from", "gpt-4.1", "--to", "gpt-5", "--suite", "suite.jsonl", "--dry-run"],
        capsys,
    )
    assert code == 0
    assert "projected" in out.lower()


def test_diff_max_cost_aborts(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _run(["init", "."], capsys)
    code, out = _run(
        [
            "diff", "--from", "gpt-4.1", "--to", "gpt-5",
            "--suite", "suite.jsonl", "--max-cost", "0.0001",
        ],
        capsys,
    )
    assert code == 2
    assert "budget exceeded" in out.lower()


def test_diff_resumes_from_cache(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _run(["init", "."], capsys)
    base = [
        "diff", "--from", "mock:stable", "--to", "mock:drifty",
        "--suite", "suite.jsonl", "--samples", "1", "--no-progress",
    ]
    _run(base, capsys)
    code, out = _run(base, capsys)
    assert "hits" in out.lower()


def test_report_command_rerenders(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _run(["init", "."], capsys)
    _run(
        [
            "diff", "--from", "mock:stable", "--to", "mock:drifty",
            "--suite", "suite.jsonl", "--samples", "1", "--no-progress", "--out", "out",
        ],
        capsys,
    )
    code, out = _run(["report", "out", "--format", "md"], capsys)
    assert code == 0
    assert "FAIL" in out


def test_compare_command(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _run(["init", "."], capsys)
    _run(
        [
            "diff", "--from", "mock:stable", "--to", "mock:stable",
            "--suite", "suite.jsonl", "--samples", "1", "--no-progress", "--out", "a",
        ],
        capsys,
    )
    _run(
        [
            "diff", "--from", "mock:stable", "--to", "mock:drifty",
            "--suite", "suite.jsonl", "--samples", "1", "--no-progress", "--out", "b",
        ],
        capsys,
    )
    code, out = _run(["compare", "a/report.json", "b/report.json"], capsys)
    assert code == 0
    assert "worsened" in out.lower() or "drift" in out.lower()


def test_cache_stats_and_clear(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _run(["init", "."], capsys)
    _run(
        [
            "diff", "--from", "mock:stable", "--to", "mock:drifty",
            "--suite", "suite.jsonl", "--samples", "1", "--no-progress",
        ],
        capsys,
    )
    code, out = _run(["cache", "stats"], capsys)
    assert code == 0
    assert "entries" in out.lower()
    code, out = _run(["cache", "clear", "--yes"], capsys)
    assert code == 0


def test_calendar_lists_retirements(capsys):
    code, out = _run(["calendar"], capsys)
    assert code == 0
    assert "retire" in out.lower()


def test_calendar_json(capsys):
    code, out = _run(["calendar", "--json"], capsys)
    payload = json.loads(out)
    retirements = payload["retirements"] if isinstance(payload, dict) else payload
    assert retirements
    assert {"model", "provider", "retirement_date", "successor"} <= set(retirements[0])


def test_providers_masks_keys(capsys, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-supersecretvalue123")
    code, out = _run(["providers"], capsys)
    assert code == 0
    assert "supersecretvalue123" not in out
    assert "openai" in out


def test_doctor_runs(capsys):
    code, out = _run(["doctor"], capsys)
    assert code == 0
    assert "modelbump" in out.lower()


def test_suite_stats(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _run(["init", "."], capsys)
    code, out = _run(["suite", "stats", "suite.jsonl"], capsys)
    assert code == 0
    assert "cases" in out.lower()


def test_suite_from_traces(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "t.jsonl").write_text(
        json.dumps(
            {
                "attributes": {
                    "gen_ai.prompt": "Email a@b.com about the invoice",
                    "gen_ai.request.model": "gpt-4.1",
                }
            }
        )
        + "\n"
    )
    code, out = _run(["suite", "from-traces", "t.jsonl", "--out", "cases.jsonl"], capsys)
    assert code == 0
    written = (tmp_path / "cases.jsonl").read_text()
    assert "a@b.com" not in written
    assert "REDACTED" in written


def test_unknown_command_exits_nonzero(capsys):
    code, out = _run(["frobnicate"], capsys)
    assert code != 0
