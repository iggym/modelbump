"""Judge tests: position swap, rubric hashing, cost accounting, mock judge."""

from __future__ import annotations

import pytest

from modelbump.case import Case
from modelbump.judge import (
    RUBRIC_VERSION,
    Judge,
    _build_prompt,
    rubric_hash,
)
from modelbump.providers.base import ModelSpec, Response
from modelbump.providers.mock import MockProvider


@pytest.fixture
def judge() -> Judge:
    provider = MockProvider(ModelSpec(provider="mock", model="stable"))
    return Judge(provider, model_name="mock:stable")


def test_rubric_hash_is_stable_and_versioned():
    assert rubric_hash() == rubric_hash()
    assert len(rubric_hash()) == 16
    assert RUBRIC_VERSION


def test_custom_rubric_changes_hash():
    assert rubric_hash("Prefer concise answers.") != rubric_hash()


def test_build_prompt_contains_both_outputs_and_json_instruction():
    case = Case(id="x", input="what is 2+2?")
    prompt = _build_prompt(case, "four", "4")
    assert "four" in prompt
    assert "4" in prompt
    assert "FIRST OUTPUT:" in prompt
    assert "SECOND OUTPUT:" in prompt
    assert "JSON" in prompt


@pytest.mark.asyncio
async def test_judge_identical_outputs_is_equivalent(judge):
    case = Case(id="x", input="q", expected="yes")
    verdict = await judge.judge_case(case, "the same answer", "the same answer")
    assert verdict.equivalent is True
    assert verdict.better == "tie"
    assert not verdict.uncertain


@pytest.mark.asyncio
async def test_judge_position_swap_cancels_position_bias(judge):
    """The mock prefers the shorter output regardless of position, so the
    swapped order agrees and the verdict is a stable preference for A."""
    case = Case(id="x", input="q")
    verdict = await judge.judge_case(case, "short", "a much much longer answer here")
    assert verdict.better == "a"
    assert not verdict.uncertain
    assert verdict.order1 is not None and verdict.order2 is not None


@pytest.mark.asyncio
async def test_judge_position_swap_detects_disagreement():
    """A judge that always prefers whatever is shown *first* must be recorded
    as uncertain, because the swapped order disagrees."""

    class FirstWinsProvider(MockProvider):
        async def complete(self, **kwargs):  # type: ignore[override]
            import json as _json

            return Response(
                text=_json.dumps({"equivalent": False, "better": "first", "reason": "first"}),
                finish_reason="stop",
            )

    provider = FirstWinsProvider(ModelSpec(provider="mock", model="stable"))
    judge = Judge(provider, model_name="mock:firstwins")
    verdict = await judge.judge_case(Case(id="x", input="q"), "alpha", "beta")
    assert verdict.uncertain is True
    assert verdict.better == "tie"
    assert "swap" in verdict.reason.lower()


@pytest.mark.asyncio
async def test_judge_tracks_cost(judge):
    case = Case(id="x", input="q")
    verdict = await judge.judge_case(case, "a", "b")
    assert verdict.cost_usd is not None
    assert verdict.cost_usd >= 0


@pytest.mark.asyncio
async def test_judge_records_uncertain_on_bad_output():
    class BrokenProvider(MockProvider):
        async def complete(self, **kwargs):  # type: ignore[override]
            return Response(text="not json at all", finish_reason="stop")

    provider = BrokenProvider(ModelSpec(provider="mock", model="stable"))
    judge = Judge(provider, model_name="mock:broken")
    verdict = await judge.judge_case(Case(id="x", input="q"), "a", "b")
    assert verdict.uncertain is True
    assert verdict.better == "tie"


@pytest.mark.asyncio
async def test_judge_reports_provider_errors():
    class FailingProvider(MockProvider):
        async def complete(self, **kwargs):  # type: ignore[override]
            return Response(error="HTTP 500: boom")

    provider = FailingProvider(ModelSpec(provider="mock", model="stable"))
    judge = Judge(provider, model_name="mock:failing")
    verdict = await judge.judge_case(Case(id="x", input="q"), "a", "b")
    assert verdict.error
    assert verdict.uncertain is True
