"""The async runner: fan-out, caching, budget guard, resumability (F-RUN-*)."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from modelbump.cache import Cache
from modelbump.case import Case
from modelbump.errors import BudgetExceeded, ProviderError
from modelbump.metrics import REFUSAL_RE
from modelbump.output import ProgressBar
from modelbump.providers import build_provider
from modelbump.providers.base import (
    BaseProvider,
    RateLimiter,
    Response,
    estimate_input_tokens,
)
from modelbump.registry import registry

ProgressCallback = Callable[[int, int, str], None]


@dataclass
class RunParams:
    samples: int = 3
    concurrency: int = 8
    temperature: float | None = None
    max_tokens: int | None = None
    seed: int | None = None
    rpm: float | None = None
    tpm: float | None = None
    timeout: float = 120.0
    max_retries: int = 4
    max_cost: float | None = None
    cache_enabled: bool = True
    refresh: bool = False
    cache_dir: str | None = None
    keep_raw: bool = False
    api_a: str | None = None
    api_b: str | None = None
    refusal_patterns: list[str] | None = None
    stream: bool = False

    def effective_params(self, case: Case) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        merged.update(case.params or {})
        if self.temperature is not None:
            merged["temperature"] = self.temperature
        if self.max_tokens is not None:
            merged["max_tokens"] = self.max_tokens
        if self.seed is not None:
            merged["seed"] = self.seed
        merged.pop("_expected", None)
        return merged

    def cache_params(self, case: Case) -> dict[str, Any]:
        """Only the parameters that change model behaviour go into the cache key."""
        params = self.effective_params(case)
        return {
            "temperature": params.get("temperature"),
            "max_tokens": params.get("max_tokens"),
            "top_p": params.get("top_p"),
            "seed": params.get("seed"),
            "tools": case.tools,
            "schema": case.schema,
        }


@dataclass
class CaseRun:
    case: Case
    a: list[Response] = field(default_factory=list)
    b: list[Response] = field(default_factory=list)
    from_cache: int = 0
    errors: int = 0


@dataclass
class RunResult:
    cases: list[CaseRun] = field(default_factory=list)
    cache_hits: int = 0
    cache_misses: int = 0
    elapsed_s: float = 0.0
    projected_cost: float | None = None
    provider_info: dict[str, Any] = field(default_factory=dict)


def _refusal_re(patterns: list[str] | None) -> re.Pattern[str]:
    if not patterns:
        return REFUSAL_RE
    try:
        return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)
    except re.error as exc:
        raise ProviderError(f"invalid --refusal-patterns regex: {exc}") from exc


def project_cost(
    cases: list[Case],
    model_specs: list[str],
    params: RunParams,
    *,
    api_overrides: list[str | None] | None = None,
) -> float | None:
    """Estimate the run's cost from input token counts (F-RUN-3).

    Returns ``None`` when any model's pricing is unknown — we never pretend an
    unknown model is free.
    """
    reg = registry()
    total = 0.0
    overrides = api_overrides or [None] * len(model_specs)
    for spec, _api in zip(model_specs, overrides, strict=False):
        from modelbump.providers import parse_model_spec

        parsed = parse_model_spec(spec)
        info = reg.get(parsed.model)
        if info is None or not info.known_pricing:
            return None
        for case in cases:
            tokens_in = estimate_input_tokens(case.messages, case.system)
            max_tokens = params.effective_params(case).get("max_tokens") or 512
            for _ in range(params.samples):
                cost = info.cost(tokens_in, max_tokens, 0)
                total += cost or 0.0
    return total


async def run_suite(
    cases: list[Case],
    from_spec: str,
    to_spec: str,
    params: RunParams,
    *,
    provider_a: BaseProvider | None = None,
    provider_b: BaseProvider | None = None,
    progress: bool = True,
    on_progress: ProgressCallback | None = None,
) -> RunResult:
    """Fan out over cases × samples × 2 models, with cache and budget guard."""
    started = time.perf_counter()
    limiter = RateLimiter(rpm=params.rpm, tpm=params.tpm) if (params.rpm or params.tpm) else None

    prov_a = provider_a or build_provider(
        from_spec,
        api=params.api_a,
        limiter=limiter,
        timeout=params.timeout,
        max_retries=params.max_retries,
        keep_raw=params.keep_raw,
    )
    prov_b = provider_b or build_provider(
        to_spec,
        api=params.api_b,
        limiter=limiter,
        timeout=params.timeout,
        max_retries=params.max_retries,
        keep_raw=params.keep_raw,
    )

    # Budget guard: project before spending anything.
    projected = project_cost(
        cases, [from_spec, to_spec], params, api_overrides=[params.api_a, params.api_b]
    )
    if params.max_cost is not None and projected is not None and projected > params.max_cost:
        raise BudgetExceeded(projected, params.max_cost)

    cache = Cache(params.cache_dir, enabled=params.cache_enabled)

    result = RunResult(projected_cost=projected)
    result.provider_info = {
        "a": {"spec": from_spec, "base_url": prov_a.base_url, "provider": prov_a.name},
        "b": {"spec": to_spec, "base_url": prov_b.base_url, "provider": prov_b.name},
    }

    total_tasks = len(cases) * params.samples * 2
    bar = ProgressBar(total_tasks, label="modelbump") if progress else None

    semaphore = asyncio.Semaphore(max(1, params.concurrency))
    lock = asyncio.Lock()

    async def one(
        provider: BaseProvider, case: Case, sample: int, side: str
    ) -> Response:
        key = Cache.make_key(
            provider=provider.name,
            model=provider.model,
            base_url=provider.base_url,
            fingerprint=case.fingerprint(),
            params=params.cache_params(case),
            sample=sample,
        )
        if params.cache_enabled and not params.refresh:
            cached = cache.get(key)
            if cached is not None:
                async with lock:
                    result.cache_hits += 1
                if bar:
                    bar.advance(note="cache")
                if on_progress:
                    on_progress(bar.done if bar else 0, total_tasks, case.id)
                return cached

        async with semaphore:
            request_params = params.effective_params(case)
            request_params["_sample"] = sample
            request_params["_expected"] = case.expected
            response = await provider.complete(
                messages=case.messages,
                system=case.system,
                tools=case.tools,
                schema=case.schema,
                params=request_params,
            )
        if params.cache_enabled:
            cache.put(key, response)
        async with lock:
            result.cache_misses += 1
            if response.error:
                result.provider_info.setdefault("errors", []).append(
                    {"case": case.id, "side": side, "error": response.error}
                )
        if bar:
            bar.advance(note="error" if response.error else "ok")
        if on_progress:
            on_progress(bar.done if bar else 0, total_tasks, case.id)
        return response

    async def run_case(case: Case) -> CaseRun:
        run = CaseRun(case=case)
        tasks_a = [one(prov_a, case, s, "a") for s in range(params.samples)]
        tasks_b = [one(prov_b, case, s, "b") for s in range(params.samples)]
        responses_a, responses_b = await asyncio.gather(
            asyncio.gather(*tasks_a), asyncio.gather(*tasks_b)
        )
        run.a = list(responses_a)
        run.b = list(responses_b)
        run.from_cache = sum(1 for r in run.a + run.b if r.cached)
        run.errors = sum(1 for r in run.a + run.b if r.error)
        return run

    try:
        gathered = await asyncio.gather(*(run_case(case) for case in cases))
    finally:
        if bar:
            bar.close()
        if provider_a is None:
            await prov_a.aclose()
        if provider_b is None:
            await prov_b.aclose()

    result.cases = list(gathered)
    result.elapsed_s = time.perf_counter() - started
    return result


def load_cases_for_run(cases: Iterable[Case]) -> list[Case]:
    return list(cases)
