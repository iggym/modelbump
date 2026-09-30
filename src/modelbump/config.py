"""Project configuration: ``modelbump.toml``."""

from __future__ import annotations

import hashlib
import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_FILENAMES = ("modelbump.toml", ".modelbump.toml", "modelbump.toml.example")


@dataclass
class Config:
    path: Path | None = None
    thresholds: dict[str, Any] = field(default_factory=dict)
    tag_thresholds: dict[str, dict[str, Any]] = field(default_factory=dict)
    defaults: dict[str, Any] = field(default_factory=dict)
    judge: dict[str, Any] = field(default_factory=dict)
    report: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def hash(self) -> str:
        blob = json.dumps(self.raw, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def default(self, key: str, fallback: Any = None) -> Any:
        return self.defaults.get(key, fallback)


def discover(start: Path | None = None) -> Path | None:
    """Walk up from ``start`` looking for a config file."""
    current = (start or Path.cwd()).resolve()
    for directory in [current, *current.parents]:
        for name in CONFIG_FILENAMES:
            candidate = directory / name
            if candidate.exists():
                return candidate
    return None


def load(path: str | os.PathLike[str] | None = None, start: Path | None = None) -> Config:
    if path is None:
        found = discover(start)
        if found is None:
            return Config()
        path = found
    p = Path(path)
    if not p.exists():
        return Config()
    with p.open("rb") as fh:
        data = tomllib.load(fh)
    thresholds = dict(data.get("thresholds", {}))
    tag_thresholds = dict(thresholds.pop("tag", {}) or {})
    return Config(
        path=p,
        thresholds=thresholds,
        tag_thresholds={str(k): dict(v) for k, v in tag_thresholds.items()},
        defaults=dict(data.get("defaults", {})),
        judge=dict(data.get("judge", {})),
        report=dict(data.get("report", {})),
        raw=data,
    )


DEFAULT_CONFIG_TEMPLATE = """\
# modelbump configuration
# https://github.com/iggym/modelbump

[thresholds]
max_drift_rate = 0.25
min_schema_valid_rate = 0.98
max_schema_valid_drop = 0.02
max_expected_hit_drop = 0.05
max_refusal_increase = 0.05
max_error_rate = 0.02
max_cost_increase = 1.0
max_latency_increase = 1.0
max_judge_prefer_a_rate = 0.6

# Per-tag overrides. Legal and safety cases get a stricter drift budget.
[thresholds.tag.legal]
max_drift_rate = 0.10
max_expected_hit_drop = 0.0

[thresholds.tag.safety]
max_drift_rate = 0.05
max_refusal_increase = 0.0

[defaults]
samples = 3
concurrency = 8
drift_threshold = 0.35
temperature = 0.0

[report]
title = "Model migration diff"
github_summary = true
"""
