"""Provider factory: turn a model spec into a concrete adapter (F-PROV-1)."""

from __future__ import annotations

from typing import Any

from modelbump.errors import ProviderError
from modelbump.providers.anthropic import AnthropicProvider
from modelbump.providers.base import BaseProvider, ModelSpec, RateLimiter, parse_model_spec
from modelbump.providers.bedrock import BedrockProvider
from modelbump.providers.google import GoogleProvider
from modelbump.providers.mock import MockProvider
from modelbump.providers.openai_chat import (
    AzureOpenAIProvider,
    OpenAIChatProvider,
    OpenAICompatProvider,
)
from modelbump.providers.openai_responses import (
    OpenAIResponsesProvider,
    prefers_responses_api,
)

# Base URLs users can pass with ``--provider-base`` for the openai-compatible path.
KNOWN_COMPAT_BASE_URLS = {
    "groq": "https://api.groq.com/openai/v1",
    "mistral": "https://api.mistral.ai/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "together": "https://api.together.xyz/v1",
    "fireworks": "https://api.fireworks.ai/inference/v1",
    "ollama": "http://localhost:11434/v1",
    "vllm": "http://localhost:8000/v1",
    "xai": "https://api.x.ai/v1",
    "moonshot": "https://api.moonshot.cn/v1",
    "alibaba": "https://dashscope.aliyuncs.com/compatible-mode/v1",
}


def build_provider(
    spec: str | ModelSpec,
    *,
    api: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout: float = 120.0,
    max_retries: int = 4,
    limiter: RateLimiter | None = None,
    extra: dict[str, Any] | None = None,
    keep_raw: bool = False,
) -> BaseProvider:
    """Instantiate a provider adapter for a spec.

    ``api`` overrides the transport for OpenAI-family models: ``chat`` or
    ``responses``. Without it we auto-select — Responses for ``gpt-5*``/``o*``,
    Chat otherwise.
    """
    parsed = spec if isinstance(spec, ModelSpec) else parse_model_spec(spec)
    common: dict[str, Any] = {
        "api_key": api_key,
        "base_url": base_url,
        "timeout": timeout,
        "max_retries": max_retries,
        "limiter": limiter,
        "extra": extra,
        "keep_raw": keep_raw,
    }

    provider = parsed.provider

    if provider == "mock":
        return MockProvider(parsed, **common)
    if provider == "anthropic":
        return AnthropicProvider(parsed, **common)
    if provider == "google":
        return GoogleProvider(parsed, **common)
    if provider == "azure":
        return AzureOpenAIProvider(parsed, **common)
    if provider == "bedrock":
        return BedrockProvider(parsed, **common)
    if provider in ("openai_compat", "openai-compatible"):
        return OpenAICompatProvider(parsed, **common)
    if provider == "openai":
        chosen = _select_openai_api(parsed.model, api)
        if chosen == "responses":
            return OpenAIResponsesProvider(parsed, **common)
        return OpenAIChatProvider(parsed, **common)
    if provider in ("openai_chat", "openai-chat"):
        return OpenAIChatProvider(parsed, **common)
    if provider in ("openai_responses", "openai-responses"):
        return OpenAIResponsesProvider(parsed, **common)

    # Fall back to an OpenAI-compatible base URL when the provider is a known
    # third-party host, so `groq:llama-3.1-8b` just works.
    if provider in KNOWN_COMPAT_BASE_URLS:
        compat = ModelSpec(
            provider="openai_compat",
            model=parsed.model,
            base_url=parsed.base_url or KNOWN_COMPAT_BASE_URLS[provider],
            raw=parsed.raw,
        )
        return OpenAICompatProvider(compat, **common)

    raise ProviderError(
        f"unknown provider {provider!r}. Known providers: mock, openai, anthropic, "
        f"google, azure, bedrock, openai_compat, "
        f"{', '.join(sorted(KNOWN_COMPAT_BASE_URLS))}"
    )


def _select_openai_api(model: str, api: str | None) -> str:
    if api:
        api = api.lower()
        if api in ("responses", "response"):
            return "responses"
        if api in ("chat", "chat_completions", "chat-completions"):
            return "chat"
        raise ProviderError(f"--api must be 'chat' or 'responses', got {api!r}")
    return "responses" if prefers_responses_api(model) else "chat"


def describe_provider(spec: str | ModelSpec, *, api: str | None = None) -> dict[str, Any]:
    """Describe how a spec will be executed, without building a client."""
    parsed = spec if isinstance(spec, ModelSpec) else parse_model_spec(spec)
    info: dict[str, Any] = {
        "spec": str(parsed),
        "provider": parsed.provider,
        "model": parsed.model,
        "base_url": parsed.base_url,
    }
    if parsed.provider == "openai":
        info["api"] = _select_openai_api(parsed.model, api)
    return info


__all__ = [
    "build_provider",
    "describe_provider",
    "parse_model_spec",
    "ModelSpec",
    "KNOWN_COMPAT_BASE_URLS",
]
