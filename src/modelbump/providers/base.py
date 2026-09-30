"""Provider base types: model specs, unified Response, and the retrying client."""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

from modelbump.errors import ProviderError
from modelbump.registry import registry

# HTTP statuses that warrant a retry (F-PROV-6).
RETRY_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504, 529}

_MODEL_SPEC_RE = re.compile(
    r"^(?:(?P<provider>[a-z0-9_\-]+):)?(?P<model>[^@]+?)(?:@(?P<base_url>.+))?$"
)


@dataclass
class ModelSpec:
    """Parsed ``[provider:]model[@base_url]`` (F-PROV-2)."""

    provider: str
    model: str
    base_url: str | None = None
    raw: str = ""

    def __str__(self) -> str:
        base = f"{self.provider}:{self.model}"
        return f"{base}@{self.base_url}" if self.base_url else base

    @property
    def key(self) -> str:
        return f"{self.provider}|{self.model}|{self.base_url or ''}"


def parse_model_spec(spec: str, *, provider_hint: str | None = None) -> ModelSpec:
    """Parse a model spec, inferring the provider from the registry when omitted."""
    raw = spec.strip()
    if not raw:
        raise ProviderError("empty model spec")

    # Azure's grammar is ``azure:<deployment>@<endpoint>``; the endpoint may
    # itself contain no '@', so the generic regex handles it. A bare endpoint
    # URL is also accepted via provider_hint.
    match = _MODEL_SPEC_RE.match(raw)
    if not match:
        raise ProviderError(
            f"cannot parse model spec {raw!r}. Expected [provider:]model[@base_url]"
        )
    provider = match.group("provider")
    model = match.group("model").strip()
    base_url = match.group("base_url")

    if provider is None:
        if provider_hint:
            provider = provider_hint
        else:
            inferred = registry().infer_provider(model)
            if inferred is None:
                raise ProviderError(
                    f"cannot infer a provider for model {model!r}. "
                    f"Use an explicit spec such as 'openai:{model}' or "
                    f"'openai_compat:{model}@https://your-host/v1'."
                )
            provider = inferred

    provider = provider.lower().replace("-", "_")
    if provider == "openai-compatible":
        provider = "openai_compat"
    if base_url:
        base_url = base_url.rstrip("/")
    return ModelSpec(provider=provider, model=model, base_url=base_url, raw=raw)


@dataclass
class ToolCall:
    name: str
    arguments: Any = None
    id: str | None = None

    def arguments_dict(self) -> dict[str, Any]:
        if isinstance(self.arguments, dict):
            return self.arguments
        if isinstance(self.arguments, str):
            try:
                parsed = json.loads(self.arguments)
                return parsed if isinstance(parsed, dict) else {"_value": parsed}
            except json.JSONDecodeError:
                return {}
        return {}

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "arguments": self.arguments, "id": self.id}


@dataclass
class Response:
    """Unified provider response (F-PROV-3)."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tokens_in: int | None = None
    tokens_out: int | None = None
    tokens_cached: int | None = None
    latency_ms: float | None = None
    ttft_ms: float | None = None
    finish_reason: str | None = None
    error: str | None = None
    http_status: int | None = None
    raw: Any = None
    structured_output_mechanism: str | None = None
    translation_warnings: list[str] = field(default_factory=list)
    model: str | None = None
    cached: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def truncated(self) -> bool:
        return (self.finish_reason or "").lower() in ("length", "max_tokens", "max_output_tokens")

    def to_dict(self, include_raw: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "text": self.text,
            "tool_calls": [t.to_dict() for t in self.tool_calls],
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "tokens_cached": self.tokens_cached,
            "latency_ms": self.latency_ms,
            "ttft_ms": self.ttft_ms,
            "finish_reason": self.finish_reason,
            "error": self.error,
            "http_status": self.http_status,
            "structured_output_mechanism": self.structured_output_mechanism,
            "model": self.model,
        }
        if include_raw and self.raw is not None:
            out["raw"] = self.raw
        return out


# ---------------------------------------------------------------------------
# Environment variables for credentials (documented table in docs/providers.md)
# ---------------------------------------------------------------------------

ENV_KEYS: dict[str, tuple[str, ...]] = {
    "openai": ("OPENAI_API_KEY",),
    "openai_responses": ("OPENAI_API_KEY",),
    "openai_chat": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "google": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "azure": ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_VERSION"),
    "bedrock": ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION"),
    "openai_compat": ("OPENAI_COMPAT_API_KEY",),
    "mock": (),
}

BASE_URL_ENV: dict[str, str] = {
    "openai": "OPENAI_BASE_URL",
    "anthropic": "ANTHROPIC_BASE_URL",
    "google": "GEMINI_BASE_URL",
    "openai_compat": "OPENAI_COMPAT_BASE_URL",
}


def mask_secret(value: str | None) -> str:
    """Mask a credential for display: keep a short prefix/suffix only."""
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:4]}{'•' * 6}{value[-4:]}"


def env_status() -> list[dict[str, Any]]:
    """Per-provider credential status with masked values (F-PROV-2 / providers cmd)."""
    rows: list[dict[str, Any]] = []
    for provider, keys in ENV_KEYS.items():
        entries = []
        for key in keys:
            value = os.environ.get(key)
            entries.append(
                {
                    "name": key,
                    "set": bool(value),
                    "masked": mask_secret(value),
                }
            )
        base_url_var = BASE_URL_ENV.get(provider)
        base_url_value = os.environ.get(base_url_var) if base_url_var else None
        rows.append(
            {
                "provider": provider,
                "env": entries,
                "base_url_env": base_url_var,
                "base_url": base_url_value,
                "ready": all(e["set"] for e in entries[:1]) if entries else True,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

class RateLimiter:
    """A minimal async token-bucket for --rpm / --tpm."""

    def __init__(self, rpm: float | None = None, tpm: float | None = None) -> None:
        self.rpm = rpm
        self.tpm = tpm
        self._req_times: list[float] = []
        self._token_events: list[tuple[float, int]] = []
        self._lock = asyncio.Lock()

    async def acquire(self, estimated_tokens: int = 0) -> None:
        if self.rpm is None and self.tpm is None:
            return
        async with self._lock:
            while True:
                now = time.monotonic()
                if self.rpm is not None:
                    self._req_times = [t for t in self._req_times if now - t < 60]
                if self.tpm is not None:
                    self._token_events = [
                        (t, n) for t, n in self._token_events if now - t < 60
                    ]
                wait = 0.0
                if self.rpm is not None and len(self._req_times) >= self.rpm:
                    wait = max(wait, 60 - (now - self._req_times[0]))
                if self.tpm is not None:
                    used = sum(n for _, n in self._token_events)
                    if used + estimated_tokens > self.tpm:
                        if self._token_events:
                            wait = max(wait, 60 - (now - self._token_events[0][0]))
                        else:
                            wait = max(wait, 1.0)
                if wait <= 0:
                    self._req_times.append(now)
                    if estimated_tokens:
                        self._token_events.append((now, estimated_tokens))
                    return
                await asyncio.sleep(min(wait, 5.0))


# ---------------------------------------------------------------------------
# Base provider
# ---------------------------------------------------------------------------

class BaseProvider(ABC):
    """Common HTTP behaviour: retry, backoff, jitter, Retry-After."""

    name = "base"
    supports_tools = True
    supports_schema = True

    def __init__(
        self,
        spec: ModelSpec,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 120.0,
        max_retries: int = 4,
        limiter: RateLimiter | None = None,
        extra: dict[str, Any] | None = None,
        keep_raw: bool = False,
    ) -> None:
        self.spec = spec
        self.model = spec.model
        self.base_url = (base_url or spec.base_url or self.default_base_url()).rstrip("/")
        self.api_key = api_key if api_key is not None else self.resolve_api_key()
        self.timeout = timeout
        self.max_retries = max_retries
        self.limiter = limiter
        self.extra = dict(extra or {})
        self.keep_raw = keep_raw
        self._client: httpx.AsyncClient | None = None

    # -- overridables ----------------------------------------------------
    def default_base_url(self) -> str:
        raise NotImplementedError

    def resolve_api_key(self) -> str | None:
        for key in ENV_KEYS.get(self.name, ()):
            value = os.environ.get(key)
            if value:
                return value
        return None

    def auth_headers(self) -> dict[str, str]:
        return {}

    @abstractmethod
    def build_request(
        self,
        *,
        messages: list[dict[str, Any]],
        system: str | None,
        tools: list[dict[str, Any]] | None,
        schema: dict[str, Any] | None,
        params: dict[str, Any],
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Return ``(method, path, body)`` for the provider call."""

    @abstractmethod
    def parse_response(self, payload: Any, *, http_status: int) -> Response:
        """Map a provider payload onto the unified Response."""

    # -- shared ----------------------------------------------------------
    async def client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"content-type": "application/json", **self.auth_headers()}
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=15.0),
                headers=headers,
                follow_redirects=True,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _retry_delay(self, attempt: int, response: httpx.Response | None) -> float:
        if response is not None:
            retry_after = response.headers.get("retry-after")
            if retry_after:
                try:
                    return max(0.0, float(retry_after))
                except ValueError:
                    pass
        base = min(30.0, 0.5 * (2**attempt))
        return base + random.uniform(0, base * 0.25)

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        schema: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Response:
        params = dict(params or {})
        try:
            method, path, body = self.build_request(
                messages=messages, system=system, tools=tools, schema=schema, params=params
            )
        except Exception as exc:  # a bad configuration must not kill the run
            return Response(error=f"{type(exc).__name__}: {exc}", model=self.model)
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        estimated = estimate_input_tokens(messages, system)
        if self.limiter is not None:
            await self.limiter.acquire(estimated)

        client = await self.client()
        last_error: str | None = None
        last_status: int | None = None
        last_raw: Any = None

        for attempt in range(self.max_retries + 1):
            started = time.perf_counter()
            try:
                if method == "GET":
                    http_response = await client.get(url, params=body)
                else:
                    http_response = await client.request(method, url, json=body)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < self.max_retries:
                    await asyncio.sleep(self._retry_delay(attempt, None))
                    continue
                return Response(
                    error=last_error,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    model=self.model,
                )

            latency_ms = (time.perf_counter() - started) * 1000
            last_status = http_response.status_code

            if http_response.status_code in RETRY_STATUSES and attempt < self.max_retries:
                await asyncio.sleep(self._retry_delay(attempt, http_response))
                continue

            try:
                payload = http_response.json()
            except (json.JSONDecodeError, ValueError):
                payload = None
                last_raw = http_response.text[:2000]

            if http_response.status_code >= 400:
                message = _extract_error_message(payload) or (last_raw or "")
                return Response(
                    error=f"HTTP {http_response.status_code}: {message}"[:600],
                    http_status=http_response.status_code,
                    latency_ms=latency_ms,
                    model=self.model,
                    raw=payload if self.keep_raw else None,
                )

            if payload is None:
                return Response(
                    error="provider returned a non-JSON body",
                    http_status=http_response.status_code,
                    latency_ms=latency_ms,
                    model=self.model,
                )

            try:
                response = self.parse_response(payload, http_status=http_response.status_code)
            except Exception as exc:  # defensive: a shape change must not kill a run
                return Response(
                    error=f"failed to parse provider response: {exc}",
                    http_status=http_response.status_code,
                    latency_ms=latency_ms,
                    model=self.model,
                    raw=payload if self.keep_raw else None,
                )
            response.latency_ms = latency_ms
            response.model = self.model
            if self.keep_raw:
                response.raw = payload
            return response

        return Response(
            error=last_error or f"exhausted retries (last status {last_status})",
            http_status=last_status,
            model=self.model,
        )


def _extract_error_message(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("type") or error)
    if isinstance(error, str):
        return error
    if "message" in payload:
        return str(payload["message"])
    return None


def estimate_input_tokens(messages: list[dict[str, Any]], system: str | None) -> int:
    """A cheap token estimate for budget projection (F-RUN-3).

    Uses ``tiktoken`` when the ``[tokens]`` extra is installed, else a
    characters/4 heuristic which is good enough for a projection guard.
    """
    text = (system or "") + "".join(
        json.dumps(m, ensure_ascii=False, default=str) for m in messages
    )
    try:
        import tiktoken  # type: ignore

        return len(tiktoken.get_encoding("cl100k_base").encode(text))
    except Exception:
        return max(1, len(text) // 4)
