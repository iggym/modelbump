"""The public Python API: ``run_diff`` → ``DiffResult`` (F-API).

This is the single orchestration path. The CLI is a thin shell over it, which
means anything you can do from the command line you can do from Python.
"""

from __future__ import annotations

import asyncio
import platform
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from modelbump import __version__
from modelbump.case import Case
from modelbump.config import Config
from modelbump.config import load as load_config
from modelbump.drift import CaseResult, compute_case_result
from modelbump.errors import ModelbumpError
from modelbump.judge import Judge, JudgeVerdict, judge_summary, load_rubric
from modelbump.judge import rubric_hash as _rubric_hash
from modelbump.providers import build_provider, describe_provider, parse_model_spec
from modelbump.registry import registry
from modelbump.runner import RunParams, project_cost, run_suite
from modelbump.suite import load as load_suite
from modelbump.summary import summarise
from modelbump.thresholds import (
    Thresholds,
    Verdict,
    evaluate,
    thresholds_for_tag,
    thresholds_from_config,
)


@dataclass
class DiffOptions:
    from_model: str
    to_model: str
    suite: str | Path
    samples: int = 3
    tags: list[str] = field(default_factory=list)
    limit: int | None = None
    judge: str | None = None
    judge_all: bool = False
    judge_max_cases: int = 100
    rubric: str | None = None
    drift_threshold: float = 0.35
    temperature: float | None = None
    max_tokens: int | None = None
    seed: int | None = None
    concurrency: int = 8
    rpm: float | None = None
    tpm: float | None = None
    max_cost: float | None = None
    dry_run: bool = False
    no_cache: bool = False
    refresh: bool = False
    cache_dir: str | None = None
    out: str | None = None
    junit: str | None = None
    keep_raw: bool = False
    api_a: str | None = None
    api_b: str | None = None
    refusal_patterns: list[str] | None = None
    strict: bool = False
    config_path: str | None = None
    command: str = ""
    progress: bool = True
    on_progress: Callable[[int, int, str], None] | None = None
    provider_a: Any = None
    provider_b: Any = None


@dataclass
class DiffResult:
    report: dict[str, Any]
    verdict: Verdict
    summary: dict[str, Any]
    results: list[CaseResult] = field(default_factory=list)
    cases: list[Case] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.verdict.passed

    @property
    def drift_rate(self) -> float:
        return float(self.summary.get("drift_rate", 0.0))

    def to_dict(self) -> dict[str, Any]:
        return self.report

    def worst(self, n: int = 5) -> list[CaseResult]:
        from modelbump.drift import DRIFT_FLAGS

        flagged = [r for r in self.results if any(f in DRIFT_FLAGS for f in r.flags)]
        return sorted(flagged, key=lambda r: -r.drift_score)[:n]


def _filter_cases(cases: list[Case], tags: list[str], limit: int | None) -> list[Case]:
    out = cases
    if tags:
        wanted = {t.lower() for t in tags}
        out = [c for c in out if wanted & {t.lower() for t in c.tags}]
    if limit is not None:
        out = out[:limit]
    return out


def _hash_command(command: str) -> str:
    import hashlib

    return hashlib.sha256(command.encode("utf-8")).hexdigest()[:16] if command else ""


async def _run_diff_async(options: DiffOptions) -> DiffResult:
    started = time.time()
    config = load_config(options.config_path)
    thresholds = thresholds_from_config(config)

    suite_result = load_suite(options.suite)
    cases = _filter_cases(suite_result.cases, options.tags, options.limit)
    if not cases:
        raise ModelbumpError(
            "no cases selected. Check --suite, --tag, and --limit."
        )

    run_params = RunParams(
        samples=max(1, options.samples),
        concurrency=max(1, options.concurrency),
        temperature=options.temperature,
        max_tokens=options.max_tokens,
        seed=options.seed,
        rpm=options.rpm,
        tpm=options.tpm,
        max_cost=options.max_cost,
        cache_enabled=not options.no_cache,
        refresh=options.refresh,
        cache_dir=options.cache_dir,
        keep_raw=options.keep_raw,
        api_a=options.api_a,
        api_b=options.api_b,
        refusal_patterns=options.refusal_patterns,
    )

    projected = project_cost(
        cases,
        [options.from_model, options.to_model],
        run_params,
        api_overrides=[options.api_a, options.api_b],
    )

    if options.dry_run:
        report = _build_report(
            options=options,
            config=config,
            thresholds=thresholds,
            summary={
                "total_cases": len(cases),
                "dry_run": True,
                "projected_cost_usd": projected,
                "samples": options.samples,
            },
            results=[],
            judge_info=None,
            cache_hits=0,
            cache_misses=0,
            elapsed=0.0,
            provider_info={
                "a": describe_provider(options.from_model, api=options.api_a),
                "b": describe_provider(options.to_model, api=options.api_b),
            },
            total_cases=len(cases),
        )
        return DiffResult(
            report=report,
            verdict=Verdict(passed=True),
            summary=report["summary"],
            results=[],
            cases=cases,
        )

    run = await run_suite(
        cases,
        options.from_model,
        options.to_model,
        run_params,
        provider_a=options.provider_a,
        provider_b=options.provider_b,
        progress=options.progress,
        on_progress=options.on_progress,
    )

    refusal_re = _compile_refusal(options.refusal_patterns)
    results: list[CaseResult] = []
    for case_run in run.cases:
        results.append(
            compute_case_result(
                case_run.case,
                case_run.a,
                case_run.b,
                model_a=_bare_model(options.from_model),
                model_b=_bare_model(options.to_model),
                drift_threshold=options.drift_threshold,
                refusal_re=refusal_re,
            )
        )

    # -- judge ----------------------------------------------------------
    judge_info: dict[str, Any] | None = None
    judge_verdicts: dict[str, JudgeVerdict] = {}
    if options.judge:
        judge_rubric = load_rubric(options.rubric) if options.rubric else None
        targets = results if options.judge_all else [
            r for r in results if any(f in _free_text_flags() for f in r.flags)
        ]
        targets = targets[: options.judge_max_cases]
        if targets:
            judge_provider = build_provider(options.judge)
            judge = Judge(
                judge_provider,
                model_name=_bare_model(options.judge),
                rubric=judge_rubric,
                max_cases=options.judge_max_cases,
            )
            for result in targets:
                output_a = "\n\n".join(result.a.outputs)
                output_b = "\n\n".join(result.b.outputs)
                verdict = await judge.judge_case(result.case, output_a, output_b)
                judge_verdicts[result.case.id] = verdict
                result.judge = verdict.to_dict()
            await judge_provider.aclose()
            judge_info = judge_summary(judge_verdicts)
            judge_info["model"] = options.judge
            judge_info["rubric_hash"] = judge.rubric_hash
            judge_info["rubric_version"] = judge.rubric_version
            judge_info["judged_case_ids"] = sorted(judge_verdicts)
        elif options.judge:
            judge_info = {
                "judged": 0,
                "model": options.judge,
                "note": "no flagged cases to judge (use --judge-all to judge every case)",
            }

    summary = summarise(results, judge_summary=judge_info)

    tag_thresholds = {
        tag: thresholds_for_tag(thresholds, config, tag) for tag in config.tag_thresholds
    }
    verdict = evaluate(summary, thresholds, summary.get("tags"), tag_thresholds)
    if options.strict:
        for warning in verdict.warnings:
            warning.severity = "violation"
        verdict.violations.extend(verdict.warnings)
        verdict.warnings = []
        verdict.passed = not verdict.violations

    report = _build_report(
        options=options,
        config=config,
        thresholds=thresholds,
        summary=summary,
        results=results,
        judge_info=judge_info,
        cache_hits=run.cache_hits,
        cache_misses=run.cache_misses,
        elapsed=run.elapsed_s,
        provider_info=run.provider_info,
        total_cases=len(cases),
        verdict=verdict,
        projected=projected,
        created=started,
    )
    _write_reports(report, options)
    return DiffResult(
        report=report, verdict=verdict, summary=summary, results=results, cases=cases
    )


def _write_reports(report: dict[str, Any], options: DiffOptions) -> None:
    """Write the report artifacts when the caller asked for an output dir."""
    if not options.out and not options.junit:
        return
    from modelbump.report import render_markdown, write_html, write_json, write_junit

    if options.out:
        out_dir = Path(options.out)
        write_json(report, out_dir / "report.json")
        (out_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")
        write_html(report, out_dir / "report.html")
    if options.junit:
        write_junit(report, options.junit)


def _free_text_flags() -> set[str]:
    return {"semantic", "expected", "length", "language", "refusal", "errors", "truncated"}


def _bare_model(spec: str) -> str:
    try:
        return parse_model_spec(spec).model
    except Exception:
        return spec


def _compile_refusal(patterns: list[str] | None) -> re.Pattern[str] | None:
    if not patterns:
        return None
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)


def _build_report(
    *,
    options: DiffOptions,
    config: Config,
    thresholds: Thresholds,
    summary: dict[str, Any],
    results: list[CaseResult],
    judge_info: dict[str, Any] | None,
    cache_hits: int,
    cache_misses: int,
    elapsed: float,
    provider_info: dict[str, Any],
    total_cases: int,
    verdict: Verdict | None = None,
    projected: float | None = None,
    created: float | None = None,
) -> dict[str, Any]:
    from modelbump import REPORT_SCHEMA_VERSION

    rubric_digest = judge_info.get("rubric_hash") if judge_info else _rubric_hash()
    structured: dict[str, Any] = {}
    if results:
        structured = {
            "a": _first_mechanism(results, "a"),
            "b": _first_mechanism(results, "b"),
        }
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "modelbump": {
            "version": __version__,
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(created or time.time())),
        "from": {
            **_spec_summary(options.from_model, options.api_a),
            **{k: v for k, v in provider_info.get("a", {}).items() if k in ("base_url",)},
        },
        "to": {
            **_spec_summary(options.to_model, options.api_b),
            **{k: v for k, v in provider_info.get("b", {}).items() if k in ("base_url",)},
        },
        "suite": {
            "path": str(options.suite),
            "cases": total_cases,
            "warnings": _suite_warnings(options.suite),
        },
        "samples": options.samples,
        "params": {
            "temperature": options.temperature,
            "max_tokens": options.max_tokens,
            "seed": options.seed,
            "concurrency": options.concurrency,
            "drift_threshold": options.drift_threshold,
            "tags": options.tags,
            "limit": options.limit,
        },
        "thresholds": thresholds.to_dict(),
        "tag_thresholds": {
            tag: overrides for tag, overrides in (config.tag_thresholds or {}).items()
        },
        "judge_model": options.judge,
        "rubric_hash": rubric_digest,
        "structured_output": structured,
        "summary": summary,
        "cases": [
            r.to_dict(include_outputs=True, include_raw=options.keep_raw) for r in results
        ],
        "registry": {
            "sources": registry().sources,
            "pricing_known": {
                _bare_model(options.from_model): _pricing_known(options.from_model),
                _bare_model(options.to_model): _pricing_known(options.to_model),
            },
        },
        "reproduce": {
            "command": options.command,
            "command_hash": _hash_command(options.command),
            "config_path": str(config.path) if config.path else None,
            "config_hash": config.hash,
            "rubric_hash": rubric_digest,
            "cache_hits": cache_hits,
            "cache_misses": cache_misses,
            "projected_cost_usd": projected,
            "elapsed_s": round(elapsed, 3),
            "steps": _reproduce_steps(options),
        },
        "verdict": (verdict.to_dict() if verdict else {"pass": True, "violations": []}),
    }
    return report


def _suite_warnings(suite: str | Path) -> list[str]:
    try:
        return load_suite(suite).warnings
    except Exception:
        return []


def _reproduce_steps(options: DiffOptions) -> list[str]:
    lines = [
        f"pipx install modelbump=={__version__}",
        options.command or (
            f"modelbump diff --from {options.from_model} --to {options.to_model} "
            f"--suite {options.suite} --samples {options.samples}"
        ),
    ]
    return lines


def _pricing_known(model: str) -> bool:
    info = registry().get(_bare_model(model))
    return bool(info and info.known_pricing)


def _spec_summary(spec: str, api: str | None) -> dict[str, Any]:
    try:
        described = describe_provider(spec, api=api)
    except Exception:
        return {"spec": spec}
    return {
        "spec": spec,
        "provider": described.get("provider"),
        "model": described.get("model"),
        "api": described.get("api"),
    }


def _first_mechanism(results: list[CaseResult], side: str) -> str | None:
    """The structured-output mechanism recorded for this side, if uniform."""
    for result in results:
        stats = getattr(result, side)
        if stats.structured_output_mechanism:
            return stats.structured_output_mechanism
    return None


def run_diff(options: DiffOptions | None = None, **kwargs: Any) -> DiffResult:
    """Run a behavioral diff and return a ``DiffResult``.

    Either pass a :class:`DiffOptions` or keyword arguments matching its fields.
    """
    if options is None:
        options = DiffOptions(**kwargs)
    return asyncio.run(_run_diff_async(options))


async def run_diff_async(options: DiffOptions | None = None, **kwargs: Any) -> DiffResult:
    if options is None:
        options = DiffOptions(**kwargs)
    return await _run_diff_async(options)
