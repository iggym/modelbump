"""Unit tests for every metric, hand-built (spec §9)."""

from __future__ import annotations

import pytest

from modelbump.case import Case
from modelbump.metrics import (
    aggregate_model_case,
    cross_similarity,
    detect_language,
    drift_score,
    expected_hit,
    extract_json,
    is_strict_json,
    json_similarity,
    json_subset,
    output_kind,
    self_similarity,
    similarity,
    text_similarity,
    tool_similarity,
)
from modelbump.providers.base import Response, ToolCall

# -- extract_json -----------------------------------------------------------

def test_extract_json_plain():
    value, strict = extract_json('{"a": 1}')
    assert value == {"a": 1} and strict is True


def test_extract_json_fenced_is_not_strict():
    value, strict = extract_json('```json\n{"a": 1}\n```')
    assert value == {"a": 1} and strict is False


def test_extract_json_with_prose():
    value, strict = extract_json('Here you go: {"a": 1} — hope that helps')
    assert value == {"a": 1} and strict is False


def test_extract_json_nested_braces():
    value, _ = extract_json('prefix {"a": {"b": [1, 2, {"c": 3}]}} suffix')
    assert value == {"a": {"b": [1, 2, {"c": 3}]}}


def test_extract_json_handles_escaped_quotes():
    value, _ = extract_json(r'{"msg": "he said \"hi\""}')
    assert value == {"msg": 'he said "hi"'}


def test_extract_json_none_when_absent():
    assert extract_json("no json here") == (None, False)


def test_is_strict_json():
    assert is_strict_json('{"a": 1}') is True
    assert is_strict_json('```json\n{"a": 1}\n```') is False
    assert is_strict_json("prose") is False
    assert is_strict_json("") is False


# -- expected_hit -----------------------------------------------------------

def test_expected_hit_containment():
    assert expected_hit("Paris", "The capital is Paris.") is True
    assert expected_hit("Paris", "The capital is Lyon.") is False


def test_expected_hit_case_insensitive():
    assert expected_hit("PARIS", "paris is the capital") is True


def test_expected_hit_any_of_list():
    assert expected_hit(["alpha", "beta"], "only beta here") is True
    assert expected_hit(["alpha", "beta"], "gamma only") is False


def test_expected_hit_json_subset():
    assert expected_hit({"a": 1}, '{"a": 1, "b": 2}') is True
    assert expected_hit({"a": 1}, '{"a": 2}') is False


def test_expected_hit_nested_json_subset():
    assert expected_hit({"a": {"b": "x"}}, '{"a": {"b": "x", "c": "y"}}') is True
    assert expected_hit({"a": {"b": "x"}}, '{"a": {"b": "z"}}') is False


def test_expected_hit_regex():
    assert expected_hit({"regex": "(?i)cannot"}, "I cannot help with that") is True
    assert expected_hit({"regex": "^yes$"}, "yes indeed") is False


def test_expected_hit_invalid_regex_is_false():
    assert expected_hit({"regex": "("}, "anything") is False


def test_json_subset_numeric_tolerance():
    assert json_subset({"x": 1.0000001}, {"x": 1.0}) is True
    assert json_subset({"x": 1.5}, {"x": 1.0}) is False


# -- similarity -------------------------------------------------------------

def test_text_similarity_identical():
    assert text_similarity("hello world", "hello world") == pytest.approx(1.0)


def test_text_similarity_empty_both():
    assert text_similarity("", "") == 1.0


def test_text_similarity_one_empty():
    assert text_similarity("", "x") == 0.0


def test_text_similarity_range():
    score = text_similarity("the quick brown fox", "the slow brown fox")
    assert 0 < score < 1


def test_json_similarity_identical():
    assert json_similarity({"a": 1, "b": 2}, {"a": 1, "b": 2}) == pytest.approx(1.0)


def test_json_similarity_key_overlap_partial():
    score = json_similarity({"a": 1, "b": 2}, {"a": 1, "c": 3})
    assert 0 < score < 1


def test_json_similarity_disjoint_keys():
    assert json_similarity({"a": 1}, {"b": 2}) == pytest.approx(0.0)


def test_json_similarity_nested_paths():
    score = json_similarity({"a": {"b": 1}}, {"a": {"b": 1, "c": 2}})
    assert score > 0.5


def test_tool_similarity_identical():
    calls = [ToolCall(name="f", arguments={"x": 1})]
    assert tool_similarity(calls, calls) == pytest.approx(1.0)


def test_tool_similarity_different_names():
    a = [ToolCall(name="f", arguments={"x": 1})]
    b = [ToolCall(name="g", arguments={"x": 1})]
    assert tool_similarity(a, b) == pytest.approx(0.5)


def test_tool_similarity_empty_both():
    assert tool_similarity([], []) == 1.0


def test_tool_similarity_one_empty():
    assert tool_similarity([ToolCall(name="f")], []) == 0.0


# -- output_kind ------------------------------------------------------------

def test_output_kind_tool():
    case = Case(id="x", input="hi")
    response = Response(tool_calls=[ToolCall(name="f", arguments={})])
    assert output_kind(case, response) == "tool"


def test_output_kind_json_from_schema():
    case = Case(id="x", input="hi", schema={"type": "object"})
    assert output_kind(case, Response(text="{}")) == "json"


def test_output_kind_json_from_expected_subset():
    case = Case(id="x", input="hi", expected={"a": 1})
    assert output_kind(case, Response(text='{"a": 1}')) == "json"


def test_output_kind_text():
    case = Case(id="x", input="hi", expected="hello")
    assert output_kind(case, Response(text="hello there")) == "text"


# -- drift score math -------------------------------------------------------

def test_drift_score_zero_when_cross_equals_noise():
    assert drift_score(cross_sim=0.9, self_a=0.9, self_b=0.95) == pytest.approx(0.0)


def test_drift_score_positive_when_below_noise():
    score = drift_score(cross_sim=0.5, self_a=1.0, self_b=1.0)
    assert score == pytest.approx(0.5)


def test_drift_score_clamped_at_zero():
    assert drift_score(cross_sim=1.0, self_a=0.8, self_b=0.9) == 0.0


def test_drift_score_uses_min_noise_floor():
    # min(1.0, 0.5) = 0.5 is the noise floor
    score = drift_score(cross_sim=0.25, self_a=1.0, self_b=0.5)
    assert score == pytest.approx(0.5)


def test_drift_score_without_self_similarity():
    assert drift_score(cross_sim=0.4, self_a=None, self_b=None) == pytest.approx(0.6)


def test_drift_score_zero_noise_falls_back():
    score = drift_score(cross_sim=0.3, self_a=0.0, self_b=0.0)
    assert score == pytest.approx(0.7)


# -- aggregation ------------------------------------------------------------

def test_self_similarity_single_sample_is_none():
    assert self_similarity([Response(text="a")], "text") is None


def test_self_similarity_deterministic_is_one():
    responses = [Response(text="same") for _ in range(3)]
    assert self_similarity(responses, "text") == pytest.approx(1.0)


def test_cross_similarity_identical_lists_averages_all_pairs():
    # Cross similarity averages every A/B pair, so a two-item list with one
    # shared and one distinct item lands in the middle.
    a = [Response(text="x"), Response(text="y")]
    b = [Response(text="x"), Response(text="y")]
    assert cross_similarity(a, b, "text") == pytest.approx(0.5, abs=0.05)


def test_cross_similarity_identical_single_sample_is_one():
    assert cross_similarity([Response(text="x")], [Response(text="x")], "text") == pytest.approx(1.0)


def test_cross_similarity_disjoint_is_low():
    a = [Response(text="alpha beta gamma")]
    b = [Response(text="totally unrelated words here")]
    assert cross_similarity(a, b, "text") < 0.3


def test_aggregate_rates():
    case = Case(id="x", input="hi", expected="yes")
    responses = [
        Response(text="yes", tokens_in=10, tokens_out=2, latency_ms=100),
        Response(text="no", tokens_in=10, tokens_out=2, latency_ms=200),
    ]
    stats = aggregate_model_case(case, responses, model_name="mock:stable", kind="text")
    assert stats.expected_hit_rate == pytest.approx(0.5)
    assert stats.error_rate == 0.0
    assert stats.refusal_rate == 0.0
    assert stats.p50_latency_ms == pytest.approx(150)


def test_aggregate_error_rate():
    case = Case(id="x", input="hi")
    responses = [Response(error="boom"), Response(text="ok")]
    stats = aggregate_model_case(case, responses, model_name="m", kind="text")
    assert stats.error_rate == pytest.approx(0.5)


def test_aggregate_refusal_detection():
    case = Case(id="x", input="hi")
    responses = [
        Response(text="I'm sorry, I can't help with that."),
        Response(text="Sure, here you go."),
    ]
    stats = aggregate_model_case(case, responses, model_name="m", kind="text")
    assert stats.refusal_rate == pytest.approx(0.5)


def test_aggregate_schema_valid_rate():
    case = Case(
        id="x",
        input="hi",
        schema={"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]},
    )
    responses = [Response(text='{"a": 1}'), Response(text='{"a": "not an int"}')]
    stats = aggregate_model_case(case, responses, model_name="m", kind="json")
    assert stats.schema_valid_rate == pytest.approx(0.5)


def test_aggregate_tool_args_valid():
    case = Case(
        id="x",
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
    good = Response(tool_calls=[ToolCall(name="f", arguments={"n": 1})])
    bad = Response(tool_calls=[ToolCall(name="f", arguments={"n": "x"})])
    stats = aggregate_model_case(case, [good, bad], model_name="m", kind="tool")
    assert stats.tool_args_valid_rate == pytest.approx(0.5)
    assert stats.expected_tool_hit_rate == pytest.approx(1.0)


# -- language ---------------------------------------------------------------

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("The quick brown fox jumps over the lazy dog and then runs away", "en"),
        ("El despliegue falló porque el servicio no estaba listo para la carga", "es"),
        ("Le déploiement a échoué parce que le service n'était pas prêt", "fr"),
        ("モデルの廃止は依存関係の更新と同じです", "ja"),
        ("Модель не была готова к этой нагрузке", "ru"),
    ],
)
def test_detect_language(text, expected):
    assert detect_language(text) == expected


def test_detect_language_empty():
    assert detect_language("") == "unknown"


# -- similarity dispatch ----------------------------------------------------

def test_similarity_json_kind_uses_structure():
    a = Response(text='{"a": 1, "b": 2}')
    b = Response(text='{"b": 2, "a": 1}')
    assert similarity("json", a, b) == pytest.approx(1.0)


def test_similarity_json_kind_one_side_not_json():
    a = Response(text='{"a": 1}')
    b = Response(text="plain text answer")
    score = similarity("json", a, b)
    assert score < 0.5


def test_similarity_tool_kind():
    a = Response(tool_calls=[ToolCall(name="f", arguments={"x": 1})])
    b = Response(tool_calls=[ToolCall(name="f", arguments={"x": 1})])
    assert similarity("tool", a, b) == pytest.approx(1.0)
