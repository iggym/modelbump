"""Import cases from production traces (F-SUITE-4, v0.1 wedge).

Supports:
  * OpenTelemetry GenAI semantic-convention JSONL exports
  * Langfuse JSON and CSV exports

Everything passing through is redacted with the regex family in ``redact.py``.
This is the wedge for the full ``trace2test`` tool planned post-0.1.
"""

from __future__ import annotations

import csv
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from modelbump.case import Case, slugify
from modelbump.errors import SuiteError
from modelbump.suite.redact import RedactionReport, redact, redact_value

# OTel GenAI semantic-convention attribute keys we understand.
_OTEL_INPUT_KEYS = (
    "gen_ai.prompt",
    "gen_ai.request.prompt",
    "gen_ai.content.prompt",
    "input.value",
    "gen_ai.input.messages",
)
_OTEL_SYSTEM_KEYS = (
    "gen_ai.system_instructions",
    "gen_ai.request.system",
    "gen_ai.content.system",
)
_OTEL_TOOLS_KEYS = ("gen_ai.request.tools", "gen_ai.tool.definitions")


@dataclass
class TraceImportResult:
    cases: list[Case] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    redaction: RedactionReport = field(default_factory=lambda: RedactionReport(counts={}))
    source_format: str = "unknown"
    skipped: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_format": self.source_format,
            "imported": len(self.cases),
            "skipped": self.skipped,
            "warnings": self.warnings,
            "redaction": self.redaction.to_dict(),
        }


def load_traces(
    path: str | Path, *, sample: int | None = None, seed: int = 0
) -> TraceImportResult:
    p = Path(path)
    if not p.exists():
        raise SuiteError(f"trace file does not exist: {p}")
    suffix = p.suffix.lower()
    if suffix in (".jsonl", ".ndjson"):
        result = _load_otel_jsonl(p)
    elif suffix == ".json":
        result = _load_langfuse_json(p)
    elif suffix == ".csv":
        result = _load_langfuse_csv(p)
    else:
        raise SuiteError(f"unsupported trace format '{suffix}' for {p}")

    if sample is not None and sample < len(result.cases):
        rng = random.Random(seed)
        result.cases = rng.sample(result.cases, sample)
        result.warnings.append(f"sampled {sample} of {result.skipped + sample} traces (seed={seed})")
    return result


# ---------------------------------------------------------------------------
# OpenTelemetry GenAI
# ---------------------------------------------------------------------------

def _otel_attributes(record: dict[str, Any]) -> dict[str, Any]:
    """Collect attributes from the several shapes an OTel export can take."""
    attrs: dict[str, Any] = {}
    for container in ("attributes", "resource_attributes", "scope_attributes"):
        value = record.get(container)
        if isinstance(value, dict):
            attrs.update(value)
    # Some exporters nest the span under "span".
    span = record.get("span")
    if isinstance(span, dict):
        attrs.update(_otel_attributes(span))
    return attrs


def _messages_from_otel(value: Any) -> list[dict[str, Any]] | str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        messages: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, dict) and "role" in item:
                content = item.get("content") or item.get("parts") or ""
                messages.append({"role": str(item["role"]), "content": content})
            elif isinstance(item, dict) and "body" in item:
                messages.append({"role": "user", "content": item["body"]})
            elif isinstance(item, str):
                messages.append({"role": "user", "content": item})
        return messages or None
    return None


def _load_otel_jsonl(path: Path) -> TraceImportResult:
    result = TraceImportResult(source_format="otel-genai-jsonl")
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            result.warnings.append(f"{path}:{lineno}: skipped (invalid JSON)")
            result.skipped += 1
            continue
        if not isinstance(record, dict):
            result.skipped += 1
            continue
        attrs = _otel_attributes(record)
        case = _otel_record_to_case(record, attrs, result, lineno)
        if case is None:
            result.skipped += 1
        else:
            result.cases.append(case)
    return result


def _otel_record_to_case(
    record: dict[str, Any], attrs: dict[str, Any], result: TraceImportResult, lineno: int
) -> Case | None:
    input_value: Any = None
    for key in _OTEL_INPUT_KEYS:
        if key in attrs:
            input_value = _messages_from_otel(attrs[key])
            if input_value:
                break
    if input_value is None:
        # Fall back to plain "input" / "prompt" keys some exporters emit.
        for key in ("input", "prompt", "gen_ai.prompt.0.content"):
            if key in attrs:
                input_value = attrs[key]
                break
    if input_value in (None, "", []):
        result.warnings.append(f"{path_label(result, lineno)}: skipped (no prompt found)")
        return None

    system = None
    for key in _OTEL_SYSTEM_KEYS:
        if key in attrs and isinstance(attrs[key], str):
            system = attrs[key]
            break

    tools = None
    for key in _OTEL_TOOLS_KEYS:
        value = attrs.get(key)
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                value = None
        if isinstance(value, list):
            tools = value
            break

    model = attrs.get("gen_ai.request.model") or attrs.get("gen_ai.response.model")
    tags = ["traces"]
    if model:
        tags.append(slugify(str(model)))

    meta: dict[str, Any] = {"source": "otel", "line": lineno}
    for key in ("gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens", "gen_ai.operation.name"):
        if key in attrs:
            meta[key] = attrs[key]

    payload = {
        "id": slugify(f"trace-{lineno}-{model or 'unknown'}"),
        "input": input_value,
        "system": system,
        "tools": tools,
        "tags": tags,
        "meta": meta,
    }
    redacted = redact_value(payload, result.redaction)
    try:
        return Case.from_dict(redacted)
    except ValueError as exc:
        result.warnings.append(f"{path_label(result, lineno)}: skipped ({exc})")
        return None


def path_label(result: TraceImportResult, lineno: int) -> str:
    return f"line {lineno}"


# ---------------------------------------------------------------------------
# Langfuse
# ---------------------------------------------------------------------------

def _load_langfuse_json(path: Path) -> TraceImportResult:
    result = TraceImportResult(source_format="langfuse-json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SuiteError(f"{path}: invalid JSON — {exc.msg}") from exc
    records = data.get("data") if isinstance(data, dict) else data
    if not isinstance(records, list):
        raise SuiteError(f"{path}: expected a list of observations (or {{'data': [...]}})")
    for i, record in enumerate(records):
        if not isinstance(record, dict):
            result.skipped += 1
            continue
        case = _langfuse_record_to_case(record, result, i)
        if case is None:
            result.skipped += 1
        else:
            result.cases.append(case)
    return result


def _load_langfuse_csv(path: Path) -> TraceImportResult:
    result = TraceImportResult(source_format="langfuse-csv")
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for i, row in enumerate(reader):
            record = {k: v for k, v in row.items() if k is not None}
            # Langfuse CSV nests JSON in some columns.
            for key in ("input", "output", "metadata", "tools"):
                value = record.get(key)
                if isinstance(value, str) and value[:1] in "[{":
                    try:
                        record[key] = json.loads(value)
                    except json.JSONDecodeError:
                        pass
            case = _langfuse_record_to_case(record, result, i)
            if case is None:
                result.skipped += 1
            else:
                result.cases.append(case)
    return result


def _langfuse_record_to_case(
    record: dict[str, Any], result: TraceImportResult, index: int
) -> Case | None:
    input_value = record.get("input") or record.get("prompt")
    if isinstance(input_value, dict):
        # Langfuse chat-style input: {"messages": [...]} or {"role":..., "content":...}
        if "messages" in input_value and isinstance(input_value["messages"], list):
            input_value = input_value["messages"]
        elif "content" in input_value:
            input_value = [
                {"role": str(input_value.get("role", "user")), "content": input_value["content"]}
            ]
    if input_value in (None, "", []):
        result.warnings.append(f"record {index}: skipped (no input)")
        return None

    metadata = record.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {"raw": metadata}

    tags = record.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.replace(";", ",").split(",") if t.strip()]
    tags = ["traces", *[str(t) for t in tags]]

    system = None
    if isinstance(metadata, dict):
        system = metadata.get("system") or metadata.get("system_prompt")

    tools = record.get("tools")
    if not isinstance(tools, list):
        tools = None

    model = record.get("model")
    if model:
        tags.append(slugify(str(model)))

    meta: dict[str, Any] = {"source": "langfuse", "index": index}
    if isinstance(metadata, dict):
        for key in ("user_id", "session_id", "environment", "release"):
            if key in metadata:
                meta[key] = metadata[key]

    payload = {
        "id": slugify(f"trace-{index}-{record.get('id') or model or 'unknown'}"),
        "input": input_value,
        "system": system,
        "tools": tools,
        "tags": tags,
        "meta": meta,
    }
    redacted = redact_value(payload, result.redaction)
    try:
        return Case.from_dict(redacted)
    except ValueError as exc:
        result.warnings.append(f"record {index}: skipped ({exc})")
        return None


def write_jsonl(cases: list[Case], path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        for case in cases:
            fh.write(json.dumps(case.to_dict(), ensure_ascii=False) + "\n")
    return p


def redact_text(text: str) -> str:
    return redact(text)
