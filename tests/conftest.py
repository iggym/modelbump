"""Shared pytest fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from modelbump.case import Case

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def simple_cases() -> list[Case]:
    return [
        Case(id="c1", input="Say hello.", expected="hello", tags=["basic"]),
        Case(id="c2", input="Classify: great", expected="positive", tags=["classification"]),
        Case(
            id="c3",
            input="Extract the order id: order ORD-1",
            schema={
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
            },
            tags=["extraction", "schema"],
        ),
        Case(
            id="c4",
            input="What is the weather in Paris?",
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Get weather",
                        "parameters": {
                            "type": "object",
                            "properties": {"city": {"type": "string"}},
                            "required": ["city"],
                        },
                    },
                }
            ],
            expect_tool="get_weather",
            tags=["tool-use"],
        ),
    ]


@pytest.fixture
def suite_file(tmp_path: Path, simple_cases: list[Case]) -> Path:
    path = tmp_path / "suite.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for case in simple_cases:
            fh.write(json.dumps(case.to_dict(), ensure_ascii=False) + "\n")
    return path


@pytest.fixture
def big_suite(tmp_path: Path) -> Path:
    """A 300-case suite for the performance test."""
    path = tmp_path / "big.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for i in range(300):
            case = {
                "id": f"perf-{i}",
                "tags": [f"tag{i % 6}"],
                "input": f"Case number {i}: summarise the topic {i} briefly.",
                "expected": f"topic {i}",
            }
            fh.write(json.dumps(case) + "\n")
    return path


@pytest.fixture(autouse=True)
def _no_color(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODELBUMP_CACHE_DIR", str(tmp_path / ".cache"))
