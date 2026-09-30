"""Provider tests against recorded HTTP fixtures (respx), per spec §9.

Covers: success paths, 429 retry with Retry-After, schema pass-through,
tool translation, and the structured-output mechanism record.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from modelbump.case import Case
from modelbump.providers import build_provider, parse_model_spec
from modelbump.providers.anthropic import SCHEMA_TOOL_NAME
from modelbump.providers.base import ModelSpec, RateLimiter
from modelbump.providers.mock import MockProvider
from modelbump.providers.openai_chat import AzureOpenAIProvider
from modelbump.providers.openai_responses import OpenAIResponsesProvider, prefers_responses_api

MESSAGES = [{"role": "user", "content": "Hello"}]


# ---------------------------------------------------------------------------
# Model spec grammar (F-PROV-2)
# ---------------------------------------------------------------------------

def test_parse_spec_with_provider():
    spec = parse_model_spec("anthropic:claude-3-5-sonnet-20241022")
    assert spec.provider == "anthropic"
    assert spec.model == "claude-3-5-sonnet-20241022"


def test_parse_spec_infers_provider():
    spec = parse_model_spec("gpt-4.1")
    assert spec.provider == "openai"


def test_parse_spec_with_base_url():
    spec = parse_model_spec("openai_compat:llama-3.1-8b@http://localhost:8000/v1")
    assert spec.provider == "openai_compat"
    assert spec.model == "llama-3.1-8b"
    assert spec.base_url == "http://localhost:8000/v1"


def test_parse_spec_azure_deployment_and_endpoint():
    spec = parse_model_spec("azure:my-deployment@https://res.openai.azure.com")
    assert spec.provider == "azure"
    assert spec.model == "my-deployment"
    assert spec.base_url == "https://res.openai.azure.com"


def test_parse_spec_unknown_model_raises():
    from modelbump.errors import ProviderError

    with pytest.raises(ProviderError):
        parse_model_spec("totally-unknown-model-xyz")


def test_openai_api_auto_selection():
    assert prefers_responses_api("gpt-5") is True
    assert prefers_responses_api("gpt-5-mini") is True
    assert prefers_responses_api("o3") is True
    assert prefers_responses_api("gpt-4o") is False


def test_build_provider_selects_responses_for_gpt5():
    provider = build_provider("openai:gpt-5", api_key="k")
    assert isinstance(provider, OpenAIResponsesProvider)


def test_build_provider_selects_chat_for_gpt4o():
    provider = build_provider("openai:gpt-4o", api_key="k")
    assert provider.name == "openai_chat"


def test_api_override_forces_chat():
    provider = build_provider("openai:gpt-5", api="chat", api_key="k")
    assert provider.name == "openai_chat"


# ---------------------------------------------------------------------------
# OpenAI Chat (F-PROV-1, F-PROV-4, F-PROV-5)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_openai_chat_success():
    route = respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "model": "gpt-4o",
                "choices": [
                    {"message": {"content": "Hi there"}, "finish_reason": "stop"}
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 3,
                    "prompt_tokens_details": {"cached_tokens": 4},
                },
            },
        )
    )
    provider = build_provider("openai:gpt-4o", api="chat", api_key="test")
    response = await provider.complete(messages=MESSAGES)
    await provider.aclose()
    assert response.ok
    assert response.text == "Hi there"
    assert response.tokens_in == 10
    assert response.tokens_cached == 4
    assert response.finish_reason == "stop"
    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_openai_chat_schema_mechanism_recorded():
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )
    )
    provider = build_provider("openai:gpt-4o", api="chat", api_key="test")
    response = await provider.complete(
        messages=MESSAGES, schema={"type": "object", "properties": {"a": {"type": "string"}}}
    )
    await provider.aclose()
    assert response.structured_output_mechanism == "json_schema"


@pytest.mark.asyncio
@respx.mock
async def test_openai_chat_tool_translation():
    route = respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'},
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5},
            },
        )
    )
    provider = build_provider("openai:gpt-4o", api="chat", api_key="test")
    response = await provider.complete(
        messages=MESSAGES,
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather",
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
                },
            }
        ],
    )
    await provider.aclose()
    assert response.tool_calls[0].name == "get_weather"
    assert response.tool_calls[0].arguments_dict() == {"city": "Paris"}
    sent = json.loads(route.calls.last.request.content)
    assert sent["tools"][0]["function"]["name"] == "get_weather"


@pytest.mark.asyncio
@respx.mock
async def test_openai_chat_retries_on_429_then_succeeds():
    route = respx.post("https://api.openai.com/v1/chat/completions")
    route.side_effect = [
        httpx.Response(429, headers={"retry-after": "0"}, json={"error": {"message": "slow down"}}),
        httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        ),
    ]
    provider = build_provider("openai:gpt-4o", api="chat", api_key="test", max_retries=3)
    response = await provider.complete(messages=MESSAGES)
    await provider.aclose()
    assert response.ok
    assert response.text == "ok"
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_openai_chat_error_status_recorded():
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(401, json={"error": {"message": "bad key"}})
    )
    provider = build_provider("openai:gpt-4o", api="chat", api_key="nope", max_retries=0)
    response = await provider.complete(messages=MESSAGES)
    await provider.aclose()
    assert not response.ok
    assert response.http_status == 401
    assert "bad key" in (response.error or "")


@pytest.mark.asyncio
@respx.mock
async def test_openai_chat_exhausts_retries():
    route = respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(500, json={"error": {"message": "boom"}})
    )
    provider = build_provider("openai:gpt-4o", api="chat", api_key="k", max_retries=2)
    response = await provider.complete(messages=MESSAGES)
    await provider.aclose()
    assert not response.ok
    assert route.call_count == 3  # initial + 2 retries


# ---------------------------------------------------------------------------
# OpenAI Responses (F-PROV-1)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_openai_responses_success():
    route = respx.post("https://api.openai.com/v1/responses").mock(
        return_value=httpx.Response(
            200,
            json={
                "model": "gpt-5",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "Hello from responses"}],
                    }
                ],
                "usage": {
                    "input_tokens": 8,
                    "output_tokens": 4,
                    "input_tokens_details": {"cached_tokens": 2},
                },
            },
        )
    )
    provider = build_provider("openai:gpt-5", api_key="k")
    response = await provider.complete(messages=MESSAGES)
    await provider.aclose()
    assert response.text == "Hello from responses"
    assert response.tokens_in == 8
    assert response.tokens_cached == 2
    sent = json.loads(route.calls.last.request.content)
    assert sent["input"][0]["role"] == "user"


@pytest.mark.asyncio
@respx.mock
async def test_openai_responses_incomplete_maps_to_length():
    respx.post("https://api.openai.com/v1/responses").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "partial"}]}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )
    )
    provider = build_provider("openai:gpt-5", api_key="k")
    response = await provider.complete(messages=MESSAGES)
    await provider.aclose()
    assert response.finish_reason == "length"
    assert response.truncated is True


@pytest.mark.asyncio
@respx.mock
async def test_openai_responses_function_call():
    respx.post("https://api.openai.com/v1/responses").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "name": "lookup",
                        "arguments": '{"id": 7}',
                        "call_id": "call_x",
                    }
                ],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )
    )
    provider = build_provider("openai:gpt-5", api_key="k")
    response = await provider.complete(
        messages=MESSAGES,
        tools=[
            {
                "type": "function",
                "function": {"name": "lookup", "parameters": {"type": "object"}},
            }
        ],
    )
    await provider.aclose()
    assert response.tool_calls[0].name == "lookup"
    assert response.tool_calls[0].arguments_dict() == {"id": 7}


# ---------------------------------------------------------------------------
# Anthropic (F-PROV-4 tool forcing, F-PROV-5 translation)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_anthropic_success_and_cache_tokens():
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "model": "claude-3-5-sonnet-20241022",
                "content": [{"type": "text", "text": "Bonjour"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 12, "output_tokens": 2, "cache_read_input_tokens": 6},
            },
        )
    )
    provider = build_provider("anthropic:claude-3-5-sonnet-20241022", api_key="k")
    response = await provider.complete(messages=MESSAGES)
    await provider.aclose()
    assert response.text == "Bonjour"
    assert response.tokens_cached == 6
    assert response.finish_reason == "stop"


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_schema_uses_tool_forcing():
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "content": [
                    {"type": "tool_use", "id": "t1", "name": SCHEMA_TOOL_NAME, "input": {"a": "b"}}
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 5, "output_tokens": 5},
            },
        )
    )
    provider = build_provider("anthropic:claude-3-5-sonnet-20241022", api_key="k")
    response = await provider.complete(
        messages=MESSAGES, schema={"type": "object", "properties": {"a": {"type": "string"}}}
    )
    await provider.aclose()
    assert response.structured_output_mechanism == "tool_forcing"
    assert json.loads(response.text) == {"a": "b"}
    sent = json.loads(route.calls.last.request.content)
    assert sent["tool_choice"] == {"type": "tool", "name": SCHEMA_TOOL_NAME}


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_tool_translation_strips_unsupported():
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )
    )
    provider = build_provider("anthropic:claude-3-5-sonnet-20241022", api_key="k")
    response = await provider.complete(
        messages=MESSAGES,
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "f",
                    "parameters": {
                        "$schema": "http://json-schema.org/draft-07/schema#",
                        "type": "object",
                        "properties": {"x": {"type": "string"}},
                    },
                },
            }
        ],
    )
    await provider.aclose()
    sent = json.loads(route.calls.last.request.content)
    assert "$schema" not in sent["tools"][0]["input_schema"]
    assert any("$schema" in w for w in response.translation_warnings)


# ---------------------------------------------------------------------------
# Google (F-PROV-4 responseSchema)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_google_success_and_response_schema():
    route = respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "modelVersion": "gemini-2.5-flash",
                "candidates": [
                    {
                        "content": {"parts": [{"text": '{"a": "b"}'}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 7,
                    "candidatesTokenCount": 3,
                    "cachedContentTokenCount": 2,
                },
            },
        )
    )
    provider = build_provider("google:gemini-2.5-flash", api_key="k")
    response = await provider.complete(
        messages=MESSAGES,
        schema={
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "additionalProperties": False,
        },
    )
    await provider.aclose()
    assert response.structured_output_mechanism == "responseSchema"
    sent = json.loads(route.calls.last.request.content)
    # additionalProperties is unsupported by Gemini and must be stripped.
    assert "additionalProperties" not in sent["generationConfig"]["responseSchema"]


@pytest.mark.asyncio
@respx.mock
async def test_google_safety_block():
    respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    ).mock(
        return_value=httpx.Response(
            200, json={"promptFeedback": {"blockReason": "SAFETY"}, "candidates": []}
        )
    )
    provider = build_provider("google:gemini-2.5-flash", api_key="k")
    response = await provider.complete(messages=MESSAGES)
    await provider.aclose()
    assert not response.ok
    assert response.finish_reason == "refusal"


@pytest.mark.asyncio
@respx.mock
async def test_google_function_call():
    respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"parts": [{"functionCall": {"name": "f", "args": {"x": 1}}}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
            },
        )
    )
    provider = build_provider("google:gemini-2.5-flash", api_key="k")
    response = await provider.complete(
        messages=MESSAGES,
        tools=[{"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}],
    )
    await provider.aclose()
    assert response.tool_calls[0].name == "f"
    assert response.tool_calls[0].arguments_dict() == {"x": 1}


# ---------------------------------------------------------------------------
# Azure
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_azure_deployment_path_and_api_version():
    route = respx.post(
        "https://res.openai.azure.com/openai/deployments/my-deploy/chat/completions"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "azure ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )
    )
    provider = AzureOpenAIProvider(
        ModelSpec(provider="azure", model="my-deploy", base_url="https://res.openai.azure.com"),
        api_key="k",
        extra={"api_version": "2024-10-21"},
    )
    response = await provider.complete(messages=MESSAGES)
    await provider.aclose()
    assert response.text == "azure ok"
    assert "api-version=2024-10-21" in str(route.calls.last.request.url)


@pytest.mark.asyncio
async def test_azure_without_endpoint_errors():
    provider = AzureOpenAIProvider(
        ModelSpec(provider="azure", model="d", base_url=None), api_key="k"
    )
    response = await provider.complete(messages=MESSAGES)
    await provider.aclose()
    assert not response.ok
    assert "endpoint" in (response.error or "").lower()


# ---------------------------------------------------------------------------
# OpenAI-compatible base URL
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_openai_compat_base_url():
    route = respx.post("http://localhost:8000/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "local"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )
    )
    provider = build_provider("openai_compat:llama-3.1-8b@http://localhost:8000/v1", api_key="k")
    response = await provider.complete(messages=MESSAGES)
    await provider.aclose()
    assert response.text == "local"
    assert route.called


# ---------------------------------------------------------------------------
# Rate limiter (F-PROV-6)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rate_limiter_allows_under_limit():
    limiter = RateLimiter(rpm=10)
    from modelbump.providers.base import RateLimiter as RL

    # Should return immediately; nothing to assert beyond "it completes".
    await limiter.acquire()
    assert isinstance(limiter, RL)


# ---------------------------------------------------------------------------
# Mock provider determinism (F-PROV-7)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mock_is_deterministic():
    spec = ModelSpec(provider="mock", model="stable")
    a = MockProvider(spec)
    b = MockProvider(spec)
    case_params = {"_expected": "hello", "temperature": 0}
    r1 = await a.complete(messages=MESSAGES, params=dict(case_params))
    r2 = await b.complete(messages=MESSAGES, params=dict(case_params))
    assert r1.text == r2.text
    assert r1.text == "hello"


@pytest.mark.asyncio
async def test_mock_variants_broken_and_refusenik():
    broken = MockProvider(ModelSpec(provider="mock", model="broken"))
    response = await broken.complete(messages=MESSAGES)
    assert not response.ok and response.http_status == 500

    refusenik = MockProvider(ModelSpec(provider="mock", model="refusenik"))
    response = await refusenik.complete(messages=MESSAGES)
    assert "can't help" in response.text or "cannot" in response.text.lower()


@pytest.mark.asyncio
async def test_mock_strict_json_produces_no_fences():
    from modelbump.metrics import is_strict_json

    provider = MockProvider(ModelSpec(provider="mock", model="strict-json"))
    response = await provider.complete(
        messages=MESSAGES,
        schema={"type": "object", "properties": {"a": {"type": "string"}}},
    )
    assert is_strict_json(response.text)


@pytest.mark.asyncio
async def test_mock_drifty_is_deterministic():
    spec = ModelSpec(provider="mock", model="drifty")
    first = MockProvider(spec)
    second = MockProvider(spec)
    params = {"_expected": "Paris", "temperature": 0}
    r1 = await first.complete(messages=MESSAGES, params=dict(params))
    r2 = await second.complete(messages=MESSAGES, params=dict(params))
    assert r1.text == r2.text


@pytest.mark.asyncio
async def test_mock_stable_satisfies_schema_and_expected_together():
    """A case may carry both a schema and an expected JSON subset.

    The faithful mock must satisfy both, otherwise stable→stable reports a
    spurious schema regression (a false positive that would poison the demo).
    """
    from modelbump.metrics import compute_sample_metrics

    provider = MockProvider(ModelSpec(provider="mock", model="stable"))
    schema = {
        "type": "object",
        "properties": {"refundable": {"type": "boolean"}, "reason": {"type": "string"}},
        "required": ["refundable", "reason"],
    }
    expected = {"refundable": False}
    response = await provider.complete(
        messages=MESSAGES, schema=schema, params={"_expected": expected}
    )
    payload = json.loads(response.text)
    assert payload["refundable"] is False  # expectation honoured
    assert isinstance(payload["reason"], str)  # schema still satisfied

    case = Case(id="c", input="q", schema=schema, expected=expected)
    metrics = compute_sample_metrics(case, response, model_name="mock:stable")
    assert metrics.schema_valid is True
    assert metrics.expected_hit is True


@pytest.mark.asyncio
async def test_mock_stable_schema_expected_subset_nested():
    """Nested expected subsets merge rather than replacing the object."""
    from modelbump.metrics import compute_sample_metrics

    provider = MockProvider(ModelSpec(provider="mock", model="stable"))
    schema = {
        "type": "object",
        "properties": {
            "meta": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "tag": {"type": "string"}},
                "required": ["id", "tag"],
            }
        },
        "required": ["meta"],
    }
    case = Case(id="c", input="q", schema=schema, expected={"meta": {"id": 7}})
    response = await provider.complete(
        messages=MESSAGES, schema=schema, params={"_expected": case.expected}
    )
    payload = json.loads(response.text)
    assert payload["meta"]["id"] == 7
    assert isinstance(payload["meta"]["tag"], str)
    metrics = compute_sample_metrics(case, response, model_name="mock:stable")
    assert metrics.schema_valid is True


# ---------------------------------------------------------------------------
# env status masking (F-PROV-2)
# ---------------------------------------------------------------------------

def test_env_status_masks_secrets(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-abcdefghijklmnop")
    from modelbump.providers.base import env_status

    rows = {row["provider"]: row for row in env_status()}
    openai = rows["openai"]
    masked = openai["env"][0]["masked"]
    assert "abcdefghijklmnop" not in masked
    assert masked.startswith("sk-a")
    assert "•" in masked
