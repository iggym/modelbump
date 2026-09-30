"""The Case model — one behavioral test case for a model migration diff."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

KNOWN_FIELDS = {
    "id",
    "tags",
    "system",
    "input",
    "expected",
    "schema",
    "tools",
    "expect_tool",
    "params",
    "meta",
}

# Fields a case may declare that are not part of the native schema but are
# understood by importers (recorded in meta rather than rejected).
IMPORT_HINTS = {"expected_output", "assert", "vars", "description"}

_MAX_INPUT_CHARS = 200_000


def stable_id(payload: Any) -> str:
    """A stable, content-addressed id for a case without an explicit one."""
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return "case-" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


@dataclass
class Case:
    """A single behavioral test case.

    Only ``input`` is required. ``input`` is either a string (single user turn)
    or a list of ``{role, content}`` messages (multi-turn).
    """

    input: str | list[dict[str, Any]]
    id: str = ""
    tags: list[str] = field(default_factory=list)
    system: str | None = None
    expected: str | list[str] | dict[str, Any] | None = None
    schema: dict[str, Any] | None = None
    tools: list[dict[str, Any]] | None = None
    expect_tool: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            self.id = stable_id(
                {"input": self.input, "system": self.system, "schema": self.schema}
            )

    # -- derived helpers -------------------------------------------------
    @property
    def is_multiturn(self) -> bool:
        return isinstance(self.input, list)

    @property
    def messages(self) -> list[dict[str, Any]]:
        if isinstance(self.input, str):
            return [{"role": "user", "content": self.input}]
        return [dict(m) for m in self.input]

    @property
    def flat_input(self) -> str:
        """All input text concatenated — used for fingerprints and length checks."""
        if isinstance(self.input, str):
            return self.input
        parts = []
        for m in self.input:
            content = m.get("content")
            if isinstance(content, str):
                parts.append(content)
            else:
                parts.append(json.dumps(content, ensure_ascii=False, default=str))
        return "\n".join(parts)

    @property
    def input_chars(self) -> int:
        return len(self.flat_input)

    def fingerprint(self) -> str:
        """Content fingerprint used for cache keys (excludes cosmetic meta)."""
        payload = {
            "id": self.id,
            "system": self.system,
            "input": self.input,
            "expected": self.expected,
            "schema": self.schema,
            "tools": self.tools,
            "expect_tool": self.expect_tool,
            "params": self.params,
        }
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "input": self.input}
        if self.tags:
            out["tags"] = list(self.tags)
        if self.system is not None:
            out["system"] = self.system
        if self.expected is not None:
            out["expected"] = self.expected
        if self.schema is not None:
            out["schema"] = self.schema
        if self.tools is not None:
            out["tools"] = self.tools
        if self.expect_tool is not None:
            out["expect_tool"] = self.expect_tool
        if self.params:
            out["params"] = self.params
        if self.meta:
            out["meta"] = self.meta
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Case:
        if not isinstance(data, dict):
            raise ValueError(f"case must be a mapping, got {type(data).__name__}")
        if "input" not in data or data["input"] in (None, ""):
            raise ValueError("case is missing required field 'input'")
        kwargs: dict[str, Any] = {}
        for key in KNOWN_FIELDS:
            if key in data:
                kwargs[key] = data[key]
        # Preserve importer-only fields under meta so nothing is silently lost.
        extra = {k: v for k, v in data.items() if k not in KNOWN_FIELDS}
        if extra:
            meta = dict(kwargs.get("meta") or {})
            meta.setdefault("_extra", {}).update(extra)
            kwargs["meta"] = meta
        case = cls(**kwargs)
        return case


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _iter_json_schemas(schema: Any, path: str = "$") -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = [(path, schema)]
    if isinstance(schema, dict):
        for key in ("properties", "$defs", "definitions", "patternProperties"):
            sub = schema.get(key)
            if isinstance(sub, dict):
                for name, child in sub.items():
                    found.extend(_iter_json_schemas(child, f"{path}.{key}.{name}"))
        for key in ("items", "additionalProperties", "not", "contains"):
            sub = schema.get(key)
            if isinstance(sub, dict):
                found.extend(_iter_json_schemas(sub, f"{path}.{key}"))
        for key in ("allOf", "anyOf", "oneOf", "prefixItems"):
            sub = schema.get(key)
            if isinstance(sub, list):
                for i, child in enumerate(sub):
                    found.extend(_iter_json_schemas(child, f"{path}.{key}[{i}]"))
    return found


def validate_case(case: Case) -> list[str]:
    """Return a list of human-readable problems; empty list means valid."""
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError

    problems: list[str] = []

    if not isinstance(case.input, (str, list)):
        problems.append("input must be a string or a list of messages")
    elif isinstance(case.input, list):
        if not case.input:
            problems.append("input message list is empty")
        for i, msg in enumerate(case.input):
            if not isinstance(msg, dict):
                problems.append(f"input[{i}] must be an object")
                continue
            if "role" not in msg:
                problems.append(f"input[{i}] is missing 'role'")
            if "content" not in msg:
                problems.append(f"input[{i}] is missing 'content'")

    if case.input_chars > _MAX_INPUT_CHARS:
        problems.append(
            f"input is oversized ({case.input_chars} chars > {_MAX_INPUT_CHARS})"
        )

    if case.tags is not None and not isinstance(case.tags, list):
        problems.append("tags must be a list of strings")

    if case.expected is not None and not isinstance(
        case.expected, (str, list, dict)
    ):
        problems.append("expected must be a string, list, or object")

    if case.schema is not None:
        if not isinstance(case.schema, dict):
            problems.append("schema must be a JSON Schema object")
        else:
            for path, sub in _iter_json_schemas(case.schema):
                try:
                    Draft202012Validator.check_schema(sub)
                except SchemaError as exc:
                    problems.append(f"invalid JSON schema at {path}: {exc.message}")

    if case.tools is not None:
        if not isinstance(case.tools, list):
            problems.append("tools must be a list")
        else:
            for i, tool in enumerate(case.tools):
                if not isinstance(tool, dict):
                    problems.append(f"tools[{i}] must be an object")
                    continue
                name = tool.get("name") or (tool.get("function") or {}).get("name")
                if not name:
                    problems.append(f"tools[{i}] has no name")

    if case.expect_tool is not None:
        if not isinstance(case.expect_tool, str):
            problems.append("expect_tool must be a string")
        elif case.tools:
            names = {
                (t.get("name") or (t.get("function") or {}).get("name"))
                for t in case.tools
                if isinstance(t, dict)
            }
            if case.expect_tool not in names:
                problems.append(
                    f"expect_tool '{case.expect_tool}' is not among the declared tools"
                )

    if case.params and not isinstance(case.params, dict):
        problems.append("params must be an object")

    return problems


def find_unknown_fields(data: dict[str, Any]) -> list[str]:
    return sorted(k for k in data if k not in KNOWN_FIELDS and k not in IMPORT_HINTS)


_ID_SAFE = re.compile(r"[^a-zA-Z0-9_.:-]+")


def slugify(value: str) -> str:
    return _ID_SAFE.sub("-", value).strip("-").lower() or "case"
