"""CIF-constrained whole-pattern XRD peak fitting."""

from typing import TYPE_CHECKING, Any

from materials_data_studio.modules.xrd.peak_fitting.models import (
    FitConfiguration,
    FitParameter,
    FitProgress,
    FitStatistics,
    PeakFitResult,
    PeakReflection,
    ReflectionGroup,
)

if TYPE_CHECKING:
    from materials_data_studio.modules.xrd.peak_fitting.optimizer import (
        FitCancelledError,
        fit_pattern,
        preview_pattern,
    )


def __getattr__(name: str) -> Any:
    if name in {"FitCancelledError", "fit_pattern", "preview_pattern"}:
        from materials_data_studio.modules.xrd.peak_fitting import optimizer
        value = getattr(optimizer, name)
        globals()[name] = value
        return value
    raise AttributeError(name)

__all__ = [
    "FitCancelledError",
    "FitConfiguration",
    "FitParameter",
    "FitProgress",
    "FitStatistics",
    "PeakFitResult",
    "PeakReflection",
    "ReflectionGroup",
    "fit_pattern",
    "preview_pattern",
]
