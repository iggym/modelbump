"""Optional bounded judge (F-JUDGE-1..3).

Runs only on flagged cases unless ``--judge-all``. Every case is judged twice
with the positions swapped (A/B then B/A) so position bias cancels; when the two
orders disagree the result is recorded as ``tie/uncertain`` rather than being
silently resolved.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from modelbump.case import Case
from modelbump.errors import JudgeError
from modelbump.providers.base import BaseProvider, Response

RUBRIC_VERSION = "1.0.0"

RUBRIC = """\
You are comparing two model outputs for the same task. Decide whether they are
behaviourally equivalent for the user's purpose.

Judge on, in order:
1. Task correctness — does each output do what the input asked?
2. Constraint compliance — schema, required fields, expected content, tools.
3. Material differences only — ignore wording, formatting, and length unless
   they change meaning, break a constraint, or change a decision.

Do not reward verbosity. Do not penalise a different but equivalent phrasing.

Reply with a single JSON object and nothing else:
{"equivalent": true|false, "better": "first"|"second"|"tie", "reason": "<one sentence>"}
"""


def rubric_hash(rubric: str | None = None) -> str:
    text = rubric if rubric is not None else RUBRIC
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass
class JudgeVerdict:
    equivalent: bool | None
    better: str  # "a" | "b" | "tie"
    reason: str = ""
    order1: dict[str, Any] | None = None
    order2: dict[str, Any] | None = None
    uncertain: bool = False
    cost_usd: float | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "equivalent": self.equivalent,
            "better": self.better,
            "reason": self.reason,
            "uncertain": self.uncertain,
            "order1": self.order1,
            "order2": self.order2,
            "cost_usd": self.cost_usd,
            "error": self.error,
        }


def _build_prompt(case: Case, first: str, second: str) -> str:
    parts = [f"TASK INPUT:\n{case.flat_input}"]
    if case.system:
        parts.insert(0, f"SYSTEM INSTRUCTION:\n{case.system}")
    if case.expected is not None:
        parts.append(f"EXPECTED (may be a subset, list, or regex):\n{json.dumps(case.expected, ensure_ascii=False)}")
    if case.schema is not None:
        parts.append(f"REQUIRED SCHEMA:\n{json.dumps(case.schema, ensure_ascii=False)}")
    if case.expect_tool:
        parts.append(f"EXPECTED TOOL: {case.expect_tool}")
    parts.append(f"FIRST OUTPUT:\n{first}")
    parts.append(f"SECOND OUTPUT:\n{second}")
    parts.append(
        "Reply with a single JSON object and nothing else, using these keys:\n"
        '  "equivalent": true if the two outputs are semantically interchangeable, else false\n'
        '  "better": "first", "second", or "tie"\n'
        '  "reason": one short sentence'
    )
    return "\n\n".join(parts)


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_judge_output(text: str) -> dict[str, Any]:
    if not text:
        return {}
    match = _JSON_RE.search(text)
    candidate = match.group(0) if match else text
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


class Judge:
    """A bounded pairwise judge over a provider."""

    def __init__(
        self,
        provider: BaseProvider,
        *,
        model_name: str,
        rubric: str | None = None,
        max_cases: int = 100,
    ) -> None:
        self.provider = provider
        self.model_name = model_name
        self.rubric = rubric or RUBRIC
        self.rubric_hash = rubric_hash(self.rubric)
        self.rubric_version = RUBRIC_VERSION
        self.max_cases = max_cases

    async def judge_case(self, case: Case, output_a: str, output_b: str) -> JudgeVerdict:
        verdict = JudgeVerdict(equivalent=None, better="tie")

        # Order 1: A first, B second.
        first = await self._call(case, output_a, output_b)
        # Order 2: B first, A second.
        second = await self._call(case, output_b, output_a)
        verdict.order1 = first
        verdict.order2 = second

        errors = [r.get("_error") for r in (first, second) if r.get("_error")]
        if errors:
            verdict.error = "; ".join(str(e) for e in errors)[:300]
            verdict.uncertain = True

        costs = [r.get("_cost") for r in (first, second) if r.get("_cost") is not None]
        verdict.cost_usd = sum(costs) if costs else None

        # Translate "better" back into A/B terms for each order.
        better1 = _better_to_ab(first.get("better"), swapped=False)
        better2 = _better_to_ab(second.get("better"), swapped=True)

        if better1 is None or better2 is None:
            verdict.better = "tie"
            verdict.uncertain = True
        elif better1 == better2:
            verdict.better = better1
        else:
            verdict.better = "tie"
            verdict.uncertain = True
            verdict.reason = (
                f"position swap disagreed (order 1 preferred {better1}, "
                f"order 2 preferred {better2}) — recorded as uncertain"
            )

        eq1 = first.get("equivalent")
        eq2 = second.get("equivalent")
        if isinstance(eq1, bool) and isinstance(eq2, bool):
            if eq1 == eq2:
                verdict.equivalent = eq1
            else:
                verdict.equivalent = None
                verdict.uncertain = True
        else:
            verdict.equivalent = eq1 if isinstance(eq1, bool) else None

        if not verdict.reason:
            verdict.reason = str(first.get("reason") or second.get("reason") or "")
        return verdict

    async def _call(self, case: Case, first: str, second: str) -> dict[str, Any]:
        prompt = _build_prompt(case, first, second)
        response: Response = await self.provider.complete(
            messages=[{"role": "user", "content": prompt}],
            system=self.rubric,
            params={"temperature": 0},
        )
        if response.error:
            return {"_error": response.error}
        parsed = _parse_judge_output(response.text)
        parsed["_cost"] = _cost(self.model_name, response)
        parsed["_raw_text"] = response.text[:2000]
        return parsed


def _better_to_ab(value: Any, *, swapped: bool) -> str | None:
    if not isinstance(value, str):
        return None
    lowered = value.strip().lower()
    if lowered in ("tie", "equal", "neither", "same"):
        return "tie"
    if lowered in ("first", "a", "1"):
        return "b" if swapped else "a"
    if lowered in ("second", "b", "2"):
        return "a" if swapped else "b"
    return None


def _cost(model_name: str, response: Response) -> float | None:
    from modelbump.registry import registry

    return registry().cost(
        model_name, response.tokens_in, response.tokens_out, response.tokens_cached
    )


def load_rubric(path: str | Path) -> str:
    p = Path(path)
    if not p.exists():
        raise JudgeError(f"rubric file does not exist: {p}")
    text = p.read_text(encoding="utf-8").strip()
    if not text:
        raise JudgeError(f"rubric file is empty: {p}")
    return text


def judge_summary(verdicts: dict[str, JudgeVerdict]) -> dict[str, Any]:
    judged = len(verdicts)
    prefer_a = sum(1 for v in verdicts.values() if v.better == "a")
    prefer_b = sum(1 for v in verdicts.values() if v.better == "b")
    ties = sum(1 for v in verdicts.values() if v.better == "tie")
    uncertain = sum(1 for v in verdicts.values() if v.uncertain)
    equivalent = sum(1 for v in verdicts.values() if v.equivalent is True)
    not_equivalent = sum(1 for v in verdicts.values() if v.equivalent is False)
    total_cost = sum(v.cost_usd for v in verdicts.values() if v.cost_usd is not None)
    return {
        "judged": judged,
        "prefer_a": prefer_a,
        "prefer_b": prefer_b,
        "tie": ties,
        "uncertain": uncertain,
        "equivalent": equivalent,
        "not_equivalent": not_equivalent,
        "cost_usd": round(total_cost, 6) if total_cost else 0.0,
    }
