"""Two-report comparison: has drift changed between runs? (F-REP-2)

Typical use: you fix a prompt or bump the pinned model and want to know whether
the drift got better, worse, or just moved sideways.
"""

from __future__ import annotations

from typing import Any

FLAG_SET = (
    "errors",
    "refusal",
    "schema",
    "strict_json",
    "expected",
    "expected_tool",
    "tool_choice",
    "tool_args",
    "semantic",
    "length",
    "language",
    "truncated",
)


def _cases_by_id(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(c.get("id")): c for c in report.get("cases", [])}


def compare_reports(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_cases = _cases_by_id(before)
    after_cases = _cases_by_id(after)
    ids = sorted(set(before_cases) | set(after_cases))

    fixed: list[str] = []
    regressed: list[str] = []
    unchanged_flagged: list[str] = []
    unchanged_clean: list[str] = []
    changed_flags: list[dict[str, Any]] = []

    for case_id in ids:
        b = before_cases.get(case_id)
        a = after_cases.get(case_id)
        if b is None:
            changed_flags.append({"id": case_id, "note": "added in the newer report"})
            continue
        if a is None:
            changed_flags.append({"id": case_id, "note": "missing from the newer report"})
            continue
        b_flags = set(b.get("flags") or [])
        a_flags = set(a.get("flags") or [])
        if b_flags and not a_flags:
            fixed.append(case_id)
        elif a_flags and not b_flags:
            regressed.append(case_id)
        elif a_flags and b_flags:
            unchanged_flagged.append(case_id)
        else:
            unchanged_clean.append(case_id)
        if b_flags != a_flags:
            changed_flags.append(
                {
                    "id": case_id,
                    "before_flags": sorted(b_flags),
                    "after_flags": sorted(a_flags),
                    "before_drift": b.get("drift_score"),
                    "after_drift": a.get("drift_score"),
                }
            )

    before_summary = before.get("summary", {})
    after_summary = after.get("summary", {})
    b_rate = float(before_summary.get("drift_rate", 0.0))
    a_rate = float(after_summary.get("drift_rate", 0.0))

    return {
        "before": {
            "from": before.get("from", {}).get("spec"),
            "to": before.get("to", {}).get("spec"),
            "generated_at": before.get("generated_at"),
            "drift_rate": b_rate,
            "flagged_cases": before_summary.get("flagged_cases", 0),
            "pass": before.get("verdict", {}).get("pass"),
        },
        "after": {
            "from": after.get("from", {}).get("spec"),
            "to": after.get("to", {}).get("spec"),
            "generated_at": after.get("generated_at"),
            "drift_rate": a_rate,
            "flagged_cases": after_summary.get("flagged_cases", 0),
            "pass": after.get("verdict", {}).get("pass"),
        },
        "drift_rate_delta": a_rate - b_rate,
        "direction": (
            "improved" if a_rate < b_rate else "worsened" if a_rate > b_rate else "unchanged"
        ),
        "fixed_cases": fixed,
        "regressed_cases": regressed,
        "still_flagged": unchanged_flagged,
        "still_clean": unchanged_clean,
        "flag_changes": changed_flags,
        "flag_histogram_delta": _hist_delta(before_summary, after_summary),
    }


def _hist_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, int]:
    b = before.get("flag_histogram") or {}
    a = after.get("flag_histogram") or {}
    delta = {}
    for flag in sorted(set(b) | set(a)):
        change = int(a.get(flag, 0)) - int(b.get(flag, 0))
        if change:
            delta[flag] = change
    return delta


def render_text(result: dict[str, Any]) -> str:
    from modelbump.output import C, bold, dim, ok, paint, warn

    before = result["before"]
    after = result["after"]
    delta = result["drift_rate_delta"]
    direction = result["direction"]
    color = {
        "improved": ok,
        "worsened": warn,
        "unchanged": dim,
    }[direction]

    lines = [
        bold("modelbump compare"),
        "",
        f"  before: {before['from']} → {before['to']}  drift {before['drift_rate']:.1%} "
        f"({before['flagged_cases']} flagged)",
        f"  after:  {after['from']} → {after['to']}  drift {after['drift_rate']:.1%} "
        f"({after['flagged_cases']} flagged)",
        "",
        f"  change: {color(f'{delta:+.1%} ({direction})')}",
        "",
    ]
    if result["fixed_cases"]:
        lines.append(ok(f"  fixed ({len(result['fixed_cases'])}): ") +
                     ", ".join(result["fixed_cases"][:20]))
    if result["regressed_cases"]:
        lines.append(warn(f"  regressed ({len(result['regressed_cases'])}): ") +
                     ", ".join(result["regressed_cases"][:20]))
    if result["still_flagged"]:
        lines.append(dim(f"  still flagged ({len(result['still_flagged'])})"))
    if result["flag_histogram_delta"]:
        lines.append("")
        lines.append("  flag deltas:")
        for flag, change in result["flag_histogram_delta"].items():
            sign = "+" if change > 0 else ""
            lines.append(f"    {flag}: {sign}{change}")
    lines.append("")
    lines.append(paint("  reproduce:", C.BRIGHT_CYAN))
    lines.append(f"    modelbump report {before.get('generated_at', '')}".rstrip())
    return "\n".join(lines)


def render_json(result: dict[str, Any]) -> str:
    import json

    return json.dumps(result, indent=2)
