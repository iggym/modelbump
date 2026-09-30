"""The ``mock:`` provider (F-PROV-7).

Deterministic and fully offline. The same case always produces the same output
for a given variant, which is what makes "identical model ⇒ zero drift" a
testable invariant rather than a hope.

Variants
--------
``stable``      Echoes the expectation faithfully. The control condition.
``drifty``      Behaves like a plausible-but-wrong model upgrade: drops JSON
                keys, reorders and rewords prose, picks the wrong tool, and
                occasionally refuses. This is what a real regression looks like.
``verbose``     Correct content, roughly 2.5x the length — exercises the
                ``length`` flag without semantic drift.
``strict-json`` Always emits clean, fence-free JSON matching the schema.
``refusenik``   Refuses everything — exercises the ``refusal`` flag.
``broken``      Returns HTTP 500 on every call — exercises error handling and
                the ``errors`` flag.
``slow``        Adds a deterministic 400ms sleep — exercises the ``latency`` flag.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any

from modelbump.providers.base import BaseProvider, ModelSpec, Response, ToolCall

VARIANTS = ("stable", "drifty", "verbose", "strict-json", "refusenik", "broken", "slow")

_REFUSAL_TEXT = (
    "I'm sorry, but I can't help with that request. It falls outside what I'm "
    "able to assist with."
)


def _seed(*parts: Any) -> int:
    blob = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return int(hashlib.sha256(blob.encode("utf-8")).hexdigest()[:8], 16)


def _merge_expected(value: Any, expected: Any) -> Any:
    """Overlay an expected JSON subset onto a schema-satisfying value.

    A faithful model satisfies the schema *and* the expectation, so the
    expectation overrides matching keys instead of replacing the whole object.
    """
    if isinstance(value, dict) and isinstance(expected, dict):
        merged = dict(value)
        for key, sub in expected.items():
            merged[key] = _merge_expected(merged.get(key), sub) if key in merged else sub
        return merged
    return expected


_JUDGE_MARKER = "Reply with a single JSON object"
_FIRST_RE = re.compile(r"FIRST OUTPUT:\n(.*?)(?:\n\nSECOND OUTPUT:|\Z)", re.DOTALL)
_SECOND_RE = re.compile(
    r"SECOND OUTPUT:\n(.*?)(?:\n\nReply with a single JSON object|\Z)", re.DOTALL
)


def _is_judge_prompt(system: str | None, messages: list[dict[str, Any]]) -> bool:
    if system and _JUDGE_MARKER in system:
        return True
    for message in messages:
        content = message.get("content")
        if isinstance(content, str) and "FIRST OUTPUT:" in content and "SECOND OUTPUT:" in content:
            return True
    return False


def _judge_reply(messages: list[dict[str, Any]]) -> str:
    """A deterministic, position-consistent judge answer for the mock.

    Compares the two outputs by length and content. Because it is a fixed
    function of the two outputs (not of their order), swapping positions yields
    a consistent verdict rather than a spurious disagreement.
    """
    text = ""
    for message in messages:
        if isinstance(message.get("content"), str):
            text = message["content"]
            break
    first_match = _FIRST_RE.search(text)
    second_match = _SECOND_RE.search(text)
    if not first_match or not second_match:
        return json.dumps({"equivalent": True, "better": "tie", "reason": "no outputs found"})
    first = first_match.group(1).strip()
    second = second_match.group(1).strip()
    equivalent = first == second
    if equivalent:
        better = "tie"
        reason = "the two outputs are identical"
    elif len(first) == len(second):
        better = "tie"
        reason = "different wording, similar substance"
    else:
        better = "first" if len(first) < len(second) else "second"
        reason = "the shorter output is more direct"
    return json.dumps({"equivalent": equivalent, "better": better, "reason": reason})


class MockProvider(BaseProvider):
    name = "mock"

    def __init__(self, spec: ModelSpec, **kwargs: Any) -> None:
        self.variant = self._variant_from(spec.model)
        kwargs["api_key"] = kwargs.get("api_key") or "mock"
        super().__init__(spec, **kwargs)
        self._last_request: dict[str, Any] = {}

    @staticmethod
    def _variant_from(model: str) -> str:
        bare = model.split(":", 1)[-1]
        if bare in VARIANTS:
            return bare
        for variant in VARIANTS:
            if bare.endswith(variant) or bare.startswith(variant):
                return variant
        return "stable"

    def default_base_url(self) -> str:
        return "mock://offline"

    def auth_headers(self) -> dict[str, str]:
        return {}

    def build_request(
        self,
        *,
        messages: list[dict[str, Any]],
        system: str | None,
        tools: list[dict[str, Any]] | None,
        schema: dict[str, Any] | None,
        params: dict[str, Any],
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        # The mock never issues HTTP; it records the request for its own use.
        self._last_request = {
            "messages": messages,
            "system": system,
            "tools": tools,
            "schema": schema,
            "params": params,
        }
        return "POST", "/mock", self._last_request

    def parse_response(self, payload: Any, *, http_status: int) -> Response:  # pragma: no cover
        raise NotImplementedError("mock provider overrides complete()")

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        schema: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Response:
        params = dict(params or {})
        request = {
            "messages": messages,
            "system": system,
            "tools": tools,
            "schema": schema,
            "params": params,
        }
        self._last_request = request
        seed = _seed(self.variant, messages, system, schema, tools, params)
        sample_index = int(params.pop("_sample", 0) or 0)

        if self.limiter is not None:
            await self.limiter.acquire(64)

        # The judge prompt is recognisable; answer it deterministically so the
        # position-swap logic is genuinely exercised offline.
        if _is_judge_prompt(system, messages):
            return self._finalise(
                Response(text=_judge_reply(messages), finish_reason="stop"), seed, params
            )

        if self.variant == "broken":
            return Response(
                error="HTTP 500: mock provider 'broken' always fails",
                http_status=500,
                latency_ms=12.0,
                model=self.model,
            )

        if self.variant == "slow":
            await asyncio.sleep(0.4)

        if self.variant == "refusenik":
            return self._finish(_REFUSAL_TEXT, seed, params, finish_reason="stop")

        if self.variant == "drifty":
            return self._drifty(request, seed, sample_index, params)

        if self.variant == "verbose":
            base = self._faithful(request, seed, sample_index)
            base.text = self._expand(base.text)
            base.finish_reason = "stop"
            return self._finalise(base, seed, params)

        if self.variant == "strict-json":
            base = self._faithful(request, seed, sample_index)
            base.text = self._as_strict_json(base.text, schema)
            base.finish_reason = "stop"
            return self._finalise(base, seed, params)

        # stable
        base = self._faithful(request, seed, sample_index)
        return self._finalise(base, seed, params)

    # -- construction helpers -------------------------------------------
    def _finalise(self, response: Response, seed: int, params: dict[str, Any]) -> Response:
        text = response.text
        response.tokens_in = max(1, len(json.dumps(self._last_request, default=str)) // 4)
        response.tokens_out = max(1, len(text) // 4)
        response.tokens_cached = response.tokens_in // 4 if params.get("_cacheable") else 0
        response.finish_reason = response.finish_reason or "stop"
        response.structured_output_mechanism = (
            "mock-json_schema" if self._last_request.get("schema") else None
        )
        if response.latency_ms is None:
            # Deterministic per-case latency so the latency flag is exercisable.
            response.latency_ms = float(90 + (seed % 260))
            if self.variant == "slow":
                response.latency_ms += 400.0
        response.model = self.model
        return response

    def _finish(
        self, text: str, seed: int, params: dict[str, Any], *, finish_reason: str
    ) -> Response:
        response = Response(text=text, finish_reason=finish_reason)
        return self._finalise(response, seed, params)

    def _faithful(self, request: dict[str, Any], seed: int, sample_index: int) -> Response:
        schema = request.get("schema")
        tools = request.get("tools")
        expected = request.get("params", {}).get("_expected")

        if isinstance(expected, dict) and "regex" in expected:
            # Emit something that matches the requested regex where we can.
            pattern = str(expected["regex"])
            if "cannot" in pattern or "refuse" in pattern:
                return Response(text=_REFUSAL_TEXT)
            return Response(text="Here is the answer you asked for.")

        if schema:
            payload = self._synthesise(schema, seed)
            # A faithful model satisfies the schema *and* the expectation, so
            # overlay any expected JSON subset rather than returning it alone.
            if isinstance(expected, dict):
                payload = _merge_expected(payload, expected)
            return Response(text=json.dumps(payload, ensure_ascii=False))

        if expected is not None:
            if isinstance(expected, list):
                return Response(text=" ".join(str(e) for e in expected))
            if isinstance(expected, dict):
                return Response(text=json.dumps(expected, ensure_ascii=False))
            return Response(text=str(expected))

        if tools:
            first = tools[0]
            fn = first.get("function", first)
            name = fn.get("name", "tool")
            args = self._synthesise(
                fn.get("parameters") or {"type": "object", "properties": {}}, seed
            )
            return Response(
                text="",
                tool_calls=[ToolCall(name=name, arguments=args, id=f"call_{seed % 10**6:06d}")],
            )

        return Response(text=self._generic_text(request))

    def _drifty(self, request: dict[str, Any], seed: int, sample_index: int, params: dict[str, Any]) -> Response:
        """A plausible regression: content changes, structure degrades."""
        schema = request.get("schema")
        tools = request.get("tools")
        expected = request.get("params", {}).get("_expected")
        text_input = self._input_text(request)

        # ~1 in 6 cases refuses after the upgrade. Deterministic per case.
        if seed % 6 == 0:
            return self._finish(_REFUSAL_TEXT, seed, params, finish_reason="stop")

        # Tool cases: pick a different tool than the expected one.
        if tools:
            names = []
            for tool in tools:
                fn = tool.get("function", tool)
                names.append(fn.get("name", ""))
            if names:
                chosen = names[1 % len(names)] if len(names) > 1 else names[0]
                fn = tools[names.index(chosen)].get("function", tools[names.index(chosen)])
                args = self._synthesise(
                    fn.get("parameters") or {"type": "object", "properties": {}}, seed
                )
                # Drop one required argument to trip tool_args.
                for key in list(args):
                    if len(args) > 1:
                        del args[key]
                        break
                return self._finalise(
                    Response(
                        text="",
                        tool_calls=[ToolCall(name=chosen, arguments=args, id=f"call_{seed % 10**6:06d}")],
                    ),
                    seed,
                    params,
                )

        if schema:
            payload = self._synthesise(schema, seed)
            # Drop a key and perturb a value — the classic schema-migration break.
            if isinstance(payload, dict) and payload:
                keys = sorted(payload)
                if len(keys) > 1:
                    payload.pop(keys[0], None)
                for key in list(payload):
                    if isinstance(payload[key], str):
                        payload[key] = payload[key].upper()
                        break
                    if isinstance(payload[key], (int, float)) and not isinstance(payload[key], bool):
                        payload[key] = round(float(payload[key]) * 1.37, 2)
                        break
            # Wrap in a fence about half the time: strict_json regression.
            text = json.dumps(payload, ensure_ascii=False)
            if seed % 2 == 0:
                text = f"```json\n{text}\n```"
            else:
                text = f"Here is the extracted data:\n{text}\nLet me know if you need changes."
            return self._finish(text, seed, params, finish_reason="stop")

        if expected is not None:
            if isinstance(expected, dict) and "regex" in expected:
                return self._finish(
                    "I'd rather not answer that one, sorry.", seed, params, finish_reason="stop"
                )
            if isinstance(expected, list):
                # Keep one expected token, drop the rest, add filler.
                kept = [str(expected[0])] if expected else []
                return self._finish(
                    "In short: " + " ".join(kept) + " — plus some extra commentary that "
                    "was not requested.",
                    seed,
                    params,
                    finish_reason="stop",
                )
            if isinstance(expected, dict):
                trimmed = dict(expected)
                for key in list(trimmed)[:1]:
                    trimmed.pop(key, None)
                return self._finish(
                    json.dumps(trimmed, ensure_ascii=False), seed, params, finish_reason="stop"
                )
            # Free text: reword heavily so similarity drops below the noise floor.
            return self._finish(
                f"Rewritten answer: {str(expected)[::-1][:40]} ... (the upgraded model "
                f"phrases this quite differently)",
                seed,
                params,
                finish_reason="stop",
            )

        # Unconstrained free text: produce something lexically distant.
        words = re.findall(r"[A-Za-z']+", text_input)
        scrambled = " ".join(w[::-1] for w in words[:40]) or "an entirely different answer"
        return self._finish(
            f"An alternative take: {scrambled}", seed, params, finish_reason="stop"
        )

    def _generic_text(self, request: dict[str, Any]) -> str:
        text = self._input_text(request)
        words = re.findall(r"[A-Za-z0-9']+", text)
        return " ".join(words[:60]) if words else "ok"

    def _input_text(self, request: dict[str, Any]) -> str:
        parts = []
        if request.get("system"):
            parts.append(str(request["system"]))
        for message in request.get("messages") or []:
            content = message.get("content")
            if isinstance(content, str):
                parts.append(content)
            else:
                parts.append(json.dumps(content, ensure_ascii=False, default=str))
        return "\n".join(parts)

    def _synthesise(self, schema: dict[str, Any], seed: int) -> Any:
        """Produce a deterministic value satisfying a JSON Schema."""
        if not isinstance(schema, dict):
            return {}
        if "$ref" in schema or "$defs" in schema or "definitions" in schema:
            from modelbump.providers.translate import inline_refs

            schema = inline_refs(schema)

        if "enum" in schema and schema["enum"]:
            return schema["enum"][seed % len(schema["enum"])]
        if "const" in schema:
            return schema["const"]

        for combinator in ("anyOf", "oneOf", "allOf"):
            options = schema.get(combinator)
            if isinstance(options, list) and options:
                if combinator == "allOf":
                    merged: dict[str, Any] = {}
                    for option in options:
                        value = self._synthesise(option, seed)
                        if isinstance(value, dict):
                            merged.update(value)
                    return merged
                return self._synthesise(options[seed % len(options)], seed)

        stype = schema.get("type")
        if isinstance(stype, list):
            stype = next((t for t in stype if t != "null"), stype[0] if stype else "string")

        if stype == "object" or "properties" in schema:
            out: dict[str, Any] = {}
            for key, subschema in (schema.get("properties") or {}).items():
                out[key] = self._synthesise(subschema, seed + len(key))
            return out
        if stype == "array":
            items = schema.get("items") or {"type": "string"}
            count = max(1, min(3, schema.get("minItems", 1) or 1))
            return [self._synthesise(items, seed + i) for i in range(count)]
        if stype == "integer":
            return 1 + (seed % 100)
        if stype == "number":
            return round(1.0 + (seed % 1000) / 10.0, 2)
        if stype == "boolean":
            return bool(seed % 2)
        if stype == "null":
            return None
        return f"value-{seed % 1000}"

    def _expand(self, text: str) -> str:
        """Roughly 2.5x the length, same meaning — trips the length flag only."""
        filler = (
            " To add some context that was not strictly necessary: this response has "
            "been expanded by the newer model, which tends to be considerably more "
            "talkative than its predecessor across a wide range of prompts."
        )
        if not text:
            return filler.strip()
        return text + filler

    def _as_strict_json(self, text: str, schema: dict[str, Any] | None) -> str:
        stripped = text.strip()
        fence = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.DOTALL)
        if fence:
            stripped = fence.group(1).strip()
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = self._synthesise(schema or {"type": "object", "properties": {}}, 0)
        return json.dumps(parsed, ensure_ascii=False)


def mock_behaviour_table() -> list[dict[str, str]]:
    """Documented behaviour so users can reason about the demo (F-PROV-7)."""
    return [
        {"variant": "stable", "behaviour": "Faithful to the expectation. Drift 0."},
        {"variant": "drifty", "behaviour": "Drops JSON keys, rewords prose, picks the wrong tool, sometimes refuses."},
        {"variant": "verbose", "behaviour": "Correct content, ~2.5x length. Length flag only."},
        {"variant": "strict-json", "behaviour": "Clean fence-free JSON matching the schema."},
        {"variant": "refusenik", "behaviour": "Refuses every prompt."},
        {"variant": "broken", "behaviour": "HTTP 500 on every call."},
        {"variant": "slow", "behaviour": "Adds a fixed 400ms delay."},
    ]
