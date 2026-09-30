"""Suite loaders: native JSONL/JSON/YAML/CSV, promptfoo, DeepEval/OpenAI Evals,
and directories of any of these (F-SUITE-1).
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from modelbump.case import Case, find_unknown_fields, slugify
from modelbump.errors import SuiteError

SUPPORTED_SUFFIXES = (".jsonl", ".ndjson", ".json", ".yaml", ".yml", ".csv")


@dataclass
class LoadResult:
    cases: list[Case] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    format: str = "unknown"

    def extend(self, other: LoadResult) -> None:
        self.cases.extend(other.cases)
        self.warnings.extend(other.warnings)
        self.sources.extend(other.sources)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def load(path: str | Path, *, recursive: bool = True) -> LoadResult:
    """Load a suite from a file or a directory of supported files."""
    p = Path(path)
    if not p.exists():
        raise SuiteError(f"suite path does not exist: {p}")
    if p.is_dir():
        return _load_dir(p, recursive=recursive)
    return load_file(p)


def _load_dir(directory: Path, *, recursive: bool) -> LoadResult:
    pattern = "**/*" if recursive else "*"
    files = sorted(
        f
        for f in directory.glob(pattern)
        if f.is_file() and f.suffix.lower() in SUPPORTED_SUFFIXES and not f.name.startswith(".")
    )
    if not files:
        raise SuiteError(
            f"no suite files found in {directory} "
            f"(looked for {', '.join(SUPPORTED_SUFFIXES)})"
        )
    result = LoadResult(format="directory")
    for f in files:
        result.extend(load_file(f))
    # Directory loads dedupe by id, keeping the first occurrence, and warn.
    seen: dict[str, int] = {}
    deduped: list[Case] = []
    for case in result.cases:
        if case.id in seen:
            result.warnings.append(
                f"duplicate case id '{case.id}' across directory files; keeping first"
            )
            continue
        seen[case.id] = 1
        deduped.append(case)
    result.cases = deduped
    return result


def load_file(path: str | Path) -> LoadResult:
    p = Path(path)
    if not p.exists():
        raise SuiteError(f"suite file does not exist: {p}")
    suffix = p.suffix.lower()
    if suffix in (".jsonl", ".ndjson"):
        return _load_jsonl(p)
    if suffix == ".json":
        return _load_json(p)
    if suffix in (".yaml", ".yml"):
        return _load_yaml(p)
    if suffix == ".csv":
        return _load_csv(p)
    raise SuiteError(
        f"unsupported suite format '{suffix}' for {p}. "
        f"Supported: {', '.join(SUPPORTED_SUFFIXES)}"
    )


# ---------------------------------------------------------------------------
# Native formats
# ---------------------------------------------------------------------------

def _load_jsonl(path: Path) -> LoadResult:
    result = LoadResult(format="jsonl", sources=[str(path)])
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SuiteError(f"{path}:{lineno}: invalid JSON — {exc.msg}") from exc
        _add_native(result, data, where=f"{path}:{lineno}")
    return result


def _load_json(path: Path) -> LoadResult:
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SuiteError(f"{path}: invalid JSON — {exc.msg}") from exc
    result = LoadResult(format="json", sources=[str(path)])

    if isinstance(data, list):
        for i, entry in enumerate(data):
            _add_native(result, entry, where=f"{path}[{i}]")
        return result
    if isinstance(data, dict):
        if "cases" in data and isinstance(data["cases"], list):
            for i, entry in enumerate(data["cases"]):
                _add_native(result, entry, where=f"{path}.cases[{i}]")
            return result
        if "tests" in data and isinstance(data["tests"], list):
            # promptfoo JSON export
            return _load_promptfoo_dict(path, data, result)
        if "input" in data:
            _add_native(result, data, where=str(path))
            return result
    raise SuiteError(
        f"{path}: unrecognised JSON suite shape. Expected a list of cases, "
        f"an object with a 'cases' array, or a single case object."
    )


def _load_yaml(path: Path) -> LoadResult:
    text = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SuiteError(f"{path}: invalid YAML — {exc}") from exc
    result = LoadResult(format="yaml", sources=[str(path)])

    if isinstance(data, list):
        for i, entry in enumerate(data):
            _add_native(result, entry, where=f"{path}[{i}]")
        return result
    if isinstance(data, dict):
        if "prompts" in data and "tests" in data:
            return _load_promptfoo_dict(path, data, result)
        if "cases" in data and isinstance(data["cases"], list):
            for i, entry in enumerate(data["cases"]):
                _add_native(result, entry, where=f"{path}.cases[{i}]")
            return result
        if "input" in data:
            _add_native(result, data, where=str(path))
            return result
    raise SuiteError(f"{path}: unrecognised YAML suite shape.")


def _load_csv(path: Path) -> LoadResult:
    result = LoadResult(format="csv", sources=[str(path)])
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise SuiteError(f"{path}: CSV has no header row")
        for i, row in enumerate(reader, start=2):
            data: dict[str, Any] = {}
            for key, value in row.items():
                if key is None:
                    continue
                key = key.strip()
                value = (value or "").strip()
                if value == "":
                    continue
                if key in ("tags", "expected") and value.startswith("["):
                    try:
                        data[key] = json.loads(value)
                        continue
                    except json.JSONDecodeError:
                        pass
                if key in ("schema", "tools", "params", "meta") and value[:1] in "[{":
                    try:
                        data[key] = json.loads(value)
                        continue
                    except json.JSONDecodeError:
                        pass
                data[key] = value
            if "input" not in data:
                result.warnings.append(f"{path}:{i}: row skipped (no 'input' column)")
                continue
            _add_native(result, data, where=f"{path}:{i}")
    return result


# ---------------------------------------------------------------------------
# promptfoo (F-SUITE-1)
# ---------------------------------------------------------------------------

_ASSERT_MAP = {
    "contains": "expected",
    "icontains": "expected",
    "equals": "expected",
    "is-json": "schema",
    "contains-json": "expected_json",
}

_IGNORED_ASSERTS = {
    "javascript": "JavaScript assertions cannot be translated to a portable expectation",
    "llm-rubric": "LLM-rubric assertions require a judge; modelbump's judge is opt-in",
    "similar": "semantic similarity assertions are not portable",
    "factuality": "factuality assertions require a judge",
    "context-faithfulness": "faithfulness assertions require a judge",
    "answer-relevance": "relevance assertions require a judge",
}


def _load_promptfoo_dict(path: Path, data: dict[str, Any], result: LoadResult) -> LoadResult:
    result.format = "promptfoo"
    result.sources.append(str(path))

    prompts = data.get("prompts") or []
    system_prompt = None
    prompt_text = None
    for entry in prompts:
        if isinstance(entry, str):
            prompt_text = entry
        elif isinstance(entry, dict):
            prompt_text = entry.get("raw") or entry.get("prompt") or prompt_text
        if prompt_text:
            break

    tests = data.get("tests") or []
    if not isinstance(tests, list):
        raise SuiteError(f"{path}: promptfoo 'tests' must be a list")

    for i, test in enumerate(tests):
        where = f"{path}.tests[{i}]"
        if not isinstance(test, dict):
            result.warnings.append(f"{where}: skipped (not a mapping)")
            continue
        case_data: dict[str, Any] = {}
        vars_ = test.get("vars") or {}
        input_value: Any = None
        if isinstance(vars_, dict):
            for key in ("input", "question", "prompt", "query", "text"):
                if key in vars_:
                    input_value = vars_[key]
                    break
            if input_value is None and vars_:
                input_value = json.dumps(vars_, ensure_ascii=False, sort_keys=True)
        if input_value is None:
            input_value = test.get("description") or ""
        if isinstance(input_value, str) and prompt_text:
            input_value = _apply_prompt_template(prompt_text, vars_ if isinstance(vars_, dict) else {})

        case_data["input"] = input_value
        if system_prompt:
            case_data["system"] = system_prompt
        if test.get("description"):
            case_data["id"] = slugify(str(test["description"]))
        if isinstance(vars_, dict):
            tags = vars_.get("tags")
            if isinstance(tags, list):
                case_data["tags"] = [str(t) for t in tags]
        case_data.setdefault("tags", ["promptfoo"])

        assertions = test.get("assert") or []
        if not isinstance(assertions, list):
            assertions = [assertions]
        expectations: list[str] = []
        schema_found = None
        for assertion in assertions:
            if not isinstance(assertion, dict):
                continue
            atype = assertion.get("type", "")
            if atype in _IGNORED_ASSERTS:
                result.warnings.append(
                    f"{where}: ignoring promptfoo assert '{atype}' — {_IGNORED_ASSERTS[atype]}"
                )
                continue
            if atype == "is-json":
                value = assertion.get("value")
                if isinstance(value, dict):
                    schema_found = value
                continue
            if atype == "contains-json":
                value = assertion.get("value")
                if isinstance(value, dict):
                    expectations.append(json.dumps(value, sort_keys=True))
                continue
            if atype in ("contains", "icontains", "equals"):
                value = assertion.get("value")
                if value is not None:
                    expectations.append(str(value))
                continue
            if atype not in _ASSERT_MAP:
                result.warnings.append(f"{where}: ignoring unrecognised promptfoo assert '{atype}'")

        if expectations:
            case_data["expected"] = expectations[0] if len(expectations) == 1 else expectations
        if schema_found:
            case_data["schema"] = schema_found

        meta = {"promptfoo": True}
        if test.get("description"):
            meta["description"] = test["description"]
        case_data["meta"] = meta
        _add_native(result, case_data, where=where)
    return result


def _apply_prompt_template(template: str, vars_: dict[str, Any]) -> str:
    """Render a promptfoo ``{{var}}`` template with the test's vars."""
    out = template
    for key, value in vars_.items():
        out = out.replace("{{" + str(key) + "}}", str(value))
        out = out.replace("{{ " + str(key) + " }}", str(value))
    return out


# ---------------------------------------------------------------------------
# DeepEval / OpenAI Evals (F-SUITE-1)
# ---------------------------------------------------------------------------

def _load_deepeval_jsonl(path: Path) -> LoadResult:
    """Handled inline by _add_native when it sees input/expected_output keys."""
    return _load_jsonl(path)


# ---------------------------------------------------------------------------
# Native case insertion
# ---------------------------------------------------------------------------

def _add_native(result: LoadResult, data: Any, *, where: str) -> None:
    if not isinstance(data, dict):
        result.warnings.append(f"{where}: skipped (not a mapping)")
        return
    data = _normalise_import_keys(data, result, where)
    unknown = find_unknown_fields(data)
    if unknown:
        result.warnings.append(f"{where}: unknown field(s) {', '.join(unknown)} (kept in meta)")
    try:
        case = Case.from_dict(data)
    except ValueError as exc:
        raise SuiteError(f"{where}: {exc}") from exc
    result.cases.append(case)


def _normalise_import_keys(data: dict[str, Any], result: LoadResult, where: str) -> dict[str, Any]:
    """Map DeepEval / OpenAI Evals keys onto native fields."""
    out = dict(data)
    if "expected_output" in out and "expected" not in out:
        out["expected"] = out.pop("expected_output")
        result.warnings.append(f"{where}: mapped 'expected_output' → 'expected'")
    if "actual_output" in out:
        out.pop("actual_output", None)
    if "context" in out and "meta" not in out:
        out["meta"] = {"context": out.pop("context")}
    if "assert" in out and isinstance(out["assert"], list) and "expected" not in out:
        values = [
            str(a.get("value"))
            for a in out["assert"]
            if isinstance(a, dict) and a.get("value") is not None and a.get("type") in ("contains", "equals", "icontains")
        ]
        if values:
            out["expected"] = values[0] if len(values) == 1 else values
            result.warnings.append(f"{where}: mapped promptfoo-style 'assert' → 'expected'")
        out.pop("assert", None)
    if "vars" in out and isinstance(out["vars"], dict):
        vars_ = out.pop("vars")
        if "input" not in out:
            for key in ("input", "question", "prompt", "text"):
                if key in vars_:
                    out["input"] = vars_[key]
                    break
        meta = dict(out.get("meta") or {})
        meta.setdefault("vars", vars_)
        out["meta"] = meta
    if "description" in out:
        out.pop("description", None)
    return out


def cases_from_promptfoo(path: str | Path) -> LoadResult:
    p = Path(path)
    result = LoadResult()
    data = yaml.safe_load(p.read_text(encoding="utf-8")) if p.suffix.lower() in (".yaml", ".yml") else json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SuiteError(f"{p}: promptfoo config must be a mapping")
    return _load_promptfoo_dict(p, data, result)
