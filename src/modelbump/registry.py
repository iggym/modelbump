"""Model registry: pricing, context windows, and retirement calendar.

Reads from ``surfacelock`` when installed. Falls back to a bundled snapshot so
the tool is fully usable offline (the demo path, tests, and CI never touch the
network). Unknown models always yield ``cost=None`` — never zero.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from modelbump.errors import RegistryError

_DATA = Path(__file__).parent / "data" / "registry_snapshot.json"


@dataclass
class ModelInfo:
    name: str
    provider: str = "unknown"
    input_price_per_mtok: float | None = None
    cached_input_price_per_mtok: float | None = None
    output_price_per_mtok: float | None = None
    context_window: int | None = None
    released: str | None = None
    retirement_date: str | None = None
    successor: str | None = None
    aliases: list[str] = field(default_factory=list)
    source: str = "bundled"

    @property
    def known_pricing(self) -> bool:
        return (
            self.input_price_per_mtok is not None
            and self.output_price_per_mtok is not None
        )

    def cost(
        self, tokens_in: int | None, tokens_out: int | None, tokens_cached: int | None = None
    ) -> float | None:
        """USD cost, or ``None`` when pricing is unknown for this model."""
        if not self.known_pricing:
            return None
        tin = tokens_in or 0
        tout = tokens_out or 0
        cached = tokens_cached or 0
        billed_in = max(0, tin - cached)
        cached_price = (
            self.cached_input_price_per_mtok
            if self.cached_input_price_per_mtok is not None
            else self.input_price_per_mtok
        )
        return (
            billed_in * self.input_price_per_mtok / 1_000_000
            + cached * cached_price / 1_000_000
            + tout * self.output_price_per_mtok / 1_000_000
        )


class Registry:
    """Layered registry: surfacelock (if present) → bundled snapshot → env override."""

    def __init__(self) -> None:
        self._models: dict[str, ModelInfo] = {}
        self._loaded_from: list[str] = []
        self._load()

    # -- loading ---------------------------------------------------------
    def _load(self) -> None:
        self._load_bundled()
        self._load_surfacelock()
        self._load_env_override()

    def _load_bundled(self) -> None:
        if not _DATA.exists():
            raise RegistryError(f"bundled registry snapshot missing: {_DATA}")
        try:
            payload = json.loads(_DATA.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:  # pragma: no cover - corrupt install
            raise RegistryError(f"bundled registry snapshot is corrupt: {exc}") from exc
        for entry in payload.get("models", []):
            info = ModelInfo(
                name=entry["name"],
                provider=entry.get("provider", "unknown"),
                input_price_per_mtok=entry.get("input_price_per_mtok"),
                cached_input_price_per_mtok=entry.get("cached_input_price_per_mtok"),
                output_price_per_mtok=entry.get("output_price_per_mtok"),
                context_window=entry.get("context_window"),
                released=entry.get("released"),
                retirement_date=entry.get("retirement_date"),
                successor=entry.get("successor"),
                aliases=list(entry.get("aliases", [])),
                source="bundled",
            )
            self._register(info)
        self._loaded_from.append(f"bundled@{payload.get('version', 'unknown')}")

    def _load_surfacelock(self) -> None:
        """Overlay surfacelock data when the package is installed."""
        try:
            import surfacelock  # type: ignore
        except Exception:
            return
        getter = None
        for attr in ("get_model", "lookup", "model_info"):
            candidate = getattr(surfacelock, attr, None)
            if callable(candidate):
                getter = candidate
                break
        if getter is None:
            # surfacelock may expose a registry object instead.
            for attr in ("registry", "REGISTRY", "models"):
                candidate = getattr(surfacelock, attr, None)
                if candidate is None:
                    continue
                if callable(getattr(candidate, "get", None)):
                    getter = candidate.get
                    break
        if getter is None:
            return
        for name in list(self._models):
            try:
                entry = getter(name)
            except Exception:
                continue
            if not entry:
                continue
            self._merge_entry(name, entry)
        self._loaded_from.append(f"surfacelock@{getattr(surfacelock, '__version__', '?')}")

    def _load_env_override(self) -> None:
        path = os.environ.get("MODELBUMP_REGISTRY")
        if not path:
            return
        p = Path(path)
        if not p.exists():
            raise RegistryError(f"MODELBUMP_REGISTRY points at a missing file: {path}")
        payload = json.loads(p.read_text(encoding="utf-8"))
        for entry in payload.get("models", []):
            self._merge_entry(entry["name"], entry)
        self._loaded_from.append(f"env:{path}")

    # -- mutation --------------------------------------------------------
    def _merge_entry(self, name: str, entry: Any) -> None:
        def pick(*keys: str) -> Any:
            for k in keys:
                if isinstance(entry, dict) and k in entry:
                    return entry[k]
                if not isinstance(entry, dict) and hasattr(entry, k):
                    return getattr(entry, k)
            return None

        info = self._models.get(name) or ModelInfo(name=name)
        info.provider = pick("provider") or info.provider
        for attr, keys in (
            ("input_price_per_mtok", ("input_price_per_mtok", "input_price", "prompt_price")),
            (
                "cached_input_price_per_mtok",
                ("cached_input_price_per_mtok", "cached_input_price", "cache_read_price"),
            ),
            (
                "output_price_per_mtok",
                ("output_price_per_mtok", "output_price", "completion_price"),
            ),
            ("context_window", ("context_window", "context_length", "max_context")),
            ("released", ("released", "release_date")),
            ("retirement_date", ("retirement_date", "deprecation_date", "retires")),
            ("successor", ("successor", "replacement")),
        ):
            value = pick(*keys)
            if value is not None:
                setattr(info, attr, value)
        aliases = pick("aliases")
        if aliases:
            info.aliases = list(aliases)
        info.source = "surfacelock"
        self._register(info)

    def _register(self, info: ModelInfo) -> None:
        self._models[info.name] = info
        for alias in info.aliases:
            self._models.setdefault(alias, info)

    # -- queries ---------------------------------------------------------
    def get(self, name: str) -> ModelInfo | None:
        if name in self._models:
            return self._models[name]
        # Tolerate provider-qualified names and version suffixes.
        bare = name.split(":", 1)[-1].split("@", 1)[0]
        if bare in self._models:
            return self._models[bare]
        for key, info in self._models.items():
            if bare.startswith(key) or key.startswith(bare):
                return info
        return None

    def provider_of(self, name: str) -> str | None:
        info = self.get(name)
        return info.provider if info else None

    def infer_provider(self, name: str) -> str | None:
        """Infer a provider from a bare model name (F-PROV-2)."""
        explicit = self.provider_of(name)
        if explicit and explicit != "unknown":
            return explicit
        lowered = name.lower()
        heuristics = [
            (("gpt-", "o1", "o3", "o4", "text-embedding", "davinci", "chatgpt"), "openai"),
            (("claude",), "anthropic"),
            (("gemini", "gemma", "palm"), "google"),
            (("llama", "mistral", "mixtral", "command-r", "titan", "nova-"), "bedrock"),
            (("grok",), "xai"),
            (("deepseek",), "deepseek"),
            (("qwen",), "alibaba"),
            (("moonshot", "kimi"), "moonshot"),
        ]
        for prefixes, provider in heuristics:
            if any(p in lowered for p in prefixes):
                return provider
        return None

    def cost(
        self, model: str, tokens_in: int | None, tokens_out: int | None, tokens_cached: int | None = None
    ) -> float | None:
        info = self.get(model)
        if info is None:
            return None
        return info.cost(tokens_in, tokens_out, tokens_cached)

    def all(self) -> list[ModelInfo]:
        seen: dict[int, ModelInfo] = {}
        for info in self._models.values():
            seen[id(info)] = info
        return sorted(seen.values(), key=lambda i: i.name)

    def retirements(self) -> list[ModelInfo]:
        rows = [i for i in self.all() if i.retirement_date]
        return sorted(rows, key=lambda i: i.retirement_date or "")

    @property
    def sources(self) -> list[str]:
        return list(self._loaded_from)

    def context_window(self, model: str) -> int | None:
        info = self.get(model)
        return info.context_window if info else None


_CACHED: Registry | None = None


def registry() -> Registry:
    global _CACHED
    if _CACHED is None:
        _CACHED = Registry()
    return _CACHED


def reset_registry() -> None:
    global _CACHED
    _CACHED = None
