"""Runner and cache tests: resume, budget, cache keying (spec §9)."""

from __future__ import annotations

import pytest

from modelbump.cache import Cache
from modelbump.case import Case
from modelbump.errors import BudgetExceeded
from modelbump.providers.base import Response
from modelbump.runner import RunParams, project_cost, run_suite


def test_cache_key_varies_with_every_component():
    base = dict(
        provider="mock", model="stable", base_url="mock://", fingerprint="fp", params={"t": 0}, sample=0
    )
    key = Cache.make_key(**base)
    assert Cache.make_key(**{**base, "sample": 1}) != key
    assert Cache.make_key(**{**base, "model": "drifty"}) != key
    assert Cache.make_key(**{**base, "fingerprint": "other"}) != key
    assert Cache.make_key(**{**base, "params": {"t": 1}}) != key
    assert Cache.make_key(**{**base, "base_url": "x"}) != key


def test_cache_roundtrip(tmp_path):
    cache = Cache(tmp_path)
    key = Cache.make_key(
        provider="mock", model="m", base_url="b", fingerprint="f", params={}, sample=0
    )
    cache.put(key, Response(text="hello", tokens_in=5, finish_reason="stop"))
    restored = cache.get(key)
    assert restored is not None
    assert restored.text == "hello"
    assert restored.tokens_in == 5
    assert restored.cached is True


def test_cache_never_stores_errors(tmp_path):
    cache = Cache(tmp_path)
    key = Cache.make_key(
        provider="mock", model="m", base_url="b", fingerprint="f", params={}, sample=0
    )
    cache.put(key, Response(error="boom"))
    assert cache.get(key) is None


def test_cache_stats_and_clear(tmp_path):
    cache = Cache(tmp_path)
    key = Cache.make_key(
        provider="mock", model="m", base_url="b", fingerprint="f", params={}, sample=0
    )
    cache.put(key, Response(text="x"))
    stats = cache.stats()
    assert stats.entries == 1
    assert stats.bytes > 0
    assert cache.clear() == 1
    assert cache.stats().entries == 0


def test_cache_disabled_returns_none(tmp_path):
    cache = Cache(tmp_path, enabled=False)
    key = "abc"
    cache.put(key, Response(text="x"))
    assert cache.get(key) is None


@pytest.mark.asyncio
async def test_runner_uses_cache_on_second_pass(tmp_path, simple_cases):
    params = RunParams(samples=2, cache_dir=str(tmp_path), cache_enabled=True, concurrency=4)
    first = await run_suite(
        simple_cases, "mock:stable", "mock:drifty", params, progress=False
    )
    assert first.cache_hits == 0
    second = await run_suite(
        simple_cases, "mock:stable", "mock:drifty", params, progress=False
    )
    assert second.cache_hits == len(simple_cases) * 2 * 2
    assert second.cache_misses == 0


@pytest.mark.asyncio
async def test_runner_resumes_after_interruption(tmp_path, simple_cases):
    """Simulate an interruption: run a subset, then the full suite."""
    params = RunParams(samples=1, cache_dir=str(tmp_path), cache_enabled=True)
    partial = await run_suite(simple_cases[:2], "mock:stable", "mock:drifty", params, progress=False)
    assert partial.cache_hits == 0
    full = await run_suite(simple_cases, "mock:stable", "mock:drifty", params, progress=False)
    # The first two cases come from cache; the rest are fresh.
    assert full.cache_hits == 4
    assert full.cache_misses == (len(simple_cases) - 2) * 2


@pytest.mark.asyncio
async def test_refresh_ignores_cache(tmp_path, simple_cases):
    params = RunParams(samples=1, cache_dir=str(tmp_path), cache_enabled=True)
    await run_suite(simple_cases, "mock:stable", "mock:drifty", params, progress=False)
    params.refresh = True
    refreshed = await run_suite(simple_cases, "mock:stable", "mock:drifty", params, progress=False)
    assert refreshed.cache_hits == 0


def test_project_cost_known_models():
    cases = [Case(id="a", input="hello world " * 100)]
    params = RunParams(samples=2, max_tokens=100)
    cost = project_cost(cases, ["gpt-4.1", "gpt-5"], params)
    assert cost is not None
    assert cost > 0


def test_project_cost_unknown_model_returns_none():
    cases = [Case(id="a", input="hello")]
    # Explicit provider so the spec parses, but the model is not in the registry.
    cost = project_cost(
        cases, ["gpt-4.1", "openai:model-not-in-registry"], RunParams()
    )
    assert cost is None


@pytest.mark.asyncio
async def test_budget_guard_aborts(tmp_path, simple_cases):
    params = RunParams(samples=3, cache_dir=str(tmp_path), max_cost=0.000001)
    with pytest.raises(BudgetExceeded):
        await run_suite(simple_cases, "gpt-4.1", "gpt-5", params, progress=False)


@pytest.mark.asyncio
async def test_runner_returns_both_sides(tmp_path, simple_cases):
    params = RunParams(samples=2, cache_dir=str(tmp_path))
    result = await run_suite(
        simple_cases, "mock:stable", "mock:drifty", params, progress=False
    )
    assert len(result.cases) == len(simple_cases)
    for case_run in result.cases:
        assert len(case_run.a) == 2
        assert len(case_run.b) == 2


@pytest.mark.asyncio
async def test_runner_handles_provider_errors(tmp_path):
    cases = [Case(id="c", input="hi")]
    params = RunParams(samples=1, cache_dir=str(tmp_path))
    result = await run_suite(cases, "mock:stable", "mock:broken", params, progress=False)
    assert all(r.error for r in result.cases[0].b)
    # Errors must not be cached, so a rerun retries them.
    again = await run_suite(cases, "mock:stable", "mock:broken", params, progress=False)
    assert again.cache_misses >= 1
