"""Pure PPMS transport geometry, segmentation, and linearized fitting."""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import pairwise

import numpy as np
from numpy.typing import ArrayLike, NDArray

from materials_data_studio.core.exceptions import AnalysisError

BOLTZMANN_EV_K = 8.617333262e-5


@dataclass(slots=True)
class TransportSegment:
    segment_id: int
    indices: NDArray[np.int64]
    sweep_type: str
    direction: str
    nominal_value: float
    minimum_value: float
    maximum_value: float
    cycle: int = 0

    @property
    def label(self) -> str:
        unit = "Oe" if self.sweep_type == "temperature" else "K"
        return (
            f"{self.nominal_value:.4g} {unit} — {self.direction.title()} — "
            f"{len(self.indices)} points"
        )


@dataclass(slots=True)
class TransportFit:
    slope: float
    intercept: float
    slope_error: float
    intercept_error: float
    r_squared: float
    point_count: int
    excluded_count: int
    fitted_values: NDArray[np.float64]
    activation_energy_ev: float | None = None
    activation_energy_error_ev: float | None = None
    characteristic_temperature_k: float | None = None
    characteristic_temperature_error_k: float | None = None
    prefactor: float = math.nan
    prefactor_error: float = math.nan


def geometry_factor(
    mode: str,
    *,
    area_cm2: float | None = None,
    width_cm: float | None = None,
    thickness_cm: float | None = None,
    length_cm: float | None = None,
    factor_cm: float | None = None,
    volume_cm3: float | None = None,
) -> float:
    """Return A/L in cm for supported bar-sample geometry modes."""
    if mode == "area_length":
        values = (area_cm2, length_cm)
        factor = (
            float(area_cm2) / float(length_cm)
            if area_cm2 is not None and length_cm is not None
            else math.nan
        )
    elif mode == "width_thickness_length":
        values = (width_cm, thickness_cm, length_cm)
        factor = (
            float(width_cm) * float(thickness_cm) / float(length_cm)
            if width_cm is not None
            and thickness_cm is not None
            and length_cm is not None
            else math.nan
        )
    elif mode == "factor":
        values = (factor_cm,)
        factor = float(factor_cm) if factor_cm is not None else math.nan
    elif mode == "volume_length":
        values = (volume_cm3, length_cm)
        factor = (
            float(volume_cm3) / float(length_cm) ** 2
            if volume_cm3 is not None and length_cm is not None
            else math.nan
        )
    else:
        raise AnalysisError(f"Unknown geometry mode: {mode}")
    if any(value is None or not np.isfinite(value) or value <= 0 for value in values):
        raise AnalysisError("All required sample dimensions must be finite and positive.")
    if not np.isfinite(factor) or factor <= 0:
        raise AnalysisError("The calculated geometry factor A/L is invalid.")
    return factor


def resistance_to_resistivity(resistance_ohm: ArrayLike, factor_cm: float) -> NDArray[np.float64]:
    """Convert resistance to ohm·cm with rho = R A/L."""
    if not np.isfinite(factor_cm) or factor_cm <= 0:
        raise AnalysisError("Geometry factor A/L must be finite and positive.")
    return np.asarray(resistance_ohm, dtype=float) * factor_cm


def source_resistivity_to_ohm_cm(values: ArrayLike, unit: str) -> NDArray[np.float64]:
    """Convert an already geometry-corrected source without applying geometry."""
    factors = {"ohm_cm": 1.0, "ohm_m": 100.0, "mohm_cm": 1e-3}
    if unit not in factors:
        raise AnalysisError(f"Unknown source resistivity unit: {unit}")
    return np.asarray(values, dtype=float) * factors[unit]


def segment_transport(
    temperature_k: ArrayLike,
    field_oe: ArrayLike,
    *,
    mode: str,
    field_tolerance_oe: float = 50.0,
    temperature_tolerance_k: float = 0.05,
    measurement_order: ArrayLike | None = None,
) -> list[TransportSegment]:
    """Split by temperature or field direction using mode-specific thresholds."""
    temperature = np.asarray(temperature_k, dtype=float)
    field = np.asarray(field_oe, dtype=float)
    if temperature.shape != field.shape or temperature.ndim != 1 or len(field) == 0:
        raise AnalysisError("Temperature and field must be aligned non-empty arrays.")
    if mode not in {"rho_t", "rho_h"}:
        raise AnalysisError("Transport segmentation mode must be rho_t or rho_h.")
    if temperature_tolerance_k < 0 or field_tolerance_oe < 0:
        raise AnalysisError("Transport segmentation thresholds cannot be negative.")
    del measurement_order
    values = temperature if mode == "rho_t" else field
    threshold = (
        temperature_tolerance_k if mode == "rho_t" else field_tolerance_oe
    )
    differences = np.diff(values)
    comparison_margin = (
        np.finfo(float).eps
        * np.maximum(1.0, np.abs(values[:-1]) + np.abs(values[1:]))
        * 8
    )
    difference_states = np.where(
        differences > threshold + comparison_margin,
        1,
        np.where(differences < -threshold - comparison_margin, -1, 0),
    )
    point_states = (
        np.asarray([0], dtype=int)
        if len(temperature) == 1
        else np.concatenate(([difference_states[0]], difference_states))
    )
    starts = [0]
    starts.extend(
        index
        for index in range(1, len(point_states))
        if point_states[index] != point_states[index - 1]
    )
    starts.append(len(temperature))
    segments: list[TransportSegment] = []
    names = (
        {-1: "cooling", 0: "constant", 1: "warming"}
        if mode == "rho_t"
        else {-1: "decreasing", 0: "constant", 1: "increasing"}
    )
    for start, stop in pairwise(starts):
        indices = np.arange(start, stop, dtype=np.int64)
        nominal_values = field[indices] if mode == "rho_t" else temperature[indices]
        segments.append(
            TransportSegment(
                len(segments),
                indices,
                "temperature" if mode == "rho_t" else "field",
                names[int(point_states[start])],
                float(np.mean(nominal_values)),
                float(np.min(nominal_values)),
                float(np.max(nominal_values)),
                0,
            )
        )
    return segments


def _linear_fit(
    x: NDArray[np.float64],
    y: NDArray[np.float64],
    mask: NDArray[np.bool_],
    y_error: NDArray[np.float64] | None,
) -> tuple[float, float, float, float, float, NDArray[np.float64]]:
    selected_x, selected_y = x[mask], y[mask]
    if len(selected_x) < 3:
        raise AnalysisError("At least three valid points are required for fitting.")
    design = np.column_stack((np.ones_like(selected_x), selected_x))
    weights = (
        np.ones_like(selected_x)
        if y_error is None
        else 1.0 / np.asarray(y_error[mask], dtype=float) ** 2
    )
    covariance_unscaled = np.linalg.inv(design.T @ (weights[:, None] * design))
    intercept, slope = covariance_unscaled @ (design.T @ (weights * selected_y))
    residual = selected_y - (intercept + slope * selected_x)
    dof = len(selected_x) - 2
    covariance = (
        covariance_unscaled
        if y_error is not None
        else covariance_unscaled * float(np.sum(residual**2) / dof)
    )
    intercept_error, slope_error = np.sqrt(np.maximum(np.diag(covariance), 0))
    total = float(np.sum((selected_y - np.mean(selected_y)) ** 2))
    r_squared = 1.0 - float(np.sum(residual**2)) / total if total > 0 else 1.0
    return (
        float(slope),
        float(intercept),
        float(slope_error),
        float(intercept_error),
        r_squared,
        intercept + slope * x,
    )


def fit_arrhenius(
    temperature_k: ArrayLike,
    resistance_or_resistivity: ArrayLike,
    *,
    minimum_k: float,
    maximum_k: float,
    error: ArrayLike | None = None,
) -> TransportFit:
    """Fit ln(rho) against internal 1/T and report activation energy."""
    temperature = np.asarray(temperature_k, dtype=float)
    values = np.asarray(resistance_or_resistivity, dtype=float)
    sigma = None if error is None else np.asarray(error, dtype=float) / values
    mask = (
        np.isfinite(temperature)
        & np.isfinite(values)
        & (temperature > 0)
        & (values > 0)
        & (temperature >= minimum_k)
        & (temperature <= maximum_k)
    )
    if sigma is not None:
        mask &= np.isfinite(sigma) & (sigma > 0)
    x = np.divide(1.0, temperature, out=np.full_like(temperature, np.nan), where=temperature > 0)
    y = np.log(values, where=values > 0, out=np.full_like(values, np.nan))
    slope, intercept, slope_error, intercept_error, r_squared, fitted = _linear_fit(
        x, y, mask, sigma
    )
    prefactor = math.exp(intercept)
    return TransportFit(
        slope,
        intercept,
        slope_error,
        intercept_error,
        r_squared,
        int(np.count_nonzero(mask)),
        int(len(mask) - np.count_nonzero(mask)),
        fitted,
        activation_energy_ev=slope * BOLTZMANN_EV_K,
        activation_energy_error_ev=slope_error * BOLTZMANN_EV_K,
        prefactor=prefactor,
        prefactor_error=prefactor * intercept_error,
    )


def fit_mott_vrh(
    temperature_k: ArrayLike,
    resistance_or_resistivity: ArrayLike,
    *,
    dimension: int,
    minimum_k: float,
    maximum_k: float,
    error: ArrayLike | None = None,
) -> TransportFit:
    """Fit Mott VRH ln(rho) = ln(rho0) + T0^p T^-p."""
    if dimension not in {1, 2, 3}:
        raise AnalysisError("Mott dimensionality must be 1, 2, or 3.")
    p = 1.0 / (dimension + 1)
    temperature = np.asarray(temperature_k, dtype=float)
    values = np.asarray(resistance_or_resistivity, dtype=float)
    sigma = None if error is None else np.asarray(error, dtype=float) / values
    mask = (
        np.isfinite(temperature)
        & np.isfinite(values)
        & (temperature > 0)
        & (values > 0)
        & (temperature >= minimum_k)
        & (temperature <= maximum_k)
    )
    if sigma is not None:
        mask &= np.isfinite(sigma) & (sigma > 0)
    x = np.power(temperature, -p, where=temperature > 0, out=np.full_like(temperature, np.nan))
    y = np.log(values, where=values > 0, out=np.full_like(values, np.nan))
    slope, intercept, slope_error, intercept_error, r_squared, fitted = _linear_fit(
        x, y, mask, sigma
    )
    if slope <= 0:
        raise AnalysisError("Mott VRH requires a positive transformed slope.")
    t0 = slope ** (1.0 / p)
    t0_error = abs(t0 * slope_error / (p * slope))
    prefactor = math.exp(intercept)
    return TransportFit(
        slope,
        intercept,
        slope_error,
        intercept_error,
        r_squared,
        int(np.count_nonzero(mask)),
        int(len(mask) - np.count_nonzero(mask)),
        fitted,
        characteristic_temperature_k=t0,
        characteristic_temperature_error_k=t0_error,
        prefactor=prefactor,
        prefactor_error=prefactor * intercept_error,
    )
