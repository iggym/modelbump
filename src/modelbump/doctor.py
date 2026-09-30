"""``modelbump doctor`` — environment and setup diagnostics."""

from __future__ import annotations

import importlib.util
import os
import platform
from typing import Any

from modelbump import __version__
from modelbump.providers.base import env_status
from modelbump.registry import registry


def run_checks(config_path: str | None = None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"name": name, "status": status, "detail": detail})

    # Python version (requires-python is >= 3.11, so this is informational)
    add("python", "ok", f"{platform.python_version()} (>= 3.11)")

    # Required dependencies
    for module, label in (
        ("httpx", "httpx"),
        ("jsonschema", "jsonschema"),
        ("yaml", "pyyaml"),
        ("surfacelock", "surfacelock"),
    ):
        if importlib.util.find_spec(module) is not None:
            add(f"dep:{label}", "ok", "installed")
        elif label == "surfacelock":
            add(
                f"dep:{label}",
                "warn",
                "not installed — falling back to the bundled registry snapshot",
            )
        else:
            add(f"dep:{label}", "error", "missing — pip install modelbump")

    # Optional extras
    for module, label, extra in (
        ("botocore", "botocore", "bedrock"),
        ("tiktoken", "tiktoken", "tokens"),
    ):
        if importlib.util.find_spec(module) is not None:
            add(f"extra:{label}", "ok", "installed")
        else:
            add(f"extra:{label}", "warn", f"optional — pip install 'modelbump[{extra}]'")

    # Registry
    reg = registry()
    add("registry", "ok", f"{len(reg.all())} models from {', '.join(reg.sources)}")

    # Config
    from modelbump.config import discover

    if config_path:
        from pathlib import Path

        if Path(config_path).exists():
            add("config", "ok", config_path)
        else:
            add("config", "error", f"{config_path} does not exist")
    else:
        found = discover()
        if found:
            add("config", "ok", f"found {found}")
        else:
            add("config", "warn", "no modelbump.toml found — defaults will be used")

    # Credentials
    env_rows = env_status()
    configured = [
        row["provider"] for row in env_rows if row["provider"] not in ("mock",) and row["ready"]
    ]
    if configured:
        add("credentials", "ok", f"configured: {', '.join(configured)}")
    else:
        add(
            "credentials",
            "warn",
            "no provider keys set — only the mock: provider will work",
        )

    # Cache
    from modelbump.cache import default_cache_dir

    cache_dir = default_cache_dir()
    if cache_dir.exists():
        stats = __import__("modelbump.cache", fromlist=["Cache"]).Cache(cache_dir).stats()
        add("cache", "ok", f"{stats.entries} entries in {cache_dir}")
    else:
        add("cache", "warn", f"not yet created ({cache_dir})")

    # Write access
    try:
        probe = cache_dir
        probe.mkdir(parents=True, exist_ok=True)
        test_file = probe / ".modelbump-write-test"
        test_file.write_text("ok")
        test_file.unlink()
        add("write access", "ok", str(probe))
    except OSError as exc:
        add("write access", "error", str(exc))

    if os.environ.get("NO_COLOR"):
        add("color", "warn", "NO_COLOR is set — output will be uncolored")

    statuses = {c["status"] for c in checks}
    overall = "error" if "error" in statuses else ("warn" if "warn" in statuses else "ok")
    return {
        "modelbump": __version__,
        "overall": overall,
        "checks": checks,
    }


def render_text(result: dict[str, Any]) -> str:
    from modelbump.output import bold, dim, err, ok, table, warn

    lines = [bold(f"modelbump doctor  v{result['modelbump']}"), ""]
    rows = []
    for check in result["checks"]:
        status = check["status"]
        label = {
            "ok": ok("ok"),
            "warn": warn("warn"),
            "error": err("error"),
        }[status]
        rows.append([label, check["name"], check["detail"]])
    lines.append(table(["status", "check", "detail"], rows))
    lines.append("")
    overall = result["overall"]
    summary = {
        "ok": ok("all checks passed"),
        "warn": warn("passed with warnings"),
        "error": err("problems found — see above"),
    }[overall]
    lines.append(f"overall: {summary}")
    lines.append(dim("tip: run with --json for machine-readable output"))
    return "\n".join(lines)


def render_json(result: dict[str, Any]) -> str:
    import json

    return json.dumps(result, indent=2)
