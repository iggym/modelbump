"""OpenAI Responses API adapter (F-PROV-1).

Auto-selected for ``gpt-5*`` and ``o*`` models, overridable with ``--api``.
"""

from __future__ import annotations

from typing import Any

from modelbump.providers.base import BaseProvider, Response, ToolCall
from modelbump.providers.translate import to_openai_tools

RESPONSES_PREFIXES = ("gpt-5", "o1", "o3", "o4", "gpt-4.1")


def prefers_responses_api(model: str) -> bool:
    bare = model.split(":", 1)[-1].split("@", 1)[0].lower()
    return any(bare.startswith(prefix) for prefix in RESPONSES_PREFIXES)


class OpenAIResponsesProvider(BaseProvider):
    name = "openai_responses"

    def default_base_url(self) -> str:
        return "https://api.openai.com/v1"

    def auth_headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.api_key}"} if self.api_key else {}

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
        input_items: list[dict[str, Any]] = []
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content")
            if isinstance(content, str):
                content = [{"type": "input_text", "text": content}]
            elif isinstance(content, list):
                content = [
                    part if isinstance(part, dict) else {"type": "input_text", "text": str(part)}
                    for part in content
                ]
            else:
                content = [{"type": "input_text", "text": str(content)}]
            input_items.append({"role": role, "content": content})

        body: dict[str, Any] = {"model": self.model, "input": input_items}
        if system:
            body["instructions"] = system
        if params.get("temperature") is not None:
            body["temperature"] = params["temperature"]
        if params.get("max_tokens") is not None:
            body["max_output_tokens"] = params["max_tokens"]
        if params.get("top_p") is not None:
            body["top_p"] = params["top_p"]

        warnings: list[str] = []
        if tools:
            translated, warnings = to_openai_tools(tools)
            body["tools"] = [
                {
                    "type": "function",
                    "name": t["function"]["name"],
                    "description": t["function"]["description"],
                    "parameters": t["function"]["parameters"],
                }
                for t in translated
            ]

        mechanism = None
        if schema:
            body["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "modelbump_response",
                    "schema": schema,
                    "strict": False,
                }
            }
            mechanism = "json_schema"
        self._mechanism = mechanism
        self._translation_warnings = warnings
        return "POST", "/responses", body

    def parse_response(self, payload: Any, *, http_status: int) -> Response:
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        refusal = None

        for item in payload.get("output") or []:
            item_type = item.get("type")
            if item_type == "message":
                for part in item.get("content") or []:
                    part_type = part.get("type")
                    if part_type in ("output_text", "text"):
                        text_parts.append(part.get("text", ""))
                    elif part_type == "refusal":
                        refusal = part.get("refusal")
            elif item_type == "function_call":
                tool_calls.append(
                    ToolCall(
                        name=item.get("name", ""),
                        arguments=item.get("arguments"),
                        id=item.get("call_id") or item.get("id"),
                    )
                )

        text = "".join(text_parts)
        if not text and payload.get("output_text"):
            text = payload["output_text"]

        usage = payload.get("usage") or {}
        details = usage.get("input_tokens_details") or {}
        status = payload.get("status")
        finish_reason = status
        incomplete = payload.get("incomplete_details") or {}
        if status == "incomplete" and incomplete.get("reason") == "max_output_tokens":
            finish_reason = "length"

        error = None
        if refusal:
            error = f"provider refusal: {refusal}"[:300]
            text = text or refusal

        return Response(
            text=text,
            tool_calls=tool_calls,
            tokens_in=usage.get("input_tokens"),
            tokens_out=usage.get("output_tokens"),
            tokens_cached=details.get("cached_tokens"),
            finish_reason=finish_reason,
            error=error,
            http_status=http_status,
            structured_output_mechanism=getattr(self, "_mechanism", None),
            translation_warnings=list(getattr(self, "_translation_warnings", [])),
            model=payload.get("model") or self.model,
        )
