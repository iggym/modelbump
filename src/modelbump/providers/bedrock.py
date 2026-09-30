"""AWS Bedrock Converse adapter (F-PROV-1).

Requires the ``[bedrock]`` extra (``botocore``) for SigV4 signing. When the extra
is absent the provider raises a clear, actionable error rather than silently
falling back to an unsigned request.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from modelbump.errors import ProviderError
from modelbump.providers.base import BaseProvider, Response, ToolCall
from modelbump.providers.translate import to_bedrock_tools


class BedrockProvider(BaseProvider):
    name = "bedrock"

    def __init__(self, spec, **kwargs: Any) -> None:
        super().__init__(spec, **kwargs)
        self.region = (
            self.extra.get("region") or os.environ.get("AWS_REGION") or "us-east-1"
        )
        if not self.base_url or self.base_url == "https://":
            self.base_url = f"https://bedrock-runtime.{self.region}.amazonaws.com"
        self._validate_extra()

    def _validate_extra(self) -> None:
        try:
            import botocore  # noqa: F401
        except ImportError as exc:
            raise ProviderError(
                "the bedrock provider needs the optional dependency: "
                "pip install 'modelbump[bedrock]'"
            ) from exc

    def default_base_url(self) -> str:
        region = os.environ.get("AWS_REGION", "us-east-1")
        return f"https://bedrock-runtime.{region}.amazonaws.com"

    def resolve_api_key(self) -> str | None:
        return os.environ.get("AWS_ACCESS_KEY_ID")

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
        bedrock_messages = []
        for message in messages:
            role = message.get("role", "user")
            role = "assistant" if role == "assistant" else "user"
            content = message.get("content")
            if not isinstance(content, str):
                content = str(content)
            bedrock_messages.append({"role": role, "content": [{"text": content}]})

        body: dict[str, Any] = {"messages": bedrock_messages}
        if system:
            body["system"] = [{"text": system}]

        inference_config: dict[str, Any] = {}
        if params.get("temperature") is not None:
            inference_config["temperature"] = params["temperature"]
        if params.get("max_tokens") is not None:
            inference_config["maxTokens"] = params["max_tokens"]
        if params.get("top_p") is not None:
            inference_config["topP"] = params["top_p"]
        if params.get("stop") is not None:
            stop = params["stop"]
            inference_config["stopSequences"] = stop if isinstance(stop, list) else [stop]
        if inference_config:
            body["inferenceConfig"] = inference_config

        warnings: list[str] = []
        mechanism = None
        tool_config: dict[str, Any] = {"tools": []}
        if tools:
            translated, tool_warnings = to_bedrock_tools(tools)
            warnings.extend(tool_warnings)
            tool_config["tools"].extend(translated)

        if schema:
            tool_config["tools"].append(
                {
                    "toolSpec": {
                        "name": "modelbump_structured_output",
                        "description": "Return the answer as structured data.",
                        "inputSchema": {"json": schema},
                    }
                }
            )
            tool_config["toolChoice"] = {"tool": {"name": "modelbump_structured_output"}}
            mechanism = "tool_forcing"

        if tool_config["tools"]:
            body["toolConfig"] = tool_config

        self._mechanism = mechanism
        self._translation_warnings = warnings
        self._schema_tool = "modelbump_structured_output" if schema else None
        path = f"/model/{self.model}/converse"
        return "POST", path, body

    async def complete(self, **kwargs: Any) -> Response:
        """Bedrock needs SigV4, so we sign here and delegate the send."""
        method, path, body = self.build_request(**kwargs)
        url = f"{self.base_url}{path}"
        try:
            signed_headers = self._sign(url, body)
        except Exception as exc:
            return Response(error=f"failed to sign Bedrock request: {exc}", model=self.model)

        import time

        import httpx

        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                http_response = await client.post(url, json=body, headers=signed_headers)
        except httpx.HTTPError as exc:
            return Response(error=f"{type(exc).__name__}: {exc}", model=self.model)
        latency_ms = (time.perf_counter() - started) * 1000

        try:
            payload = http_response.json()
        except ValueError:
            payload = None
        if http_response.status_code >= 400 or payload is None:
            message = ""
            if isinstance(payload, dict):
                message = str(payload.get("message") or payload)
            return Response(
                error=f"HTTP {http_response.status_code}: {message}"[:600],
                http_status=http_response.status_code,
                latency_ms=latency_ms,
                model=self.model,
            )
        response = self.parse_response(payload, http_status=http_response.status_code)
        response.latency_ms = latency_ms
        response.model = self.model
        if self.keep_raw:
            response.raw = payload
        return response

    def _sign(self, url: str, body: dict[str, Any]) -> dict[str, str]:
        import botocore.session
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest

        session = botocore.session.get_session()
        credentials = session.get_credentials()
        if credentials is None:
            raise ProviderError(
                "no AWS credentials found. Set AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY "
                "or configure a profile."
            )
        frozen = credentials.get_frozen_credentials()
        request = AWSRequest(
            method="POST",
            url=url,
            data=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )
        SigV4Auth(frozen, "bedrock", self.region).add_auth(request)
        return dict(request.headers.items())

    def parse_response(self, payload: Any, *, http_status: int) -> Response:
        output = payload.get("output") or {}
        message = output.get("message") or {}
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        schema_tool = getattr(self, "_schema_tool", None)
        import json as _json

        for block in message.get("content") or []:
            if "text" in block:
                text_parts.append(block["text"])
            if "toolUse" in block:
                call = block["toolUse"]
                name = call.get("name", "")
                arguments = call.get("input")
                if schema_tool and name == schema_tool:
                    text_parts.append(_json.dumps(arguments, ensure_ascii=False))
                    continue
                tool_calls.append(
                    ToolCall(name=name, arguments=arguments, id=call.get("toolUseId"))
                )

        usage = payload.get("usage") or {}
        stop_reason = payload.get("stopReason")
        finish_reason = {
            "end_turn": "stop",
            "max_tokens": "length",
            "stop_sequence": "stop",
            "tool_use": "tool_calls",
            "content_filtered": "refusal",
            "guardrail_intervened": "refusal",
        }.get(stop_reason, stop_reason)

        return Response(
            text="".join(text_parts),
            tool_calls=tool_calls,
            tokens_in=usage.get("inputTokens"),
            tokens_out=usage.get("outputTokens"),
            tokens_cached=usage.get("cacheReadInputTokens"),
            finish_reason=finish_reason,
            http_status=http_status,
            structured_output_mechanism=getattr(self, "_mechanism", None),
            translation_warnings=list(getattr(self, "_translation_warnings", [])),
            model=self.model,
        )


__all__ = ["BedrockProvider", "asyncio"]
