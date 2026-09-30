"""Metrics: per-sample, per-(case, model), and drift scoring (F-MET-*).

Everything here is cheap and deterministic. No judge, no embeddings, no network.
The one idea that matters: we never call sampling variance drift. We measure the
model's *own* spread (``self_sim``) and only flag a case when the cross-model
similarity falls below that noise floor.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from modelbump.case import Case
from modelbump.providers.base import Response, ToolCall
from modelbump.providers.translate import normalize_tool, validate_tool_arguments
from modelbump.registry import registry

# ---------------------------------------------------------------------------
# Documented refusal family (F-MET, --refusal-patterns overrides)
# ---------------------------------------------------------------------------

DEFAULT_REFUSAL_PATTERNS: tuple[str, ...] = (
    r"\bi(?:'m| am)? (?:sorry|unable|not able)\b",
    r"\bi can(?:'t|not| ?no longer)\b",
    r"\bi (?:won't|will not|must decline|have to decline|refuse)\b",
    r"\b(?:cannot|can't|unable to) (?:help|assist|provide|comply|do that)\b",
    r"\bagainst (?:my|our) (?:guidelines|policy|principles)\b",
    r"\bnot (?:able|allowed|permitted) to\b",
    r"\bi (?:must|have to) (?:respectfully )?decline\b",
    r"\bas an ai\b.{0,40}\b(?:cannot|can't|won't)\b",
    r"\b(?:refuse|decline) to (?:answer|help|assist|comply|provide)\b",
)

REFUSAL_RE = re.compile("|".join(f"(?:{p})" for p in DEFAULT_REFUSAL_PATTERNS), re.IGNORECASE)

# ---------------------------------------------------------------------------
# Output-kind detection
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(?P<body>.*?)```", re.DOTALL)


def extract_json(text: str) -> tuple[Any, bool]:
    """Leniently extract a JSON value from model output.

    Returns ``(value, strict)`` where ``strict`` means the *entire* trimmed text
    was valid JSON with no prose or code fences around it.
    """
    if not isinstance(text, str):
        return None, False
    stripped = text.strip()
    if not stripped:
        return None, False
    try:
        return json.loads(stripped), True
    except json.JSONDecodeError:
        pass
    # Fenced block?
    match = _FENCE_RE.search(stripped)
    if match:
        try:
            return json.loads(match.group("body").strip()), False
        except json.JSONDecodeError:
            pass
    # First balanced object/array in the text.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = stripped.find(opener)
        if start == -1:
            continue
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(stripped)):
            char = stripped[i]
            if escape:
                escape = False
                continue
            if char == "\\":
                escape = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    candidate = stripped[start : i + 1]
                    try:
                        return json.loads(candidate), False
                    except json.JSONDecodeError:
                        break
    return None, False


def output_kind(case: Case, response: Response) -> str:
    """``"tool"``, ``"json"``, or ``"text"`` — decides how similarity is measured."""
    if response.tool_calls:
        return "tool"
    if case.schema is not None:
        return "json"
    if isinstance(case.expected, dict) and "regex" not in case.expected:
        return "json"
    parsed, _ = extract_json(response.text)
    if isinstance(parsed, (dict, list)):
        return "json"
    return "text"


# ---------------------------------------------------------------------------
# Per-sample metrics
# ---------------------------------------------------------------------------

@dataclass
class SampleMetrics:
    index: int = 0
    error: bool = False
    error_message: str | None = None
    refused: bool = False
    schema_valid: bool | None = None
    strict_json: bool | None = None
    expected_hit: bool | None = None
    tool_names: list[str] = field(default_factory=list)
    tool_arg_keys: list[str] = field(default_factory=list)
    tool_args_valid: bool | None = None
    expected_tool_hit: bool | None = None
    chars: int = 0
    tokens_out: int | None = None
    tokens_in: int | None = None
    latency_ms: float | None = None
    ttft_ms: float | None = None
    cost_usd: float | None = None
    language: str | None = None
    finish_reason: str | None = None
    truncated: bool = False
    structured_output_mechanism: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "error": self.error,
            "error_message": self.error_message,
            "refused": self.refused,
            "schema_valid": self.schema_valid,
            "strict_json": self.strict_json,
            "expected_hit": self.expected_hit,
            "tool_names": self.tool_names,
            "tool_arg_keys": self.tool_arg_keys,
            "tool_args_valid": self.tool_args_valid,
            "expected_tool_hit": self.expected_tool_hit,
            "chars": self.chars,
            "tokens_out": self.tokens_out,
            "tokens_in": self.tokens_in,
            "latency_ms": self.latency_ms,
            "ttft_ms": self.ttft_ms,
            "cost_usd": self.cost_usd,
            "language": self.language,
            "finish_reason": self.finish_reason,
            "truncated": self.truncated,
            "structured_output_mechanism": self.structured_output_mechanism,
        }


def compute_sample_metrics(
    case: Case,
    response: Response,
    *,
    index: int = 0,
    model_name: str | None = None,
    refusal_re: re.Pattern[str] | None = None,
) -> SampleMetrics:
    pattern = refusal_re or REFUSAL_RE
    metrics = SampleMetrics(index=index)
    metrics.chars = len(response.text or "")
    metrics.tokens_in = response.tokens_in
    metrics.tokens_out = response.tokens_out
    metrics.latency_ms = response.latency_ms
    metrics.ttft_ms = response.ttft_ms
    metrics.finish_reason = response.finish_reason
    metrics.truncated = response.truncated
    metrics.structured_output_mechanism = response.structured_output_mechanism

    if response.error:
        metrics.error = True
        metrics.error_message = response.error
        # Still record latency/cost where the provider reported them.
        metrics.cost_usd = registry().cost(
            model_name or "", response.tokens_in, response.tokens_out, response.tokens_cached
        )
        return metrics

    text = response.text or ""
    metrics.refused = bool(pattern.search(text)) or (response.finish_reason == "refusal")
    metrics.language = detect_language(text) if text.strip() else None

    # -- schema ---------------------------------------------------------
    if case.schema is not None:
        parsed, _ = extract_json(text)
        metrics.strict_json = is_strict_json(text)
        metrics.schema_valid = _schema_valid(parsed, case.schema)
    elif response.tool_calls:
        metrics.strict_json = None
        metrics.schema_valid = None
    else:
        parsed, strict = extract_json(text)
        if isinstance(parsed, (dict, list)):
            metrics.strict_json = strict
        else:
            metrics.strict_json = None

    # -- expected -------------------------------------------------------
    if case.expected is not None:
        metrics.expected_hit = expected_hit(case.expected, text)

    # -- tools ----------------------------------------------------------
    if response.tool_calls:
        metrics.tool_names = [call.name for call in response.tool_calls]
        keys: list[str] = []
        for call in response.tool_calls:
            keys.extend(call.arguments_dict().keys())
        metrics.tool_arg_keys = keys
        if case.expect_tool:
            metrics.expected_tool_hit = case.expect_tool in metrics.tool_names
        metrics.tool_args_valid = _tool_args_valid(case, response.tool_calls)

    metrics.cost_usd = registry().cost(
        model_name or "", response.tokens_in, response.tokens_out, response.tokens_cached
    )
    return metrics


def is_strict_json(text: str) -> bool:
    """True when the whole output parses as JSON with no fences or prose."""
    if not isinstance(text, str):
        return False
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return False
    try:
        json.loads(stripped)
        return True
    except json.JSONDecodeError:
        return False


def _schema_valid(parsed: Any, schema: dict[str, Any]) -> bool:
    if parsed is None:
        return False
    try:
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import SchemaError

        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError:
            return False
        Draft202012Validator(schema).validate(parsed)
        return True
    except Exception:
        return False


def _tool_args_valid(case: Case, calls: list[ToolCall]) -> bool | None:
    if not case.tools:
        return None
    schemas = {}
    for tool in case.tools:
        norm = normalize_tool(tool)
        schemas[norm["name"]] = norm["parameters"]
    results = []
    for call in calls:
        params = schemas.get(call.name)
        if params is None:
            results.append(False)
            continue
        ok, _ = validate_tool_arguments(call.arguments_dict(), params)
        results.append(ok)
    return all(results) if results else None


# ---------------------------------------------------------------------------
# Expected matching
# ---------------------------------------------------------------------------

def expected_hit(expected: Any, text: str) -> bool:
    """Containment / any-of / JSON-subset / regex (F-MET)."""
    if expected is None:
        return True
    if isinstance(expected, dict) and "regex" in expected:
        try:
            return re.search(str(expected["regex"]), text or "") is not None
        except re.error:
            return False
    if isinstance(expected, list):
        return any(str(item).lower() in (text or "").lower() for item in expected)
    if isinstance(expected, dict):
        parsed, _ = extract_json(text or "")
        return json_subset(parsed, expected)
    needle = str(expected)
    haystack = text or ""
    if needle.lower() in haystack.lower():
        return True
    return _normalise(needle) == _normalise(haystack)


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def json_subset(actual: Any, expected: Any) -> bool:
    """True when ``expected`` is structurally contained in ``actual``."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        for key, value in expected.items():
            if key not in actual:
                return False
            if not json_subset(actual[key], value):
                return False
        return True
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return False
        return all(any(json_subset(item, sub) for item in actual) for sub in expected)
    if isinstance(expected, bool) or isinstance(actual, bool):
        return actual == expected
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return math.isclose(float(actual), float(expected), rel_tol=1e-6, abs_tol=1e-6)
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.lower() in actual.lower() or _normalise(expected) == _normalise(actual)
    return actual == expected


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


def text_similarity(a: str, b: str) -> float:
    """0.5 * normalised sequence ratio + 0.5 * token Jaccard."""
    a, b = a or "", b or ""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    ratio = SequenceMatcher(None, a, b).ratio()
    ta, tb = set(_tokens(a)), set(_tokens(b))
    if not ta and not tb:
        jaccard = 1.0
    elif not ta or not tb:
        jaccard = 0.0
    else:
        jaccard = len(ta & tb) / len(ta | tb)
    return 0.5 * ratio + 0.5 * jaccard


def _json_paths(value: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten a JSON value to ``path -> leaf value`` pairs."""
    out: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, sub in value.items():
            out.update(_json_paths(sub, f"{prefix}.{key}" if prefix else str(key)))
        if not value:
            out[prefix or "$"] = {}
    elif isinstance(value, list):
        for i, sub in enumerate(value):
            out.update(_json_paths(sub, f"{prefix}[{i}]"))
        if not value:
            out[prefix or "$"] = []
    else:
        out[prefix or "$"] = value
    return out


def json_similarity(a: Any, b: Any) -> float:
    """Structural similarity: key-path overlap + value equality ratio."""
    if a is None and b is None:
        return 1.0
    if a is None or b is None:
        return 0.0
    pa, pb = _json_paths(a), _json_paths(b)
    keys_a, keys_b = set(pa), set(pb)
    if not keys_a and not keys_b:
        return 1.0
    key_overlap = len(keys_a & keys_b) / len(keys_a | keys_b) if (keys_a | keys_b) else 1.0
    shared = keys_a & keys_b
    if shared:
        equal = sum(1 for k in shared if _leaf_equal(pa[k], pb[k]))
        value_ratio = equal / len(shared)
    else:
        value_ratio = 0.0
    return 0.5 * key_overlap + 0.5 * value_ratio


def _leaf_equal(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=1e-6, abs_tol=1e-6)
    if isinstance(a, str) and isinstance(b, str):
        return _normalise(a) == _normalise(b)
    return a == b


def tool_similarity(a: list[ToolCall], b: list[ToolCall]) -> float:
    """Name equality + argument key/value overlap for tool calls."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    names_a = [c.name for c in a]
    names_b = [c.name for c in b]
    name_score = 1.0 if names_a == names_b else (
        len(set(names_a) & set(names_b)) / len(set(names_a) | set(names_b)) if set(names_a) | set(names_b) else 1.0
    )
    args_a = [c.arguments_dict() for c in a]
    args_b = [c.arguments_dict() for c in b]
    best_pairs = min(len(args_a), len(args_b))
    arg_scores = []
    for i in range(best_pairs):
        arg_scores.append(json_similarity(args_a[i], args_b[i]))
    arg_score = sum(arg_scores) / len(arg_scores) if arg_scores else 0.0
    return 0.5 * name_score + 0.5 * arg_score


def similarity(kind: str, a: Response, b: Response) -> float:
    """Dispatch to the right similarity measure for the output kind."""
    if kind == "tool":
        return tool_similarity(a.tool_calls, b.tool_calls)
    if kind == "json":
        pa, _ = extract_json(a.text)
        pb, _ = extract_json(b.text)
        if pa is None and pb is None:
            return text_similarity(a.text, b.text)
        if pa is None or pb is None:
            # One side produced JSON, the other did not — that is drift.
            return text_similarity(a.text, b.text) * 0.5
        return json_similarity(pa, pb)
    return text_similarity(a.text, b.text)


# ---------------------------------------------------------------------------
# Language heuristic
# ---------------------------------------------------------------------------

_LANGUAGE_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("en", (" the ", " and ", " of ", " is ", " to ", " that ", " with ")),
    ("es", (" el ", " la ", " los ", " las ", " que ", " de ", " y ", " para ")),
    ("fr", (" le ", " la ", " les ", " des ", " est ", " une ", " pour ", " que ")),
    ("de", (" der ", " die ", " das ", " und ", " ist ", " nicht ", " ein ")),
    ("pt", (" o ", " os ", " uma ", " que ", " não ", " para ", " com ")),
    ("it", (" il ", " lo ", " gli ", " che ", " non ", " per ", " una ")),
    ("nl", (" het ", " een ", " niet ", " van ", " voor ", " dat ")),
    ("ru", (" и ", " не ", " что ", " это ", " для ")),
]

_CHAR_RANGES = (
    ("ja", 0x3040, 0x30FF),
    ("zh", 0x4E00, 0x9FFF),
    ("ko", 0xAC00, 0xD7AF),
    ("ru", 0x0400, 0x04FF),
    ("ar", 0x0600, 0x06FF),
    ("hi", 0x0900, 0x097F),
    ("el", 0x0370, 0x03FF),
    ("he", 0x0590, 0x05FF),
)


def detect_language(text: str) -> str:
    """A fast, deliberately crude language guess — enough to catch a switch."""
    if not text:
        return "unknown"
    counts: dict[str, int] = {}
    for code, lo, hi in _CHAR_RANGES:
        n = sum(1 for ch in text if lo <= ord(ch) <= hi)
        if n:
            counts[code] = n
    if counts:
        code, n = max(counts.items(), key=lambda kv: kv[1])
        if n / max(1, len(text)) > 0.15:
            return code
    lowered = " " + text.lower() + " "
    best, best_score = "en", 0
    for code, markers in _LANGUAGE_HINTS:
        score = sum(lowered.count(marker) for marker in markers)
        if score > best_score:
            best, best_score = code, score
    return best


# ---------------------------------------------------------------------------
# Per (case, model) aggregation
# ---------------------------------------------------------------------------

@dataclass
class ModelCaseStats:
    samples: list[SampleMetrics] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    tool_calls: list[list[dict[str, Any]]] = field(default_factory=list)
    self_similarity: float | None = None
    error_rate: float = 0.0
    refusal_rate: float = 0.0
    schema_valid_rate: float | None = None
    strict_json_rate: float | None = None
    expected_hit_rate: float | None = None
    expected_tool_hit_rate: float | None = None
    tool_args_valid_rate: float | None = None
    p50_latency_ms: float | None = None
    p95_latency_ms: float | None = None
    avg_cost_usd: float | None = None
    avg_chars: float | None = None
    languages: list[str] = field(default_factory=list)
    truncated_any: bool = False
    structured_output_mechanism: str | None = None

    def to_dict(self, include_outputs: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "error_rate": self.error_rate,
            "refusal_rate": self.refusal_rate,
            "schema_valid_rate": self.schema_valid_rate,
            "strict_json_rate": self.strict_json_rate,
            "expected_hit_rate": self.expected_hit_rate,
            "expected_tool_hit_rate": self.expected_tool_hit_rate,
            "tool_args_valid_rate": self.tool_args_valid_rate,
            "p50_latency_ms": self.p50_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "avg_cost_usd": self.avg_cost_usd,
            "avg_chars": self.avg_chars,
            "self_similarity": self.self_similarity,
            "languages": self.languages,
            "truncated_any": self.truncated_any,
            "structured_output_mechanism": self.structured_output_mechanism,
            "sample_metrics": [s.to_dict() for s in self.samples],
        }
        if include_outputs:
            out["outputs"] = self.outputs
            out["tool_calls"] = self.tool_calls
        return out


def _rate(values: list[bool | None]) -> float | None:
    usable = [v for v in values if v is not None]
    if not usable:
        return None
    return sum(1 for v in usable if v) / len(usable)


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * pct
    lower = math.floor(k)
    upper = math.ceil(k)
    if lower == upper:
        return ordered[int(k)]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (k - lower)


def aggregate_model_case(
    case: Case,
    responses: list[Response],
    *,
    model_name: str,
    kind: str,
    refusal_re: re.Pattern[str] | None = None,
) -> ModelCaseStats:
    stats = ModelCaseStats()
    for i, response in enumerate(responses):
        metrics = compute_sample_metrics(
            case, response, index=i, model_name=model_name, refusal_re=refusal_re
        )
        stats.samples.append(metrics)
        stats.outputs.append(response.text or "")
        stats.tool_calls.append([c.to_dict() for c in response.tool_calls])

    n = max(1, len(responses))
    stats.error_rate = sum(1 for s in stats.samples if s.error) / n
    stats.refusal_rate = sum(1 for s in stats.samples if s.refused) / n
    stats.schema_valid_rate = _rate([s.schema_valid for s in stats.samples])
    stats.strict_json_rate = _rate([s.strict_json for s in stats.samples])
    stats.expected_hit_rate = _rate([s.expected_hit for s in stats.samples])
    stats.expected_tool_hit_rate = _rate([s.expected_tool_hit for s in stats.samples])
    stats.tool_args_valid_rate = _rate([s.tool_args_valid for s in stats.samples])
    latencies = [s.latency_ms for s in stats.samples if s.latency_ms is not None]
    stats.p50_latency_ms = percentile(latencies, 0.5)
    stats.p95_latency_ms = percentile(latencies, 0.95)
    costs = [s.cost_usd for s in stats.samples if s.cost_usd is not None]
    stats.avg_cost_usd = sum(costs) / len(costs) if costs else None
    stats.avg_chars = sum(s.chars for s in stats.samples) / n
    stats.languages = sorted({s.language for s in stats.samples if s.language})
    stats.truncated_any = any(s.truncated for s in stats.samples)
    mechs = sorted({s.structured_output_mechanism for s in stats.samples if s.structured_output_mechanism})
    stats.structured_output_mechanism = mechs[0] if len(mechs) == 1 else (", ".join(mechs) if mechs else None)
    stats.self_similarity = self_similarity(responses, kind)
    return stats


def self_similarity(responses: list[Response], kind: str) -> float | None:
    """Mean pairwise similarity of a model's own samples — the noise floor."""
    if len(responses) < 2:
        return None
    scores = []
    for i in range(len(responses)):
        for j in range(i + 1, len(responses)):
            scores.append(similarity(kind, responses[i], responses[j]))
    if not scores:
        return None
    return sum(scores) / len(scores)


def cross_similarity(responses_a: list[Response], responses_b: list[Response], kind: str) -> float:
    """Mean similarity across every A/B sample pair."""
    if not responses_a or not responses_b:
        return 0.0
    scores = [
        similarity(kind, a, b) for a in responses_a for b in responses_b
    ]
    return sum(scores) / len(scores) if scores else 0.0


def drift_score(cross_sim: float, self_a: float | None, self_b: float | None) -> float:
    """``max(0, noise - cross_sim) / noise`` with ``noise = min(self_sim_A, self_sim_B)``.

    When a self-similarity is unavailable (single sample), the noise floor is
    treated as 1.0 so the cross similarity speaks for itself.
    """
    candidates = [s for s in (self_a, self_b) if s is not None]
    noise = min(candidates) if candidates else 1.0
    if noise <= 0:
        # A model is not even self-consistent; fall back to raw dissimilarity.
        return max(0.0, 1.0 - cross_sim)
    return max(0.0, (noise - cross_sim) / noise)
