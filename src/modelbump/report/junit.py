"""JUnit XML writer for CI test tabs (F-REP-1, --junit)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from modelbump import __version__


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def write_junit(report: dict[str, Any], path: str | Path) -> Path:
    summary = report.get("summary", {})
    cases = report.get("cases", [])
    from_spec = report.get("from", {}).get("spec", "?")
    to_spec = report.get("to", {}).get("spec", "?")
    verdict = report.get("verdict", {})
    violations = verdict.get("violations", []) or []

    suite = ET.Element(
        "testsuite",
        {
            "name": f"modelbump: {from_spec} → {to_spec}",
            "tests": str(len(cases) or 1),
            "failures": str(sum(1 for c in cases if c.get("flags"))),
            "errors": "0",
            "skipped": "0",
            "time": _fmt(report.get("reproduce", {}).get("elapsed_s")),
        },
    )
    properties = ET.SubElement(suite, "properties")
    for key, value in (
        ("modelbump.version", __version__),
        ("modelbump.from", from_spec),
        ("modelbump.to", to_spec),
        ("modelbump.drift_rate", _fmt(summary.get("drift_rate"))),
        ("modelbump.verdict", "pass" if verdict.get("pass") else "fail"),
        ("modelbump.rubric_hash", str(report.get("rubric_hash", ""))),
    ):
        ET.SubElement(properties, "property", {"name": key, "value": value})

    for case in cases:
        case_el = ET.SubElement(
            suite,
            "testcase",
            {
                "name": str(case.get("id")),
                "classname": ",".join(case.get("tags") or []) or "modelbump",
            },
        )
        flags = case.get("flags") or []
        if flags:
            details = case.get("flag_details") or {}
            message = ", ".join(flags)
            body_lines = [
                f"drift_score: {_fmt(case.get('drift_score'))}",
                f"cross_similarity: {_fmt(case.get('cross_similarity'))}",
                f"noise_floor: {_fmt(case.get('noise_floor'))}",
                "",
            ]
            for flag in flags:
                body_lines.append(f"[{flag}] {details.get(flag, '')}")
            body_lines.append("")
            for side in ("a", "b"):
                side_stats = case.get(side, {})
                outputs = side_stats.get("outputs") or []
                body_lines.append(f"--- model {side.upper()} ---")
                for i, output in enumerate(outputs[:3]):
                    body_lines.append(f"  sample {i}: {str(output)[:400]}")
            failure = ET.SubElement(
                case_el,
                "failure",
                {"message": message, "type": "drift"},
            )
            failure.text = "\n".join(body_lines)

    if violations:
        for i, violation in enumerate(violations):
            case_el = ET.SubElement(
                suite,
                "testcase",
                {"name": f"threshold.{violation.get('rule')}.{i}", "classname": "thresholds"},
            )
            failure = ET.SubElement(
                case_el,
                "failure",
                {"message": violation.get("message", ""), "type": "threshold"},
            )
            failure.text = str(violation.get("message", ""))

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(suite)
    ET.indent(tree, space="  ")
    tree.write(p, encoding="utf-8", xml_declaration=True)
    return p
