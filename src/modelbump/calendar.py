"""Retirement calendar from the registry (``modelbump calendar``)."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from modelbump.registry import registry


def _parse(date_str: str | None) -> date | None:
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def calendar_rows(*, today: date | None = None) -> list[dict[str, Any]]:
    today = today or date.today()
    rows: list[dict[str, Any]] = []
    for info in registry().retirements():
        retirement = _parse(info.retirement_date)
        if retirement is None:
            continue
        rows.append(
            {
                "model": info.name,
                "provider": info.provider,
                "retirement_date": info.retirement_date,
                "days_remaining": (retirement - today).days,
                "successor": info.successor,
                "status": (
                    "retired"
                    if retirement < today
                    else "imminent"
                    if (retirement - today).days <= 30
                    else "scheduled"
                ),
            }
        )
    rows.sort(key=lambda r: r["retirement_date"])
    return rows


def render_text(rows: list[dict[str, Any]]) -> str:
    from modelbump.output import C, dim, err, paint, table, warn

    if not rows:
        return dim("no scheduled retirements in the registry")
    table_rows = []
    for row in rows:
        status = row["status"]
        label = {
            "retired": err("retired"),
            "imminent": warn("imminent"),
            "scheduled": paint("scheduled", C.BRIGHT_BLUE),
        }[status]
        days = row["days_remaining"]
        table_rows.append(
            [
                row["model"],
                row["provider"],
                row["retirement_date"],
                f"{days:+d}d" if days else "today",
                row["successor"] or "—",
                label,
            ]
        )
    return table(
        ["model", "provider", "retires", "in", "successor", "status"],
        table_rows,
    )


def render_json(rows: list[dict[str, Any]]) -> str:
    return json.dumps({"retirements": rows}, indent=2)
