"""``report.md`` — PR-comment-safe Markdown (F-REP-1, F-REP-3).

Nothing here relies on HTML, images, or hover, so it renders correctly in a
GitHub PR comment, a Slack unfurl, or a terminal pager.
"""

from __future__ import annotations

from typing import Any

VERDICT_BADGE = {True: "✅ **PASS**", False: "❌ **FAIL**"}

# Stable marker so the GitHub Action can find and update its own PR comment.
STICKY_MARKER = "<!-- modelbump-report -->"


def _pct(value: Any) -> str:
    if value is None:
        return "—"
    return f"{float(value) * 100:.1f}%"


def _num(value: Any, unit: str = "", digits: int = 1) -> str:
    if value is None:
        return "—"
    formatted = f"{float(value):,.{digits}f}"
    return f"{formatted}{unit}"


def _money(value: Any) -> str:
    if value is None:
        return "— *(pricing unknown)*"
    return f"${float(value):.4f}"


def _delta(a: Any, b: Any, *, invert: bool = False) -> str:
    if a is None or b is None:
        return "—"
    change = b - a
    if invert:
        good = change <= 0
    else:
        good = change >= 0
    arrow = "▲" if change > 0 else ("▼" if change < 0 else "＝")
    sign = "+" if change > 0 else ""
    marker = "🟢" if good else "🔴"
    if abs(change) < 1e-12:
        return f"＝ {marker}"
    return f"{arrow} {sign}{change:.4f} {marker}"


def render_markdown(report: dict[str, Any], *, max_cases: int = 20) -> str:
    summary = report.get("summary", {})
    verdict = report.get("verdict", {})
    a = summary.get("a", {})
    b = summary.get("b", {})
    passed = bool(verdict.get("pass"))
    from_spec = report.get("from", {}).get("spec", "?")
    to_spec = report.get("to", {}).get("spec", "?")
    samples = report.get("samples", "?")
    total = summary.get("total_cases", 0)
    flagged = summary.get("flagged_cases", 0)

    lines: list[str] = []
    lines.append(STICKY_MARKER)
    lines.append(f"# modelbump — {from_spec} → {to_spec}")
    lines.append("")
    lines.append(f"{VERDICT_BADGE[passed]}  ·  **{flagged}/{total}** cases flagged  ·  "
                 f"drift rate **{_pct(summary.get('drift_rate'))}**  ·  {samples} samples/case")
    lines.append("")

    if not passed and verdict.get("violations"):
        lines.append("## ❌ Threshold violations")
        lines.append("")
        for violation in verdict["violations"]:
            lines.append(f"- **`{violation['rule']}`** — {violation['message']}")
        lines.append("")
    if verdict.get("warnings"):
        lines.append("## ⚠️ Warnings")
        lines.append("")
        for warning in verdict["warnings"]:
            lines.append(f"- **`{warning['rule']}`** — {warning['message']}")
        lines.append("")

    lines.append("## Suite summary")
    lines.append("")
    lines.append("| Metric | A | B | Change |")
    lines.append("| --- | ---: | ---: | :---: |")
    lines.append(
        f"| Schema-valid rate | {_pct(a.get('schema_valid_rate'))} | "
        f"{_pct(b.get('schema_valid_rate'))} | "
        f"{_delta(a.get('schema_valid_rate'), b.get('schema_valid_rate'))} |"
    )
    lines.append(
        f"| Expected-hit rate | {_pct(a.get('expected_hit_rate'))} | "
        f"{_pct(b.get('expected_hit_rate'))} | "
        f"{_delta(a.get('expected_hit_rate'), b.get('expected_hit_rate'))} |"
    )
    lines.append(
        f"| Refusal rate | {_pct(a.get('refusal_rate'))} | {_pct(b.get('refusal_rate'))} | "
        f"{_delta(a.get('refusal_rate'), b.get('refusal_rate'), invert=True)} |"
    )
    lines.append(
        f"| Error rate | {_pct(a.get('error_rate'))} | {_pct(b.get('error_rate'))} | "
        f"{_delta(a.get('error_rate'), b.get('error_rate'), invert=True)} |"
    )
    lines.append(
        f"| Avg cost / case | {_money(a.get('avg_cost_usd'))} | {_money(b.get('avg_cost_usd'))} | "
        f"{_delta(a.get('avg_cost_usd'), b.get('avg_cost_usd'), invert=True)} |"
    )
    lines.append(
        f"| p50 latency | {_num(a.get('p50_latency_ms'), ' ms', 0)} | "
        f"{_num(b.get('p50_latency_ms'), ' ms', 0)} | "
        f"{_delta(a.get('p50_latency_ms'), b.get('p50_latency_ms'), invert=True)} |"
    )
    lines.append(
        f"| p95 latency | {_num(a.get('p95_latency_ms'), ' ms', 0)} | "
        f"{_num(b.get('p95_latency_ms'), ' ms', 0)} | "
        f"{_delta(a.get('p95_latency_ms'), b.get('p95_latency_ms'), invert=True)} |"
    )
    lines.append(
        f"| Noise floor (self-sim) | {_num(a.get('avg_self_similarity'), '', 3)} | "
        f"{_num(b.get('avg_self_similarity'), '', 3)} | — |"
    )
    lines.append("")

    histogram = summary.get("flag_histogram") or {}
    if histogram:
        lines.append("## Flags")
        lines.append("")
        lines.append("| Flag | Cases | Rule |")
        lines.append("| --- | ---: | --- |")
        rules = summary.get("flag_rules", {})
        for flag, count in sorted(histogram.items(), key=lambda kv: -kv[1]):
            lines.append(f"| `{flag}` | {count} | {rules.get(flag, '')} |")
        lines.append("")

    tags = summary.get("tags") or []
    if tags:
        lines.append("## Per-tag breakdown")
        lines.append("")
        lines.append("| Tag | Cases | Flagged | Drift rate | Top flag |")
        lines.append("| --- | ---: | ---: | ---: | --- |")
        for row in tags:
            hist = row.get("flag_histogram") or {}
            top = max(hist, key=hist.get) if hist else "—"
            lines.append(
                f"| `{row['tag']}` | {row['cases']} | {row['flagged_cases']} | "
                f"{_pct(row['drift_rate'])} | `{top}` |"
            )
        lines.append("")

    judge = summary.get("judge")
    if judge and judge.get("judged"):
        lines.append("## Judge")
        lines.append("")
        lines.append(
            f"Judged **{judge['judged']}** cases with `{judge.get('model', '?')}` "
            f"(rubric `{judge.get('rubric_hash', report.get('rubric_hash', ''))}`): "
            f"{judge.get('prefer_a', 0)} prefer A, {judge.get('prefer_b', 0)} prefer B, "
            f"{judge.get('tie', 0)} ties ({judge.get('uncertain', 0)} uncertain after "
            f"position swap)."
        )
        lines.append("")

    drifted = [c for c in report.get("cases", []) if c.get("flags")]
    drifted.sort(key=lambda c: -float(c.get("drift_score") or 0))
    if drifted:
        lines.append("## Worst cases")
        lines.append("")
        for case in drifted[:max_cases]:
            tags_text = ", ".join(case.get("tags") or []) or "untagged"
            lines.append(
                f"### `{case['id']}` — drift **{float(case.get('drift_score') or 0):.2f}** "
                f"(`{tags_text}`)"
            )
            lines.append("")
            lines.append(f"Flags: {', '.join(f'`{f}`' for f in case['flags'])}")
            lines.append("")
            details = case.get("flag_details") or {}
            for flag in case["flags"]:
                if flag in details:
                    lines.append(f"- `{flag}`: {details[flag]}")
            lines.append("")
            for side, label in (("a", "A"), ("b", "B")):
                outputs = case.get(side, {}).get("outputs") or []
                if not outputs:
                    continue
                preview = _truncate(str(outputs[0]), 500)
                lines.append(f"**Model {label}** ({case.get('from_spec') or report.get('from', {}).get('spec', '?') if side == 'a' else report.get('to', {}).get('spec', '?')}):")
                lines.append("")
                lines.append("> " + preview.replace("\n", "\n> "))
                lines.append("")
    else:
        lines.append("## Cases")
        lines.append("")
        lines.append("No cases were flagged. A and B are behaviourally equivalent on this suite. 🎉")
        lines.append("")

    lines.append("## Reproduce")
    lines.append("")
    lines.append("```bash")
    lines.append(report.get("reproduce", {}).get("command") or "modelbump diff ...")
    lines.append("```")
    lines.append("")
    lines.append("| | |")
    lines.append("| --- | --- |")
    lines.append(f"| modelbump | `{report.get('modelbump', {}).get('version', '?')}` |")
    lines.append(f"| Report schema | `{report.get('schema_version', '?')}` |")
    lines.append(f"| Config hash | `{report.get('reproduce', {}).get('config_hash', '')}` |")
    lines.append(f"| Rubric hash | `{report.get('rubric_hash', '')}` |")
    lines.append(f"| Command hash | `{report.get('reproduce', {}).get('command_hash', '')}` |")
    lines.append(
        f"| Cache | {report.get('reproduce', {}).get('cache_hits', 0)} hits / "
        f"{report.get('reproduce', {}).get('cache_misses', 0)} misses |"
    )
    structured = report.get("structured_output") or {}
    if structured.get("a") or structured.get("b"):
        lines.append(
            f"| Structured output | A: `{structured.get('a') or 'none'}` · "
            f"B: `{structured.get('b') or 'none'}` |"
        )
    lines.append("")

    lines.append(
        f"<sub>Generated by [modelbump](https://github.com/iggym/modelbump) "
        f"· {report.get('generated_at', '')}</sub>"
    )
    return "\n".join(lines)


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " …"
