"""``modelbump`` command line interface (F-CMD-*).

Exit codes (B3): 0 ok, 1 verdict violated (or --strict warning), 2 usage error,
3 internal error.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from modelbump import __version__
from modelbump.errors import (
    EXIT_INTERNAL,
    EXIT_OK,
    EXIT_USAGE,
    EXIT_VERDICT,
    BudgetExceeded,
    ModelbumpError,
    ProviderError,
    SuiteError,
)
from modelbump.output import (
    C,
    badge,
    bold,
    dim,
    err,
    info,
    init_color,
    ok,
    paint,
    rule,
    table,
    warn,
)

# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="modelbump",
        description="Behavioral diff for model upgrades and deprecations.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  modelbump init\n"
            "  modelbump diff --from mock:stable --to mock:drifty --suite suite.jsonl\n"
            "  modelbump diff --from gpt-4.1 --to gpt-5 --suite suite.jsonl --judge gpt-5-mini\n"
            "  modelbump suite validate suite.jsonl\n"
            "  modelbump calendar --json\n"
            "\nDocs: https://github.com/iggym/modelbump\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"modelbump {__version__}")
    parser.add_argument("--no-color", action="store_true", help="disable colored output")
    parser.add_argument("--json-errors", action="store_true", help="emit errors as JSON")

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # -- diff ------------------------------------------------------------
    diff = sub.add_parser("diff", help="run a behavioral diff between two models")
    diff.add_argument("--from", dest="from_model", required=True, metavar="SPEC",
                      help="baseline model, e.g. gpt-4.1 or mock:stable")
    diff.add_argument("--to", dest="to_model", required=True, metavar="SPEC",
                      help="candidate model, e.g. gpt-5 or mock:drifty")
    diff.add_argument("--suite", required=True, metavar="PATH",
                      help="suite file or directory")
    diff.add_argument("--samples", type=int, default=3, help="samples per case per model (default 3)")
    diff.add_argument("--tag", action="append", default=[], metavar="T",
                      help="only run cases carrying this tag (repeatable)")
    diff.add_argument("--limit", type=int, default=None, help="cap the number of cases")
    diff.add_argument("--judge", default=None, metavar="SPEC",
                      help="enable the pairwise judge on flagged cases")
    diff.add_argument("--judge-all", action="store_true", help="judge every case, not just flagged")
    diff.add_argument("--judge-max-cases", type=int, default=100, help="cap judge calls")
    diff.add_argument("--rubric", default=None, metavar="FILE", help="custom judge rubric")
    diff.add_argument("--drift-threshold", type=float, default=0.35,
                      help="semantic drift threshold (default 0.35)")
    diff.add_argument("--temperature", type=float, default=None)
    diff.add_argument("--max-tokens", type=int, default=None)
    diff.add_argument("--seed", type=int, default=None)
    diff.add_argument("--concurrency", type=int, default=8)
    diff.add_argument("--rpm", type=float, default=None, help="requests-per-minute limiter")
    diff.add_argument("--tpm", type=float, default=None, help="tokens-per-minute limiter")
    diff.add_argument("--max-cost", type=float, default=None, metavar="USD",
                      help="abort if the projected cost exceeds this")
    diff.add_argument("--dry-run", action="store_true", help="print the projection and exit")
    diff.add_argument("--no-cache", action="store_true")
    diff.add_argument("--refresh", action="store_true", help="ignore cached responses")
    diff.add_argument("--cache-dir", default=None)
    diff.add_argument("--out", default=None, metavar="DIR", help="output directory for reports")
    diff.add_argument("--junit", default=None, metavar="FILE", help="also write a JUnit XML report")
    diff.add_argument("--github-summary", action="store_true",
                      help="append the Markdown report to $GITHUB_STEP_SUMMARY")
    diff.add_argument("--format", choices=["text", "json", "markdown"], default="text")
    diff.add_argument("--api", choices=["chat", "responses"], default=None,
                      help="override the OpenAI transport for both models")
    diff.add_argument("--api-from", choices=["chat", "responses"], default=None)
    diff.add_argument("--api-to", choices=["chat", "responses"], default=None)
    diff.add_argument("--keep-raw", action="store_true", help="store raw provider payloads")
    diff.add_argument("--refusal-patterns", default=None,
                      help="comma-separated regexes overriding the refusal family")
    diff.add_argument("--strict", action="store_true", help="treat warnings as violations")
    diff.add_argument("--config", default=None, metavar="FILE", help="path to modelbump.toml")
    diff.add_argument("--no-progress", action="store_true")
    diff.add_argument("--quiet", action="store_true")

    # -- init ------------------------------------------------------------
    init = sub.add_parser("init", help="scaffold a project with an example suite")
    init.add_argument("dir", nargs="?", default=".", help="target directory (default: .)")
    init.add_argument("--force", action="store_true", help="overwrite existing files")

    # -- suite -----------------------------------------------------------
    suite = sub.add_parser("suite", help="suite utilities")
    suite_sub = suite.add_subparsers(dest="suite_command", metavar="<action>")

    sv = suite_sub.add_parser("validate", help="validate a suite")
    sv.add_argument("path")
    sv.add_argument("--json", action="store_true")

    ss = suite_sub.add_parser("stats", help="print suite statistics")
    ss.add_argument("path")
    ss.add_argument("--json", action="store_true")

    st = suite_sub.add_parser("from-traces", help="import cases from production traces")
    st.add_argument("path", help="OTel GenAI JSONL, or Langfuse JSON/CSV export")
    st.add_argument("--out", default="suite.jsonl", help="output suite path")
    st.add_argument("--sample", type=int, default=None, help="sample N traces")
    st.add_argument("--seed", type=int, default=0)
    st.add_argument("--json", action="store_true")
    st.add_argument("--no-redact", action="store_true",
                    help="disable PII redaction (not recommended)")

    # -- report ----------------------------------------------------------
    report = sub.add_parser("report", help="re-render reports from report.json")
    report.add_argument("dir", help="directory containing report.json")
    report.add_argument("--format", choices=["html", "md", "markdown", "json"], default="html")
    report.add_argument("--out", default=None)

    # -- compare ---------------------------------------------------------
    compare = sub.add_parser("compare", help="compare two report.json files")
    compare.add_argument("report_a", help="baseline report.json")
    compare.add_argument("report_b", help="newer report.json")
    compare.add_argument("--json", action="store_true")

    # -- cache -----------------------------------------------------------
    cache = sub.add_parser("cache", help="cache utilities")
    cache_sub = cache.add_subparsers(dest="cache_command", metavar="<action>")
    cs = cache_sub.add_parser("stats", help="show cache statistics")
    cs.add_argument("--json", action="store_true")
    cs.add_argument("--cache-dir", default=None)
    cc = cache_sub.add_parser("clear", help="delete the cache")
    cc.add_argument("--cache-dir", default=None)
    cc.add_argument("--yes", action="store_true")

    # -- calendar --------------------------------------------------------
    cal = sub.add_parser("calendar", help="model retirement calendar")
    cal.add_argument("--json", action="store_true")

    # -- providers -------------------------------------------------------
    prov = sub.add_parser("providers", help="list providers and credential status")
    prov.add_argument("--json", action="store_true")

    # -- doctor ----------------------------------------------------------
    doc = sub.add_parser("doctor", help="check the local setup")
    doc.add_argument("--json", action="store_true")
    doc.add_argument("--config", default=None)

    return parser


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    init_color(False if getattr(args, "no_color", False) else None)

    if args.command is None:
        parser.print_help()
        return EXIT_USAGE

    handlers = {
        "diff": cmd_diff,
        "init": cmd_init,
        "suite": cmd_suite,
        "report": cmd_report,
        "compare": cmd_compare,
        "cache": cmd_cache,
        "calendar": cmd_calendar,
        "providers": cmd_providers,
        "doctor": cmd_doctor,
    }
    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        return EXIT_USAGE

    try:
        return handler(args)
    except BudgetExceeded as exc:
        if getattr(args, "json_errors", False):
            print(json.dumps({"error": "budget_exceeded", "message": str(exc),
                              "projected": exc.projected, "limit": exc.limit}))
        else:
            print(err(f"budget exceeded: {exc}"), file=sys.stderr)
            print(dim("raise --max-cost or trim the suite with --limit / --tag"), file=sys.stderr)
        return EXIT_USAGE
    except (SuiteError, ProviderError, ModelbumpError) as exc:
        if getattr(args, "json_errors", False):
            print(json.dumps({"error": type(exc).__name__, "message": str(exc)}))
        else:
            print(err(f"error: {exc}"), file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:
        print(warn("\ninterrupted — rerun to resume from cache"), file=sys.stderr)
        return EXIT_INTERNAL
    except Exception as exc:  # noqa: BLE001
        if os.environ.get("MODELBUMP_DEBUG"):
            raise
        if getattr(args, "json_errors", False):
            print(json.dumps({"error": "internal", "message": str(exc)}))
        else:
            print(err(f"internal error: {exc}"), file=sys.stderr)
            print(dim("set MODELBUMP_DEBUG=1 for a traceback"), file=sys.stderr)
        return EXIT_INTERNAL


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------

def cmd_diff(args: argparse.Namespace) -> int:
    from modelbump.api import DiffOptions, run_diff_async
    from modelbump.report import render_markdown, write_json, write_junit

    refusal = None
    if args.refusal_patterns:
        refusal = [p for p in args.refusal_patterns.split(",") if p.strip()]

    command = "modelbump " + " ".join(_redact_argv(sys.argv[1:]))

    options = DiffOptions(
        from_model=args.from_model,
        to_model=args.to_model,
        suite=args.suite,
        samples=args.samples,
        tags=args.tag,
        limit=args.limit,
        judge=args.judge,
        judge_all=args.judge_all,
        judge_max_cases=args.judge_max_cases,
        rubric=args.rubric,
        drift_threshold=args.drift_threshold,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        seed=args.seed,
        concurrency=args.concurrency,
        rpm=args.rpm,
        tpm=args.tpm,
        max_cost=args.max_cost,
        dry_run=args.dry_run,
        no_cache=args.no_cache,
        refresh=args.refresh,
        cache_dir=args.cache_dir,
        out=args.out,
        junit=args.junit,
        keep_raw=args.keep_raw,
        api_a=args.api_from or args.api,
        api_b=args.api_to or args.api,
        refusal_patterns=refusal,
        strict=args.strict,
        config_path=args.config,
        command=command,
        progress=not args.no_progress and args.format == "text",
    )

    result = asyncio.run(run_diff_async(options))

    if args.dry_run:
        _print_dry_run(result, args)
        return EXIT_OK

    if args.format == "json":
        print(json.dumps(result.report, indent=2, ensure_ascii=False, default=str))
    elif args.format == "markdown":
        print(render_markdown(result.report))
    else:
        if not args.quiet:
            print(render_text_summary(result))

    # Write reports
    out_dir = Path(args.out) if args.out else Path(".modelbump/reports") / _report_slug(
        args.from_model, args.to_model
    )
    json_path = write_json(result.report, out_dir / "report.json")
    md = render_markdown(result.report)
    (out_dir / "report.md").write_text(md, encoding="utf-8")
    from modelbump.report import write_html

    write_html(result.report, out_dir / "report.html")
    if args.junit:
        write_junit(result.report, args.junit)

    if args.github_summary:
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as fh:
                fh.write(md + "\n")

    if not args.quiet and args.format == "text":
        print()
        print(dim("reports written:"))
        print(f"  {info(str(json_path))}")
        print(f"  {info(str(out_dir / 'report.md'))}")
        print(f"  {info(str(out_dir / 'report.html'))}")
        if args.junit:
            print(f"  {info(args.junit)}")
        if result.passed:
            print(ok("verdict: PASS") + dim(" (no threshold violations)"))
        else:
            print(err("verdict: FAIL") + dim(f" ({len(result.verdict.violations)} violations)"))

    return EXIT_OK if result.passed else EXIT_VERDICT


def _redact_argv(argv: list[str]) -> list[str]:
    """Avoid leaking credentials that were passed as flags."""
    out = []
    skip_next = False
    for _i, token in enumerate(argv):
        if skip_next:
            out.append("<redacted>")
            skip_next = False
            continue
        out.append(token)
        if token in ("--api-key", "--key"):
            skip_next = True
    return out


def _report_slug(from_model: str, to_model: str) -> str:
    from modelbump.case import slugify

    return f"{slugify(from_model)}__to__{slugify(to_model)}"


def _print_dry_run(result: Any, args: argparse.Namespace) -> None:
    summary = result.summary
    projected = summary.get("projected_cost_usd")
    print(bold("modelbump dry run"))
    print()
    print(f"  cases:     {summary.get('total_cases')}")
    print(f"  samples:   {summary.get('samples')}")
    print(f"  models:    {args.from_model} → {args.to_model}")
    if projected is None:
        print(
            "  projected: "
            + warn("unknown — pricing is unavailable for at least one model")
        )
        print(dim("             the run will proceed; cost will be reported as null where unknown"))
    else:
        print(f"  projected: ${projected:.4f}")
    if args.max_cost is not None:
        within = projected is None or projected <= args.max_cost
        status = ok("within budget") if within else err("exceeds --max-cost")
        print(f"  budget:    ${args.max_cost:.4f} ({status})")


def render_text_summary(result: Any) -> str:
    summary = result.summary
    report = result.report
    a = summary.get("a", {})
    b = summary.get("b", {})
    lines: list[str] = []

    from_spec = report.get("from", {}).get("spec", "?")
    to_spec = report.get("to", {}).get("spec", "?")
    lines.append("")
    lines.append(bold("modelbump") + dim("  behavioral diff"))
    lines.append(rule())
    lines.append(
        f"  {paint(from_spec, C.BRIGHT_CYAN)}  "
        + paint("→", C.BOLD)
        + f"  {paint(to_spec, C.BRIGHT_MAGENTA)}"
    )
    lines.append(
        dim(
            f"  suite: {report.get('suite', {}).get('path')} · "
            f"{summary.get('total_cases')} cases · {report.get('samples')} samples each"
        )
    )
    lines.append("")

    def pct(value: Any) -> str:
        return "—" if value is None else f"{value * 100:5.1f}%"

    def ms(value: Any) -> str:
        return "—" if value is None else f"{value:,.0f}ms"

    def usd(value: Any) -> str:
        return "—" if value is None else f"${value:.4f}"

    rows = [
        ["schema-valid", pct(a.get("schema_valid_rate")), pct(b.get("schema_valid_rate"))],
        ["expected-hit", pct(a.get("expected_hit_rate")), pct(b.get("expected_hit_rate"))],
        ["refusal", pct(a.get("refusal_rate")), pct(b.get("refusal_rate"))],
        ["error", pct(a.get("error_rate")), pct(b.get("error_rate"))],
        ["avg cost", usd(a.get("avg_cost_usd")), usd(b.get("avg_cost_usd"))],
        ["p50 latency", ms(a.get("p50_latency_ms")), ms(b.get("p50_latency_ms"))],
        ["self-sim", _fmt3(a.get("avg_self_similarity")), _fmt3(b.get("avg_self_similarity"))],
    ]
    lines.append(table(["metric", "A", "B"], rows, aligns=["left", "right", "right"]))

    drift_rate = summary.get("drift_rate", 0.0)
    lines.append("")
    lines.append(
        f"  drifted: {bold(str(summary.get('flagged_cases')))}/{summary.get('total_cases')} "
        f"cases  ({pct(drift_rate)} drift rate)   "
        f"mean score {summary.get('mean_drift_score', 0):.2f}  "
        f"max {summary.get('max_drift_score', 0):.2f}"
    )

    histogram = summary.get("flag_histogram") or {}
    if histogram:
        lines.append("")
        lines.append("  flags: " + "  ".join(
            paint(f"{flag}×{count}", C.BRIGHT_YELLOW)
            for flag, count in sorted(histogram.items(), key=lambda kv: -kv[1])
        ))

    tags = summary.get("tags") or []
    if tags:
        lines.append("")
        lines.append(dim("  by tag:"))
        tag_rows = []
        for row in tags[:8]:
            rate = row["drift_rate"]
            colored = (
                paint(f"{rate * 100:5.1f}%", C.BRIGHT_RED)
                if rate > 0.25
                else paint(f"{rate * 100:5.1f}%", C.BRIGHT_YELLOW)
                if rate > 0
                else paint(f"{rate * 100:5.1f}%", C.BRIGHT_GREEN)
            )
            tag_rows.append([row["tag"], str(row["cases"]), str(row["flagged_cases"]), colored])
        lines.append(table(["tag", "cases", "flagged", "drift"], tag_rows,
                           aligns=["left", "right", "right", "right"]))

    judge = summary.get("judge")
    if judge and judge.get("judged"):
        lines.append("")
        lines.append(
            dim(
                f"  judge ({judge.get('model')}): {judge.get('prefer_a')} prefer A · "
                f"{judge.get('prefer_b')} prefer B · {judge.get('tie')} tie · "
                f"{judge.get('uncertain')} uncertain"
            )
        )

    verdict = result.verdict
    lines.append("")
    lines.append(rule())
    if verdict.passed:
        lines.append(f"  {badge(True)}  {ok('no threshold violations')}")
    else:
        lines.append(f"  {badge(False)}  {len(verdict.violations)} violation(s)")
        for violation in verdict.violations:
            lines.append(f"    {err('•')} {bold(violation.rule)}: {violation.message}")
    if verdict.warnings:
        for warning in verdict.warnings:
            lines.append(f"    {warn('•')} {bold(warning.rule)}: {warning.message}")
    lines.append(
        dim(
            f"  cache: {report.get('reproduce', {}).get('cache_hits', 0)} hits / "
            f"{report.get('reproduce', {}).get('cache_misses', 0)} misses · "
            f"{report.get('reproduce', {}).get('elapsed_s', 0)}s"
        )
    )
    return "\n".join(lines)


def _fmt3(value: Any) -> str:
    return "—" if value is None else f"{value:.3f}"


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> int:
    from modelbump.suite.init import init_project

    written = init_project(args.dir, force=args.force)
    target = Path(args.dir).resolve()
    if not written:
        print(warn(f"nothing written — files already exist in {target} (use --force)"))
        return EXIT_OK
    print(ok("created modelbump project") + dim(f" in {target}"))
    for path in written:
        print(f"  {info(path.name)}")
    print()
    print(dim("next:"))
    print(f"  {bold('modelbump diff --from mock:stable --to mock:drifty --suite suite.jsonl')}")
    print(dim("  (the mock: provider runs fully offline — no API keys needed)"))
    return EXIT_OK


# ---------------------------------------------------------------------------
# suite
# ---------------------------------------------------------------------------

def cmd_suite(args: argparse.Namespace) -> int:
    if args.suite_command == "validate":
        return _suite_validate(args)
    if args.suite_command == "stats":
        return _suite_stats(args)
    if args.suite_command == "from-traces":
        return _suite_from_traces(args)
    print("usage: modelbump suite {validate|stats|from-traces} ...", file=sys.stderr)
    return EXIT_USAGE


def _suite_validate(args: argparse.Namespace) -> int:
    from modelbump.suite.validate import validate_suite

    report = validate_suite(args.path)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
        return EXIT_OK if report.ok else EXIT_USAGE

    print(bold(f"validating {args.path}"))
    print()
    print(f"  cases:      {report.total}")
    print(f"  with schema:{report.with_schema}")
    print(f"  with tools: {report.with_tools}")
    print(f"  with expect:{report.with_expected}")
    print(f"  multi-turn: {report.multiturn}")
    print(f"  largest:    {report.largest_input_chars:,} chars")
    if report.tag_counts:
        print("  tags:       " + ", ".join(
            f"{t}({n})" for t, n in sorted(report.tag_counts.items(), key=lambda kv: -kv[1])
        ))
    for warning in report.warnings:
        print(warn(f"  warning: {warning}"))
    print()
    if report.duplicate_ids:
        print(err(f"  duplicate ids: {', '.join(report.duplicate_ids)}"))
    for issue in report.issues:
        print(err(f"  {issue.case_id}:"))
        for problem in issue.problems:
            print(f"    - {problem}")
    if report.ok:
        print(ok("  ✓ suite is valid"))
        return EXIT_OK
    print(err(f"  ✗ {len(report.issues)} case(s) invalid, {len(report.duplicate_ids)} duplicate id(s)"))
    return EXIT_USAGE


def _suite_stats(args: argparse.Namespace) -> int:
    from modelbump.suite import load
    from modelbump.suite.validate import suite_stats

    result = load(args.path)
    stats = suite_stats(result.cases)
    if args.json:
        print(json.dumps({"path": str(args.path), **stats}, indent=2))
        return EXIT_OK
    print(bold(f"suite stats: {args.path}"))
    print()
    print(f"  total cases:       {stats['total']}")
    print(f"  with expected:     {stats['with_expected']}")
    print(f"  with schema:       {stats['with_schema']}")
    print(f"  with tools:        {stats['with_tools']}")
    print(f"  multi-turn:        {stats['multiturn']}")
    print(f"  total input chars: {stats['total_input_chars']:,}")
    print(f"  largest input:     {stats['largest_input_chars']:,} chars")
    tag_counts = stats["tag_counts"]
    if tag_counts:
        print()
        print("  tags:")
        rows = [[tag, str(count)] for tag, count in tag_counts.items()]
        print(table(["tag", "cases"], rows, aligns=["left", "right"]))
    for warning in result.warnings:
        print(warn(f"  warning: {warning}"))
    return EXIT_OK


def _suite_from_traces(args: argparse.Namespace) -> int:
    from modelbump.suite.traces import load_traces, write_jsonl

    traces = load_traces(args.path, sample=args.sample, seed=args.seed)

    if args.no_redact:
        # Re-import without redaction by writing what we already redacted is not
        # enough — reload the raw file with redaction disabled.
        traces = _load_traces_unredacted(args.path, args.sample, args.seed)

    if not traces.cases:
        print(err(f"no cases could be imported from {args.path}"), file=sys.stderr)
        for warning in traces.warnings[:10]:
            print(dim(f"  {warning}"), file=sys.stderr)
        return EXIT_USAGE

    out = write_jsonl(traces.cases, args.out)

    if args.json:
        print(json.dumps({**traces.to_dict(), "out": str(out)}, indent=2))
    else:
        print(ok("imported traces") + dim(f" ({traces.source_format})"))
        print(f"  imported:  {len(traces.cases)} cases")
        print(f"  skipped:   {traces.skipped}")
        print(f"  written:   {info(str(out))}")
        red = traces.redaction
        if red.total:
            print(
                warn(f"  redacted:  {red.total} PI matches (")
                + ", ".join(f"{k}×{v}" for k, v in sorted(red.counts.items()))
                + ")"
            )
        else:
            print(dim("  redacted:  0 matches"))
        for warning in traces.warnings[:8]:
            print(dim(f"  note: {warning}"))
    return EXIT_OK


def _load_traces_unredacted(path: str, sample: int | None, seed: int):
    """Import traces with redaction disabled, for users who accept the risk."""
    import modelbump.suite.traces as traces_mod
    from modelbump.suite.redact import RedactionReport

    original_redact_value = traces_mod.redact_value
    traces_mod.redact_value = lambda value, report=None: value  # type: ignore[assignment]
    try:
        result = traces_mod.load_traces(path, sample=sample, seed=seed)
        result.redaction = RedactionReport(counts={})
        result.warnings.insert(0, "PII redaction was disabled at your request (--no-redact)")
    finally:
        traces_mod.redact_value = original_redact_value  # type: ignore[assignment]
    return result


# ---------------------------------------------------------------------------
# report / compare / cache / calendar / providers / doctor
# ---------------------------------------------------------------------------

def cmd_report(args: argparse.Namespace) -> int:
    from modelbump.report import render_html, render_markdown
    from modelbump.report.json_report import read_json

    directory = Path(args.dir)
    json_path = directory / "report.json" if directory.is_dir() else directory
    if not json_path.exists():
        print(err(f"no report.json at {json_path}"), file=sys.stderr)
        return EXIT_USAGE
    report = read_json(json_path)

    if args.format == "json":
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        return EXIT_OK
    if args.format == "markdown" or args.format == "md":
        text = render_markdown(report)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
            print(ok(f"wrote {args.out}"))
        else:
            print(text)
        return EXIT_OK
    html = render_html(report)
    out = Path(args.out) if args.out else json_path.parent / "report.html"
    out.write_text(html, encoding="utf-8")
    print(ok(f"wrote {out}"))
    return EXIT_OK


def cmd_compare(args: argparse.Namespace) -> int:
    from modelbump.compare import compare_reports, render_json, render_text
    from modelbump.report.json_report import read_json

    before = read_json(args.report_a)
    after = read_json(args.report_b)
    result = compare_reports(before, after)
    if args.json:
        print(render_json(result))
    else:
        print(render_text(result))
    return EXIT_OK


def cmd_cache(args: argparse.Namespace) -> int:
    from modelbump.cache import Cache, default_cache_dir

    if args.cache_command == "clear":
        cache = Cache(args.cache_dir)
        if not args.yes:
            print(dim(f"about to clear {cache.dir}"))
            try:
                answer = input("clear the cache? [y/N] ").strip().lower()
            except EOFError:
                answer = "n"
            if answer not in ("y", "yes"):
                print(dim("aborted"))
                return EXIT_OK
        count = cache.clear()
        print(ok(f"cleared {count} cached responses from {cache.dir}"))
        return EXIT_OK

    if args.cache_command == "stats" or args.cache_command is None:
        cache_dir = args.cache_dir or default_cache_dir()
        cache = Cache(cache_dir)
        stats = cache.stats()
        if getattr(args, "json", False):
            print(json.dumps({"dir": str(cache_dir), **stats.to_dict()}, indent=2))
            return EXIT_OK
        print(bold("modelbump cache"))
        print(f"  location: {cache_dir}")
        print(f"  entries:  {stats.entries}")
        print(f"  size:     {stats.to_dict()['megabytes']} MB")
        return EXIT_OK

    print("usage: modelbump cache {stats|clear}", file=sys.stderr)
    return EXIT_USAGE


def cmd_calendar(args: argparse.Namespace) -> int:
    from modelbump.calendar import calendar_rows, render_json, render_text

    rows = calendar_rows()
    if args.json:
        print(render_json(rows))
    else:
        print(bold("model retirement calendar"))
        print()
        print(render_text(rows))
        print()
        print(dim("source: surfacelock registry overlaying the bundled snapshot"))
    return EXIT_OK


def cmd_providers(args: argparse.Namespace) -> int:
    from modelbump.providers import KNOWN_COMPAT_BASE_URLS
    from modelbump.providers.base import env_status

    rows = env_status()
    if args.json:
        print(json.dumps({"providers": rows, "compat_hosts": KNOWN_COMPAT_BASE_URLS}, indent=2))
        return EXIT_OK

    print(bold("modelbump providers"))
    print()
    table_rows = []
    for row in rows:
        env = row["env"]
        first = env[0] if env else None
        if first and first["set"]:
            status = ok(f"set ({first['masked']})")
        elif env:
            status = dim("not set")
        else:
            status = dim("no credentials needed")
        table_rows.append([row["provider"], status, ", ".join(e["name"] for e in env) or "—"])
    print(table(["provider", "status", "env vars"], table_rows,
                aligns=["left", "left", "left"]))

    print()
    print(dim("known openai-compatible hosts:"))
    for host, url in sorted(KNOWN_COMPAT_BASE_URLS.items()):
        print(f"  {host:<12} {dim(url)}")
    print()
    print(dim("spec grammar: [provider:]model[@base_url]  e.g. openai_compat:llama-3.1-8b@http://localhost:8000/v1"))
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace) -> int:
    from modelbump.doctor import render_json, render_text, run_checks

    result = run_checks(args.config)
    if args.json:
        print(render_json(result))
    else:
        print(render_text(result))
    return EXIT_OK if result["overall"] != "error" else EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
