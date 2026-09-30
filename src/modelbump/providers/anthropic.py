"""Anthropic Messages adapter (F-PROV-1, F-PROV-4).

Structured output is achieved by forcing a tool call whose input schema is the
requested schema — the mechanism is recorded because Anthropic has no native
``response_format`` at the time of writing.
"""

from __future__ import annotations

from typing import Any

from modelbump.providers.base import BaseProvider, Response, ToolCall
from modelbump.providers.translate import to_anthropic_tools

SCHEMA_TOOL_NAME = "modelbump_structured_output"


class AnthropicProvider(BaseProvider):
    name = "anthropic"

    def default_base_url(self) -> str:
        import os

        return os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1")

    def auth_headers(self) -> dict[str, str]:
        headers = {"anthropic-version": "2023-06-01"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

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
        payload_messages = [
            {"role": m.get("role", "user"), "content": m.get("content", "")}
            for m in messages
            if m.get("role") != "system"
        ]
        body: dict[str, Any] = {
            "model": self.model,
            "messages": payload_messages,
            "max_tokens": params.get("max_tokens") or 1024,
        }
        if system:
            body["system"] = system
        if params.get("temperature") is not None:
            body["temperature"] = params["temperature"]
        if params.get("top_p") is not None:
            body["top_p"] = params["top_p"]
        if params.get("stop") is not None:
            stop = params["stop"]
            body["stop_sequences"] = stop if isinstance(stop, list) else [stop]

        warnings: list[str] = []
        mechanism = None
        anthropic_tools: list[dict[str, Any]] = []
        if tools:
            anthropic_tools, tool_warnings = to_anthropic_tools(tools)
            warnings.extend(tool_warnings)

        if schema:
            anthropic_tools = list(anthropic_tools) + [
                {
                    "name": SCHEMA_TOOL_NAME,
                    "description": "Return the answer as structured data.",
                    "input_schema": schema,
                }
            ]
            body["tool_choice"] = {"type": "tool", "name": SCHEMA_TOOL_NAME}
            mechanism = "tool_forcing"

        if anthropic_tools:
            body["tools"] = anthropic_tools
            if "tool_choice" not in body and not schema:
                body["tool_choice"] = {"type": "auto"}

        self._mechanism = mechanism
        self._translation_warnings = warnings
        self._schema_tool_name = SCHEMA_TOOL_NAME if schema else None
        return "POST", "/messages", body

    def parse_response(self, payload: Any, *, http_status: int) -> Response:
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        schema_tool = getattr(self, "_schema_tool_name", None)

        for block in payload.get("content") or []:
            block_type = block.get("type")
            if block_type == "text":
                text_parts.append(block.get("text", ""))
            elif block_type == "tool_use":
                name = block.get("name", "")
                arguments = block.get("input")
                if schema_tool and name == schema_tool:
                    # Forced-schema tool: its input *is* the answer text.
                    import json as _json

                    text_parts.append(_json.dumps(arguments, ensure_ascii=False))
                    continue
                tool_calls.append(
                    ToolCall(name=name, arguments=arguments, id=block.get("id"))
                )

        usage = payload.get("usage") or {}
        stop_reason = payload.get("stop_reason")
        finish_reason = {
            "end_turn": "stop",
            "max_tokens": "length",
            "stop_sequence": "stop",
            "tool_use": "tool_calls",
            "refusal": "refusal",
        }.get(stop_reason, stop_reason)

        error = None
        if stop_reason == "refusal":
            error = "provider refusal"

        return Response(
            text="".join(text_parts),
            tool_calls=tool_calls,
            tokens_in=usage.get("input_tokens"),
            tokens_out=usage.get("output_tokens"),
            tokens_cached=usage.get("cache_read_input_tokens"),
            finish_reason=finish_reason,
            error=error,
            http_status=http_status,
            structured_output_mechanism=getattr(self, "_mechanism", None),
            translation_warnings=list(getattr(self, "_translation_warnings", [])),
            model=payload.get("model") or self.model,
        )
