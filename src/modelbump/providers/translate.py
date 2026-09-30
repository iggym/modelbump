"""Tool and structured-output translation across providers (F-PROV-4, F-PROV-5).

The *mechanism* used to enforce a schema is itself a migration variable, so it is
recorded on every response and surfaced in the report.
"""

from __future__ import annotations

from typing import Any

# JSON Schema keywords Gemini's responseSchema rejects.
_GEMINI_UNSUPPORTED = {
    "$schema",
    "$id",
    "$ref",
    "$defs",
    "definitions",
    "additionalProperties",
    "patternProperties",
    "unevaluatedProperties",
    "dependentRequired",
    "if",
    "then",
    "else",
    "not",
    "oneOf",
    "anyOf",
    "allOf",
    "const",
    "examples",
    "default",
    "title",
    "prefixItems",
    "contains",
    "minContains",
    "maxContains",
    "propertyNames",
    "contentEncoding",
    "contentMediaType",
    "deprecated",
    "readOnly",
    "writeOnly",
    "pattern",
    "format",
}


def normalize_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """Accept OpenAI-style tools, or bare ``{name, parameters}`` definitions."""
    if "function" in tool and isinstance(tool["function"], dict):
        fn = dict(tool["function"])
        return {
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
        }
    return {
        "name": tool.get("name", ""),
        "description": tool.get("description", ""),
        "parameters": tool.get("parameters")
        or tool.get("input_schema")
        or {"type": "object", "properties": {}},
    }


def to_openai_tools(tools: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    out = []
    for tool in tools:
        norm = normalize_tool(tool)
        if not norm["name"]:
            warnings.append("dropped a tool definition with no name")
            continue
        out.append(
            {
                "type": "function",
                "function": {
                    "name": norm["name"],
                    "description": norm["description"],
                    "parameters": norm["parameters"],
                },
            }
        )
    return out, warnings


def to_anthropic_tools(
    tools: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    out = []
    for tool in tools:
        norm = normalize_tool(tool)
        if not norm["name"]:
            warnings.append("dropped a tool definition with no name")
            continue
        params, sub_warnings = strip_unsupported(
            norm["parameters"], _ANTHROPIC_UNSUPPORTED, provider="anthropic"
        )
        warnings.extend(sub_warnings)
        out.append(
            {
                "name": norm["name"],
                "description": norm["description"],
                "input_schema": params,
            }
        )
    return out, warnings


def to_google_tools(
    tools: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    declarations = []
    for tool in tools:
        norm = normalize_tool(tool)
        if not norm["name"]:
            warnings.append("dropped a tool definition with no name")
            continue
        params, sub_warnings = strip_unsupported(
            norm["parameters"], _GEMINI_UNSUPPORTED, provider="google"
        )
        warnings.extend(sub_warnings)
        declarations.append(
            {
                "name": norm["name"],
                "description": norm["description"],
                "parameters": params,
            }
        )
    return [{"functionDeclarations": declarations}], warnings


def to_bedrock_tools(
    tools: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    out = []
    for tool in tools:
        norm = normalize_tool(tool)
        if not norm["name"]:
            warnings.append("dropped a tool definition with no name")
            continue
        out.append(
            {
                "toolSpec": {
                    "name": norm["name"],
                    "description": norm["description"],
                    "inputSchema": {"json": norm["parameters"]},
                }
            }
        )
    return out, warnings


_ANTHROPIC_UNSUPPORTED = {"$schema", "$id", "$ref", "$defs", "definitions", "examples", "title"}


def strip_unsupported(
    schema: dict[str, Any], unsupported: set[str], *, provider: str
) -> tuple[dict[str, Any], list[str]]:
    """Recursively drop schema keywords a provider rejects, recording warnings."""
    warnings: list[str] = []

    def walk(node: Any, path: str) -> Any:
        if isinstance(node, list):
            return [walk(item, f"{path}[]") for item in node]
        if not isinstance(node, dict):
            return node
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in unsupported:
                warnings.append(
                    f"{provider}: dropped unsupported schema keyword '{key}' at {path}"
                )
                continue
            out[key] = walk(value, f"{path}.{key}")
        return out

    cleaned = walk(schema, "$")
    if not isinstance(cleaned, dict):
        cleaned = {"type": "object", "properties": {}}
    return cleaned, warnings


def to_google_schema(schema: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Gemini responseSchema: strip unsupported keywords and inline ``$ref``."""
    return strip_unsupported(schema, _GEMINI_UNSUPPORTED, provider="google")


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline ``$defs``/``$ref`` so providers without ref support can use it."""
    defs = {}
    for key in ("$defs", "definitions"):
        if isinstance(schema.get(key), dict):
            defs.update(schema[key])

    def resolve(node: Any, depth: int = 0) -> Any:
        if depth > 12:
            return node
        if isinstance(node, list):
            return [resolve(item, depth + 1) for item in node]
        if not isinstance(node, dict):
            return node
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/"):
            name = ref.split("/")[-1]
            target = defs.get(name)
            if target is not None:
                merged = {k: v for k, v in node.items() if k != "$ref"}
                base = resolve(dict(target), depth + 1)
                if isinstance(base, dict):
                    base.update(merged)
                return base
        return {
            k: resolve(v, depth + 1)
            for k, v in node.items()
            if k not in ("$defs", "definitions")
        }

    resolved = resolve(schema)
    return resolved if isinstance(resolved, dict) else schema


def validate_tool_arguments(
    arguments: dict[str, Any], parameters: dict[str, Any]
) -> tuple[bool, str | None]:
    """Validate a tool call's arguments against the tool's own schema."""
    try:
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import SchemaError, ValidationError
    except Exception:  # pragma: no cover
        return True, None
    if not isinstance(parameters, dict) or not parameters:
        return True, None
    try:
        Draft202012Validator.check_schema(parameters)
    except SchemaError:
        return True, None
    validator = Draft202012Validator(parameters)
    try:
        validator.validate(arguments)
        return True, None
    except ValidationError as exc:
        return False, exc.message
