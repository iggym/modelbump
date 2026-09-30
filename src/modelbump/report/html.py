"""HTML report renderer (F-REP-1)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from modelbump.report.html_template import HTML_TEMPLATE


def render_html(report: dict[str, Any], *, title: str | None = None) -> str:
    """Render the single-file HTML report with data inlined."""
    from_spec = report.get("from", {}).get("spec", "?")
    to_spec = report.get("to", {}).get("spec", "?")
    resolved_title = title or f"modelbump: {from_spec} → {to_spec}"
    verdict = "PASS" if report.get("verdict", {}).get("pass") else "FAIL"
    payload = json.dumps(report, ensure_ascii=False, default=str)
    # Guard against a literal "</script>" inside the JSON ending the block early.
    payload = payload.replace("</", "<\\/")
    html = HTML_TEMPLATE.replace("__MODELBUMP_DATA__", payload)
    html = html.replace("__MODELBUMP_TITLE__", f"{resolved_title} [{verdict}]")
    return html


def write_html(report: dict[str, Any], path: str | Path, *, title: str | None = None) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_html(report, title=title), encoding="utf-8")
    return p
