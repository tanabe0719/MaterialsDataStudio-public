"""Typed state for CIF-constrained whole-pattern fitting."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from materials_data_studio.modules.xrd.cif_backend import UnitCell


@dataclass(slots=True)
class FitParameter:
    """One displayed fitting parameter and its refinement constraint."""

    name: str
    value: float
    lower: float
    upper: float
    unit: str = ""
    refine: bool = False
    status: str = "Fixed"
    standard_error: float | None = None


@dataclass(slots=True)
class PeakReflection:
    """One CIF-derived reflection retained without modifying its source data."""

    phase_id: str
    phase_name: str
    hkl: tuple[int, int, int]
    member_hkls: tuple[tuple[int, int, int], ...]
    calculated_two_theta_deg: float
    calculated_d_a: float
    calculated_intensity: float
    enabled: bool = True


@dataclass(slots=True)
class ReflectionGroup:
    """A resolution-limited group of one or more reflections."""

    group_id: int
    phase_id: str
    phase_name: str
    reflections: tuple[PeakReflection, ...]
    calculated_two_theta_deg: float
    calculated_d_a: float
    calculated_intensity: float
    fitted_two_theta_deg: float = np.nan
    fitted_d_a: float = np.nan
    fitted_area: float = 0.0
    fwhm_deg: float = np.nan
    enabled: bool = True
    status: str = "Ready"


@dataclass(slots=True)
class FitConfiguration:
    """All immutable inputs required by the fitting worker."""

    dataset_id: str
    dataset_name: str
    phase_id: str
    phase_name: str
    source_cif: str
    x: NDArray[np.float64]
    y: NDArray[np.float64]
    reflections: tuple[PeakReflection, ...]
    initial_cell: UnitCell
    crystal_system: str
    parameters: dict[str, FitParameter]
    fit_range: tuple[float, float]
    excluded_ranges: tuple[tuple[float, float], ...] = ()
    intensity_mode: str = "independent"
    grouping_factor: float = 0.5
    background_mode: str = "current"
    background_order: int = 2
    current_background: NDArray[np.float64] | None = None
    weighting: str = "uniform"
    uncertainty: NDArray[np.float64] | None = None
    loss: str = "linear"
    intensity_floor: float = 1.0
    le_bail_cycles: int = 20
    le_bail_tolerance: float = 1.0e-5
    radiation: str = "Custom"
    alpha2_ratio: float = 0.5


@dataclass(slots=True)
class FitStatistics:
    """Residual indicators and optimizer accounting."""

    success: bool
    message: str
    data_points: int
    nonlinear_parameters: int
    linear_parameters: int
    active_groups: int
    excluded_points: int
    sum_squared_residuals: float
    rms_residual: float
    r_p: float
    r_wp: float
    reduced_chi_square: float | None
    evaluations: int
    cycles: int = 0


@dataclass(slots=True)
class FitProgress:
    """One completed Le Bail cycle reported to the GUI."""

    cycle: int
    total_cycles: int
    r_wp: float
    maximum_parameter_change: float
    parameter_changes: dict[str, float]
    maximum_area_change: float


@dataclass(slots=True)
class PeakFitResult:
    """Complete fitted pattern, parameters, reflections, and diagnostics."""

    parameters: dict[str, FitParameter]
    groups: list[ReflectionGroup]
    x: NDArray[np.float64]
    observed: NDArray[np.float64]
    calculated: NDArray[np.float64]
    background: NDArray[np.float64]
    residual: NDArray[np.float64]
    weighted_residual: NDArray[np.float64]
    fit_mask: NDArray[np.bool_]
    excluded_mask: NDArray[np.bool_]
    statistics: FitStatistics
    warnings: list[str] = field(default_factory=list)
    correlation_names: list[str] = field(default_factory=list)
    correlation_matrix: NDArray[np.float64] | None = None
