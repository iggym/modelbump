"""Error types and CLI exit codes (B3)."""

from __future__ import annotations


class ModelbumpError(Exception):
    """Base class for all modelbump errors."""


class SuiteError(ModelbumpError):
    """A suite could not be loaded or is invalid."""


class ProviderError(ModelbumpError):
    """A provider adapter failed to build or execute."""


class AuthError(ProviderError):
    """A provider credential is missing or rejected."""


class BudgetExceeded(ModelbumpError):
    """The projected cost of a run exceeds --max-cost."""

    def __init__(self, projected: float, limit: float) -> None:
        super().__init__(
            f"projected cost ${projected:.4f} exceeds --max-cost ${limit:.4f}"
        )
        self.projected = projected
        self.limit = limit


class RegistryError(ModelbumpError):
    """The model registry could not be read."""


# ---------------------------------------------------------------------------
# Exit codes (B3)
# ---------------------------------------------------------------------------
EXIT_OK = 0
EXIT_VERDICT = 1  # thresholds violated (or --strict warning)
EXIT_USAGE = 2  # bad invocation / unknown model spec / missing key
EXIT_INTERNAL = 3  # unexpected internal error


class JudgeError(ModelbumpError):
    """The judge could not be configured or executed."""
