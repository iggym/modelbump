"""OpenAI Chat Completions adapter (F-PROV-1, F-PROV-4)."""

from __future__ import annotations

from typing import Any

from modelbump.providers.base import BaseProvider, Response, ToolCall
from modelbump.providers.translate import to_openai_tools


class OpenAIChatProvider(BaseProvider):
    name = "openai_chat"

    def default_base_url(self) -> str:
        return "https://api.openai.com/v1"

    def auth_headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    # -- request ---------------------------------------------------------
    def build_request(
        self,
        *,
        messages: list[dict[str, Any]],
        system: str | None,
        tools: list[dict[str, Any]] | None,
        schema: dict[str, Any] | None,
        params: dict[str, Any],
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        params = dict(params)
        payload_messages: list[dict[str, Any]] = []
        if system:
            payload_messages.append({"role": "system", "content": system})
        payload_messages.extend(_normalise_messages(messages))

        body: dict[str, Any] = {"model": self.model, "messages": payload_messages}
        _apply_sampling(body, params, self.name)

        warnings: list[str] = []
        if tools:
            translated, warnings = to_openai_tools(tools)
            body["tools"] = translated
            body["tool_choice"] = "auto"

        mechanism = None
        if schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "modelbump_response",
                    "schema": schema,
                    "strict": False,
                },
            }
            mechanism = "json_schema"
        self._mechanism = mechanism
        self._translation_warnings = warnings
        return "POST", "/chat/completions", body

    # -- response --------------------------------------------------------
    def parse_response(self, payload: Any, *, http_status: int) -> Response:
        choices = payload.get("choices") or []
        if not choices:
            return Response(error="provider returned no choices", http_status=http_status)
        choice = choices[0]
        message = choice.get("message") or {}

        text = message.get("content") or ""
        if isinstance(text, list):  # some compatible servers return content parts
            text = "".join(
                part.get("text", "") for part in text if isinstance(part, dict)
            )

        tool_calls: list[ToolCall] = []
        for call in message.get("tool_calls") or []:
            fn = call.get("function") or {}
            tool_calls.append(
                ToolCall(
                    name=fn.get("name", ""),
                    arguments=fn.get("arguments"),
                    id=call.get("id"),
                )
            )
        # Legacy single function_call shape.
        legacy = message.get("function_call")
        if legacy and not tool_calls:
            tool_calls.append(
                ToolCall(name=legacy.get("name", ""), arguments=legacy.get("arguments"))
            )

        usage = payload.get("usage") or {}
        details = usage.get("prompt_tokens_details") or {}
        finish_reason = choice.get("finish_reason")
        error = None
        if finish_reason == "content_filter":
            error = "provider blocked the response (content_filter)"

        response = Response(
            text=text,
            tool_calls=tool_calls,
            tokens_in=usage.get("prompt_tokens"),
            tokens_out=usage.get("completion_tokens"),
            tokens_cached=details.get("cached_tokens"),
            finish_reason=finish_reason,
            error=error,
            http_status=http_status,
            structured_output_mechanism=getattr(self, "_mechanism", None),
            translation_warnings=list(getattr(self, "_translation_warnings", [])),
            model=payload.get("model") or self.model,
        )
        return response


def _normalise_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for message in messages:
        content = message.get("content")
        if content is None:
            content = ""
        out.append({"role": message.get("role", "user"), "content": content})
    return out


def _apply_sampling(body: dict[str, Any], params: dict[str, Any], provider: str) -> None:
    if params.get("temperature") is not None:
        body["temperature"] = params["temperature"]
    if params.get("max_tokens") is not None:
        body["max_tokens"] = params["max_tokens"]
    if params.get("top_p") is not None:
        body["top_p"] = params["top_p"]
    if params.get("seed") is not None:
        body["seed"] = params["seed"]
    if params.get("stop") is not None:
        body["stop"] = params["stop"]


class OpenAICompatProvider(OpenAIChatProvider):
    """Any OpenAI-compatible base URL: vLLM, Ollama, Groq, Mistral, DeepSeek,
    OpenRouter, Together, Fireworks, ... (F-PROV-1)."""

    name = "openai_compat"

    def default_base_url(self) -> str:
        import os

        return os.environ.get("OPENAI_COMPAT_BASE_URL", "http://localhost:8000/v1")

    def resolve_api_key(self) -> str | None:
        import os

        return os.environ.get("OPENAI_COMPAT_API_KEY") or os.environ.get("OPENAI_API_KEY")


class AzureOpenAIProvider(OpenAIChatProvider):
    """Azure OpenAI: ``azure:<deployment>@<endpoint>`` (F-PROV-1)."""

    name = "azure"

    def default_base_url(self) -> str:
        import os

        return os.environ.get("AZURE_OPENAI_ENDPOINT", "")

    def resolve_api_key(self) -> str | None:
        import os

        return os.environ.get("AZURE_OPENAI_API_KEY")

    def auth_headers(self) -> dict[str, str]:
        return {"api-key": self.api_key} if self.api_key else {}

    @property
    def api_version(self) -> str:
        import os

        return (
            self.extra.get("api_version")
            or os.environ.get("AZURE_OPENAI_API_VERSION")
            or "2024-10-21"
        )

    def build_request(
        self,
        *,
        messages: list[dict[str, Any]],
        system: str | None,
        tools: list[dict[str, Any]] | None,
        schema: dict[str, Any] | None,
        params: dict[str, Any],
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        if not self.base_url:
            raise ValueError(
                "azure provider needs an endpoint: azure:<deployment>@https://<resource>.openai.azure.com"
            )
        _, path, body = super().build_request(
            messages=messages, system=system, tools=tools, schema=schema, params=params
        )
        deployment = self.model
        full_path = (
            f"/openai/deployments/{deployment}/chat/completions"
            f"?api-version={self.api_version}"
        )
        return "POST", full_path, body
