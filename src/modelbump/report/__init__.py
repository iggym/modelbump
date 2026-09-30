"""Report writers: JSON, Markdown (PR-safe), HTML (single file), JUnit XML."""

from __future__ import annotations

from modelbump.report.html import render_html, write_html
from modelbump.report.json_report import write_json
from modelbump.report.junit import write_junit
from modelbump.report.markdown import render_markdown

__all__ = ["write_json", "write_junit", "render_markdown", "render_html", "write_html"]
