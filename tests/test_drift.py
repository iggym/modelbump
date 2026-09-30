"""Drift scoring, flags, and the identical-model invariant (spec §9).

The headline test: an identical model must yield drift 0 and zero flags across
*all* mock variants.
"""

from __future__ import annotations

import pytest

from modelbump.case import Case
from modelbump.drift import FLAG_RULES, compute_case_result
from modelbump.metrics import compute_sample_metrics
from modelbump.providers.base import ModelSpec, Response, ToolCall
from modelbump.providers.mock import VARIANTS, MockProvider


def _responses(texts):
    return [Response(text=t, latency_ms=100.0) for t in texts]


# ---------------------------------------------------------------------------
# The invariant
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("variant", VARIANTS)
async def test_identical_model_zero_drift_and_no_flags(variant, simple_cases):
    """mock:X vs mock:X must produce drift 0 and no flags for every variant."""
    spec = ModelSpec(provider="mock", model=variant)
    provider = MockProvider(spec)
    for case in simple_cases:
        responses = []
        for sample in range(3):
            responses.append(
                await provider.complete(
                    messages=case.messages,
                    system=case.system,
                    tools=case.tools,
                    schema=case.schema,
                    params={"temperature": 0, "_expected": case.expected, "_sample": sample},
                )
            )
        result = compute_case_result(
            case,
            responses,
            responses,
            model_a=f"mock:{variant}",
            model_b=f"mock:{variant}",
        )
        assert result.drift_score == pytest.approx(0.0), (
            f"{variant}/{case.id}: drift {result.drift_score} should be 0"
        )
        assert result.flags == [], f"{variant}/{case.id}: unexpected flags {result.flags}"


# ---------------------------------------------------------------------------
# Flag rules — each one triggers under its documented condition
# ---------------------------------------------------------------------------

def test_flag_errors():
    case = Case(id="e", input="hi")
    a = _responses(["ok", "ok", "ok"])
    b = [Response(error="boom"), Response(error="boom"), Response(error="boom")]
    result = compute_case_result(case, a, b, model_a="A", model_b="B")
    assert "errors" in result.flags


def test_flag_refusal():
    case = Case(id="r", input="hi")
    a = _responses(["sure", "sure", "sure"])
    b = _responses(["I'm sorry, I can't help with that."] * 3)
    result = compute_case_result(case, a, b, model_a="A", model_b="B")
    assert "refusal" in result.flags


def test_flag_schema_on_drop():
    case = Case(
        id="s",
        input="hi",
        schema={"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]},
    )
    a = _responses(['{"a": 1}', '{"a": 1}', '{"a": 1}'])
    b = _responses(['{"b": 2}', '{"b": 2}', '{"b": 2}'])
    result = compute_case_result(case, a, b, model_a="A", model_b="B")
    assert "schema" in result.flags


def test_flag_strict_json():
    case = Case(id="j", input="hi", expected={"a": 1})
    a = _responses(['{"a": 1}'] * 3)
    b = _responses(['```json\n{"a": 1}\n```'] * 3)
    result = compute_case_result(case, a, b, model_a="A", model_b="B")
    assert "strict_json" in result.flags


def test_flag_expected():
    case = Case(id="x", input="hi", expected="Paris")
    a = _responses(["Paris"] * 3)
    b = _responses(["Lyon"] * 3)
    result = compute_case_result(case, a, b, model_a="A", model_b="B")
    assert "expected" in result.flags


def test_flag_expected_tool_and_tool_choice():
    case = Case(
        id="t",
        input="hi",
        tools=[
            {"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}},
            {"type": "function", "function": {"name": "g", "parameters": {"type": "object"}}},
        ],
        expect_tool="f",
    )
    a = [Response(tool_calls=[ToolCall(name="f", arguments={})]) for _ in range(3)]
    b = [Response(tool_calls=[ToolCall(name="g", arguments={})]) for _ in range(3)]
    result = compute_case_result(case, a, b, model_a="A", model_b="B")
    assert "expected_tool" in result.flags
    assert "tool_choice" in result.flags


def test_flag_tool_args_invalid():
    case = Case(
        id="ta",
        input="hi",
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "f",
                    "parameters": {
                        "type": "object",
                        "properties": {"n": {"type": "integer"}},
                        "required": ["n"],
                    },
                },
            }
        ],
        expect_tool="f",
    )
    a = [Response(tool_calls=[ToolCall(name="f", arguments={"n": 1})])] * 3
    b = [Response(tool_calls=[ToolCall(name="f", arguments={"n": "x"})])] * 3
    result = compute_case_result(case, a, b, model_a="A", model_b="B")
    assert "tool_args" in result.flags


def test_flag_length():
    case = Case(id="l", input="hi", expected="ok")
    a = _responses(["ok", "ok", "ok"])
    b = _responses(["ok " + "padding " * 200] * 3)
    result = compute_case_result(case, a, b, model_a="A", model_b="B")
    assert "length" in result.flags


def test_flag_language():
    case = Case(id="lang", input="hi", expected="hello")
    a = _responses(["The service is running and the deploy is complete now"] * 3)
    b = _responses(["モデルの廃止は依存関係の更新と同じです"] * 3)
    result = compute_case_result(case, a, b, model_a="A", model_b="B")
    assert "language" in result.flags


def test_flag_latency():
    case = Case(id="lat", input="hi", expected="ok")
    a = [Response(text="ok", latency_ms=100) for _ in range(3)]
    b = [Response(text="ok", latency_ms=900) for _ in range(3)]
    result = compute_case_result(case, a, b, model_a="A", model_b="B")
    assert "latency" in result.flags


def test_flag_truncated():
    case = Case(id="tr", input="hi", expected="ok")
    a = [Response(text="ok", finish_reason="stop") for _ in range(3)]
    b = [Response(text="ok", finish_reason="length") for _ in range(3)]
    result = compute_case_result(case, a, b, model_a="A", model_b="B")
    assert "truncated" in result.flags


def test_flag_semantic_free_text():
    case = Case(id="sem", input="hi")
    a = _responses(["the quick brown fox jumps over the lazy dog"] * 3)
    b = _responses(["totally unrelated content about quantum chromodynamics"] * 3)
    result = compute_case_result(
        case, a, b, model_a="A", model_b="B", drift_threshold=0.35
    )
    assert "semantic" in result.flags
    assert result.drift_score >= 0.35


def test_semantic_flag_not_applied_to_json_cases():
    case = Case(id="j", input="hi", expected={"a": 1})
    a = _responses(['{"a": 1}'] * 3)
    b = _responses(['{"b": 2}'] * 3)
    result = compute_case_result(case, a, b, model_a="A", model_b="B")
    assert "semantic" not in result.flags


def test_every_flag_has_a_documented_rule():
    from modelbump.drift import DRIFT_FLAGS

    for flag in DRIFT_FLAGS:
        assert flag in FLAG_RULES
        assert FLAG_RULES[flag].strip()


# ---------------------------------------------------------------------------
# Per-sample metrics carry the structured-output mechanism
# ---------------------------------------------------------------------------

def test_sample_metrics_records_mechanism():
    case = Case(id="m", input="hi", schema={"type": "object"})
    response = Response(text="{}", structured_output_mechanism="json_schema")
    metrics = compute_sample_metrics(case, response, model_name="gpt-5")
    assert metrics.structured_output_mechanism == "json_schema"


def test_error_sample_has_no_schema_verdict():
    case = Case(id="m", input="hi", schema={"type": "object"})
    metrics = compute_sample_metrics(case, Response(error="boom"), model_name="x")
    assert metrics.error is True
    assert metrics.schema_valid is None
