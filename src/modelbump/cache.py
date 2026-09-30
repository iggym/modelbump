"""On-disk cache for provider responses (F-RUN-2).

Keyed by ``(provider, model, base_url, case fingerprint, params, sample)``.
Errors are never cached, so a transient failure cannot be mistaken for a result.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from modelbump.providers.base import Response, ToolCall

CACHE_DIRNAME = ".modelbump/cache"


def default_cache_dir(root: str | Path | None = None) -> Path:
    base = Path(root) if root else Path.cwd()
    return base / CACHE_DIRNAME


@dataclass
class CacheStats:
    entries: int = 0
    bytes: int = 0
    oldest: float | None = None
    newest: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "entries": self.entries,
            "bytes": self.bytes,
            "megabytes": round(self.bytes / 1_048_576, 3),
            "oldest": self.oldest,
            "newest": self.newest,
        }


class Cache:
    def __init__(self, directory: str | Path | None = None, *, enabled: bool = True) -> None:
        self.dir = Path(directory) if directory else default_cache_dir()
        self.enabled = enabled
        self.hits = 0
        self.misses = 0
        if self.enabled:
            self.dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def make_key(
        *,
        provider: str,
        model: str,
        base_url: str | None,
        fingerprint: str,
        params: dict[str, Any],
        sample: int,
    ) -> str:
        blob = json.dumps(
            {
                "provider": provider,
                "model": model,
                "base_url": base_url,
                "fingerprint": fingerprint,
                "params": params,
                "sample": sample,
            },
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _path(self, key: str) -> Path:
        return self.dir / key[:2] / f"{key}.json"

    def get(self, key: str) -> Response | None:
        if not self.enabled:
            return None
        path = self._path(key)
        if not path.exists():
            self.misses += 1
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self.misses += 1
            return None
        if payload.get("error"):
            # Defensive: an error should never have been written, but if one is
            # found treat it as a miss rather than replaying a failure.
            self.misses += 1
            return None
        self.hits += 1
        response = Response(
            text=payload.get("text", ""),
            tool_calls=[
                ToolCall(name=t.get("name", ""), arguments=t.get("arguments"), id=t.get("id"))
                for t in payload.get("tool_calls", [])
            ],
            tokens_in=payload.get("tokens_in"),
            tokens_out=payload.get("tokens_out"),
            tokens_cached=payload.get("tokens_cached"),
            latency_ms=payload.get("latency_ms"),
            ttft_ms=payload.get("ttft_ms"),
            finish_reason=payload.get("finish_reason"),
            structured_output_mechanism=payload.get("structured_output_mechanism"),
            model=payload.get("model"),
            raw=payload.get("raw"),
            cached=True,
        )
        return response

    def put(self, key: str, response: Response) -> None:
        if not self.enabled or response.error:
            return
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "text": response.text,
            "tool_calls": [t.to_dict() for t in response.tool_calls],
            "tokens_in": response.tokens_in,
            "tokens_out": response.tokens_out,
            "tokens_cached": response.tokens_cached,
            "latency_ms": response.latency_ms,
            "ttft_ms": response.ttft_ms,
            "finish_reason": response.finish_reason,
            "structured_output_mechanism": response.structured_output_mechanism,
            "model": response.model,
            "raw": response.raw,
            "stored_at": time.time(),
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def stats(self) -> CacheStats:
        stats = CacheStats()
        if not self.dir.exists():
            return stats
        for file in self.dir.rglob("*.json"):
            try:
                info = file.stat()
            except OSError:
                continue
            stats.entries += 1
            stats.bytes += info.st_size
            stats.oldest = info.st_mtime if stats.oldest is None else min(stats.oldest, info.st_mtime)
            stats.newest = info.st_mtime if stats.newest is None else max(stats.newest, info.st_mtime)
        return stats

    def clear(self) -> int:
        if not self.dir.exists():
            return 0
        count = sum(1 for _ in self.dir.rglob("*.json"))
        shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True, exist_ok=True)
        return count
