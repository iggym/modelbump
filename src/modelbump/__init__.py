"""modelbump — behavioral diff for model upgrades and deprecations."""

from __future__ import annotations

__version__ = "0.1.0"

REPORT_SCHEMA_VERSION = "1.0"
TOOL_NAME = "modelbump"

from modelbump.api import DiffResult, run_diff  # noqa: E402
from modelbump.case import Case  # noqa: E402

__all__ = [
    "__version__",
    "REPORT_SCHEMA_VERSION",
    "TOOL_NAME",
    "Case",
    "DiffResult",
    "run_diff",
]
