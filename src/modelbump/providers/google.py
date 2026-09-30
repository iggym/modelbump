"""Google Gemini ``generateContent`` adapter (F-PROV-1, F-PROV-4)."""

from __future__ import annotations

import os
from typing import Any

from modelbump.providers.base import BaseProvider, Response, ToolCall
from modelbump.providers.translate import to_google_schema, to_google_tools


class GoogleProvider(BaseProvider):
    name = "google"

    def default_base_url(self) -> str:
        return os.environ.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")

    def resolve_api_key(self) -> str | None:
        return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")

    def auth_headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self.api_key} if self.api_key else {}

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
        contents = []
        for message in messages:
            role = message.get("role", "user")
            role = "model" if role == "assistant" else role
            content = message.get("content")
            if not isinstance(content, str):
                content = str(content)
            contents.append({"role": role, "parts": [{"text": content}]})

        body: dict[str, Any] = {"contents": contents}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        generation_config: dict[str, Any] = {}
        if params.get("temperature") is not None:
            generation_config["temperature"] = params["temperature"]
        if params.get("max_tokens") is not None:
            generation_config["maxOutputTokens"] = params["max_tokens"]
        if params.get("top_p") is not None:
            generation_config["topP"] = params["top_p"]
        if params.get("stop") is not None:
            stop = params["stop"]
            generation_config["stopSequences"] = stop if isinstance(stop, list) else [stop]

        warnings: list[str] = []
        mechanism = None
        if schema:
            cleaned, schema_warnings = to_google_schema(schema)
            warnings.extend(schema_warnings)
            generation_config["responseMimeType"] = "application/json"
            generation_config["responseSchema"] = cleaned
            mechanism = "responseSchema"

        if generation_config:
            body["generationConfig"] = generation_config

        if tools:
            translated, tool_warnings = to_google_tools(tools)
            warnings.extend(tool_warnings)
            body["tools"] = translated

        self._mechanism = mechanism
        self._translation_warnings = warnings
        path = f"/models/{self.model}:generateContent"
        return "POST", path, body

    def parse_response(self, payload: Any, *, http_status: int) -> Response:
        candidates = payload.get("candidates") or []
        if not candidates:
            feedback = payload.get("promptFeedback") or {}
            reason = feedback.get("blockReason")
            if reason:
                return Response(
                    error=f"blocked by provider safety filter: {reason}",
                    http_status=http_status,
                    finish_reason="refusal",
                )
            return Response(error="provider returned no candidates", http_status=http_status)

        candidate = candidates[0]
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for part in (candidate.get("content") or {}).get("parts") or []:
            if "text" in part:
                text_parts.append(part["text"])
            if "functionCall" in part:
                call = part["functionCall"]
                tool_calls.append(
                    ToolCall(name=call.get("name", ""), arguments=call.get("args"))
                )

        usage = payload.get("usageMetadata") or {}
        finish_reason = {
            "STOP": "stop",
            "MAX_TOKENS": "length",
            "SAFETY": "refusal",
            "RECITATION": "refusal",
            "PROHIBITED_CONTENT": "refusal",
        }.get(candidate.get("finishReason"), candidate.get("finishReason"))

        return Response(
            text="".join(text_parts),
            tool_calls=tool_calls,
            tokens_in=usage.get("promptTokenCount"),
            tokens_out=usage.get("candidatesTokenCount"),
            tokens_cached=usage.get("cachedContentTokenCount"),
            finish_reason=finish_reason,
            http_status=http_status,
            structured_output_mechanism=getattr(self, "_mechanism", None),
            translation_warnings=list(getattr(self, "_translation_warnings", [])),
            model=payload.get("modelVersion") or self.model,
        )
