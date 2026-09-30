"""Suite loader, validation, importer, and redaction tests (spec §9)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from modelbump.case import Case, find_unknown_fields, stable_id, validate_case
from modelbump.errors import SuiteError
from modelbump.suite import load, load_file
from modelbump.suite.redact import contains_pii, redact, redact_value
from modelbump.suite.traces import load_traces
from modelbump.suite.validate import validate_suite

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Case model
# ---------------------------------------------------------------------------

def test_only_input_required():
    case = Case(input="hello")
    assert case.id.startswith("case-")
    assert case.tags == []


def test_stable_id_is_content_addressed():
    assert stable_id({"a": 1}) == stable_id({"a": 1})
    assert stable_id({"a": 1}) != stable_id({"a": 2})


def test_case_from_dict_missing_input_raises():
    with pytest.raises(ValueError, match="input"):
        Case.from_dict({"id": "x"})


def test_case_unknown_fields_preserved_in_meta():
    case = Case.from_dict({"input": "hi", "weird_field": 123})
    assert case.meta["_extra"]["weird_field"] == 123


def test_find_unknown_fields():
    assert find_unknown_fields({"input": "x", "bogus": 1}) == ["bogus"]


def test_multiturn_messages():
    case = Case(input=[{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}])
    assert case.is_multiturn
    assert len(case.messages) == 2


def test_string_input_becomes_user_message():
    case = Case(input="hi")
    assert case.messages == [{"role": "user", "content": "hi"}]


def test_fingerprint_stable_and_sensitive():
    a = Case(id="x", input="one")
    b = Case(id="x", input="two")
    assert a.fingerprint() != b.fingerprint()
    assert a.fingerprint() == Case(id="x", input="one").fingerprint()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_validate_flags_invalid_schema():
    case = Case(input="hi", schema={"type": "object", "properties": {"a": {"type": "not-a-type"}}})
    problems = validate_case(case)
    assert any("invalid JSON schema" in p for p in problems)


def test_validate_flags_expect_tool_not_declared():
    case = Case(
        input="hi",
        tools=[{"type": "function", "function": {"name": "f", "parameters": {}}}],
        expect_tool="g",
    )
    assert any("not among the declared tools" in p for p in validate_case(case))


def test_validate_flags_bad_message_list():
    case = Case.from_dict({"input": [{"role": "user"}]})
    assert any("missing 'content'" in p for p in validate_case(case))


def test_validate_flags_oversized_input():
    case = Case(input="x" * 200_001)
    assert any("oversized" in p for p in validate_case(case))


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def test_load_jsonl_roundtrip(tmp_path: Path, simple_cases):
    path = tmp_path / "s.jsonl"
    with path.open("w") as fh:
        for case in simple_cases:
            fh.write(json.dumps(case.to_dict()) + "\n")
    result = load_file(path)
    assert len(result.cases) == len(simple_cases)
    assert result.format == "jsonl"


def test_load_json_array(tmp_path: Path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps([{"input": "a"}, {"input": "b"}]))
    assert len(load_file(path).cases) == 2


def test_load_json_cases_object(tmp_path: Path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"cases": [{"input": "a"}]}))
    assert len(load_file(path).cases) == 1


def test_load_yaml_list(tmp_path: Path):
    path = tmp_path / "s.yaml"
    path.write_text("- input: a\n  expected: b\n- input: c\n")
    assert len(load_file(path).cases) == 2


def test_load_csv(tmp_path: Path):
    path = tmp_path / "s.csv"
    path.write_text("id,input,expected,tags\n1,hello,hi,\"[\"\"a\"\",\"\"b\"\"]\"\n")
    result = load_file(path)
    assert result.cases[0].input == "hello"
    assert result.cases[0].tags == ["a", "b"]


def test_load_directory_dedupes(tmp_path: Path):
    (tmp_path / "a.jsonl").write_text('{"id": "dup", "input": "1"}\n')
    (tmp_path / "b.jsonl").write_text('{"id": "dup", "input": "2"}\n{"id": "other", "input": "3"}\n')
    result = load(tmp_path)
    assert len(result.cases) == 2
    assert any("duplicate case id" in w for w in result.warnings)


def test_load_missing_path_raises(tmp_path: Path):
    with pytest.raises(SuiteError):
        load(tmp_path / "nope.jsonl")


def test_load_empty_directory_raises(tmp_path: Path):
    with pytest.raises(SuiteError, match="no suite files"):
        load(tmp_path)


def test_load_invalid_jsonl_line_reports_line_number(tmp_path: Path):
    path = tmp_path / "bad.jsonl"
    path.write_text('{"input": "ok"}\n{not json}\n')
    with pytest.raises(SuiteError, match=":2:"):
        load_file(path)


# ---------------------------------------------------------------------------
# promptfoo importer
# ---------------------------------------------------------------------------

def test_promptfoo_import(tmp_path: Path):
    path = tmp_path / "promptfooconfig.yaml"
    path.write_text(
        """
prompts:
  - "Answer: {{q}}"
tests:
  - description: t1
    vars:
      q: "capital of France"
    assert:
      - type: contains
        value: Paris
      - type: llm-rubric
        value: "is it good"
  - description: t2
    vars:
      q: "extract id"
    assert:
      - type: is-json
        value:
          type: object
          properties:
            id: {type: string}
      - type: javascript
        value: "output.length > 0"
"""
    )
    result = load_file(path)
    assert result.format == "promptfoo"
    assert len(result.cases) == 2
    assert result.cases[0].expected == "Paris"
    assert "capital of France" in result.cases[0].input
    assert result.cases[1].schema is not None
    warnings = " ".join(result.warnings)
    assert "llm-rubric" in warnings
    assert "javascript" in warnings


def test_promptfoo_contains_json(tmp_path: Path):
    path = tmp_path / "pf.yaml"
    path.write_text(
        """
prompts: ["{{q}}"]
tests:
  - vars: {q: "hello"}
    assert:
      - type: contains-json
        value: {"ok": true}
"""
    )
    result = load_file(path)
    assert "ok" in str(result.cases[0].expected)


# ---------------------------------------------------------------------------
# DeepEval / OpenAI Evals importer
# ---------------------------------------------------------------------------

def test_deepeval_expected_output_mapping(tmp_path: Path):
    path = tmp_path / "d.jsonl"
    path.write_text(
        '{"input": "q", "expected_output": "a", "context": ["c"], "tags": ["x"]}\n'
    )
    result = load_file(path)
    case = result.cases[0]
    assert case.expected == "a"
    assert case.meta["context"] == ["c"]
    assert any("expected_output" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Trace import + redaction (F-SUITE-4)
# ---------------------------------------------------------------------------

def test_otel_import_and_redaction(tmp_path: Path):
    path = tmp_path / "traces.jsonl"
    path.write_text(
        json.dumps(
            {
                "attributes": {
                    "gen_ai.request.model": "gpt-4.1",
                    "gen_ai.prompt": "Email jane@example.com or call 555-123-4567.",
                    "gen_ai.system_instructions": "Be brief.",
                }
            }
        )
        + "\n"
    )
    result = load_traces(path)
    assert len(result.cases) == 1
    case = result.cases[0]
    assert "jane@example.com" not in case.flat_input
    assert "555-123-4567" not in case.flat_input
    assert result.redaction.total >= 2
    assert case.system == "Be brief."


def test_langfuse_json_import(tmp_path: Path):
    path = tmp_path / "l.json"
    path.write_text(
        json.dumps(
            {
                "data": [
                    {
                        "id": "o1",
                        "input": {"role": "user", "content": "hi there"},
                        "model": "gpt-4o",
                        "tags": ["docs"],
                        "metadata": {"system": "be nice"},
                    }
                ]
            }
        )
    )
    result = load_traces(path)
    assert result.cases[0].system == "be nice"
    assert "docs" in result.cases[0].tags


def test_langfuse_csv_import(tmp_path: Path):
    path = tmp_path / "l.csv"
    path.write_text('id,input,model\nc1,"hello there",gpt-4o\n')
    result = load_traces(path)
    assert len(result.cases) == 1


def test_trace_sampling(tmp_path: Path):
    path = tmp_path / "t.jsonl"
    with path.open("w") as fh:
        for i in range(20):
            fh.write(json.dumps({"attributes": {"gen_ai.prompt": f"question {i}"}}) + "\n")
    result = load_traces(path, sample=5, seed=1)
    assert len(result.cases) == 5


@pytest.mark.parametrize(
    "text",
    [
        "reach me at bob.smith@corp.io please",
        "call +1 (415) 555-0199 tomorrow",
        "my card is 4111 1111 1111 1111",
        "IBAN GB29 NWBK 6016 1331 9268 19 for the transfer",
        "SSN 123-45-6789 on file",
        "token sk-abcdefghijklmnopqrstuvwxyz",
    ],
)
def test_redaction_catches_pii(text):
    assert contains_pii(text)
    assert not contains_pii(redact(text))


def test_redaction_leaves_clean_text_alone():
    text = "The deployment finished at 14:02 with 3 replicas healthy."
    assert redact(text) == text
    assert not contains_pii(text)


def test_redaction_nested_values():
    payload = {"user": {"email": "a@b.com"}, "items": ["call 555-123-4567"]}
    redacted = redact_value(payload)
    assert "a@b.com" not in json.dumps(redacted)
    assert "555-123-4567" not in json.dumps(redacted)


def test_redaction_does_not_flag_luhn_invalid_numbers():
    # A 16-digit number failing the Luhn check should not be treated as a card.
    assert not contains_pii("reference 1234 5678 9012 3456 is just an id")


# ---------------------------------------------------------------------------
# validate_suite
# ---------------------------------------------------------------------------

def test_validate_suite_reports_stats(tmp_path: Path, simple_cases):
    path = tmp_path / "s.jsonl"
    with path.open("w") as fh:
        for case in simple_cases:
            fh.write(json.dumps(case.to_dict()) + "\n")
    report = validate_suite(path)
    assert report.ok
    assert report.total == len(simple_cases)
    assert report.with_schema == 1
    assert report.with_tools == 1


def test_validate_suite_detects_duplicates(tmp_path: Path):
    path = tmp_path / "s.jsonl"
    path.write_text('{"id": "x", "input": "1"}\n{"id": "x", "input": "2"}\n')
    report = validate_suite(path)
    assert not report.ok
    assert report.duplicate_ids == ["x"]
