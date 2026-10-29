"""Pure Heat Capacity conversions, segmentation, fitting, and entropy."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from itertools import pairwise
from threading import Event

import numpy as np
from numpy.typing import ArrayLike, NDArray

from materials_data_studio.core.exceptions import AnalysisError

GAS_CONSTANT = 8.31446261815324


@dataclass(slots=True)
class HeatCapacitySegment:
    """One contiguous approximately constant-field measurement block."""

    segment_id: int
    indices: NDArray[np.int64]
    mean_field_oe: float
    min_field_oe: float
    max_field_oe: float
    direction: str

    @property
    def label(self) -> str:
        return (
            f"{self.mean_field_oe:.4g} Oe — {self.direction.title()} — {len(self.indices)} points"
        )


@dataclass(slots=True)
class LowTemperatureFit:
    gamma_j_mol_k2: float
    beta_j_mol_k4: float
    gamma_error: float
    beta_error: float
    r_squared: float
    point_count: int
    temperature_range_k: tuple[float, float]
    debye_temperature_k: float | None
    debye_temperature_error_k: float | None
    fitted_values: NDArray[np.float64]


@dataclass(slots=True)
class DebyeEinsteinFit:
    parameters: dict[str, float]
    standard_errors: dict[str, float]
    fitted_values: NDArray[np.float64]
    residuals: NDArray[np.float64]
    included_mask: NDArray[np.bool_]
    r_squared: float
    reduced_chi_squared: float | None
    converged: bool
    covariance_warning: str


def average_adjacent_heat_capacity(
    temperature_k: ArrayLike,
    aligned_values: Mapping[str, ArrayLike],
    *,
    maximum_temperature_difference_k: float = 0.1,
    maximum_field_difference_oe: float = 100.0,
) -> dict[str, NDArray[np.float64]]:
    """Average adjacent measurement pairs whose temperatures differ by at most 0.1 K."""
    temperature = np.asarray(temperature_k, dtype=float)
    arrays = {name: np.asarray(values, dtype=float) for name, values in aligned_values.items()}
    if temperature.ndim != 1 or any(
        values.shape != temperature.shape for values in arrays.values()
    ):
        raise AnalysisError("All Heat Capacity arrays must be aligned and one-dimensional.")
    if maximum_temperature_difference_k < 0 or maximum_field_difference_oe < 0:
        raise AnalysisError("The averaging temperature threshold cannot be negative.")
    if not len(temperature):
        return {"temperature_k": temperature.copy(), **{name: values.copy() for name, values in arrays.items()},
                "source_row_start": np.empty(0), "source_row_stop": np.empty(0)}
    eligible = (np.isfinite(temperature[:-1]) & np.isfinite(temperature[1:])
                & (np.abs(np.diff(temperature)) <= maximum_temperature_difference_k))
    if "field_oe" in arrays:
        field = arrays["field_oe"]
        eligible &= (np.isfinite(field[:-1]) & np.isfinite(field[1:])
                     & (np.abs(np.diff(field)) <= maximum_field_difference_oe))
    # Greedy, non-overlapping pairs retain the exact original grouping rule.
    starts = []
    index = 0
    while index < len(temperature):
        starts.append(index)
        index += 2 if index < len(eligible) and eligible[index] else 1
    first = np.asarray(starts, dtype=np.int64)
    lengths = np.diff(np.append(first, len(temperature)))
    last = first + lengths - 1

    def averaged(values: NDArray[np.float64]) -> NDArray[np.float64]:
        finite = np.add.reduceat(np.isfinite(values).astype(np.int64), first) > 0
        counts = np.add.reduceat((~np.isnan(values)).astype(np.int64), first)
        with np.errstate(invalid="ignore", divide="ignore"):
            sums = np.add.reduceat(np.where(np.isnan(values), 0.0, values), first)
            return np.where(finite, sums / np.maximum(counts, 1), np.nan)

    result = {"temperature_k": averaged(temperature)}
    for name, values in arrays.items():
        if "error" in name.lower():
            finite = np.add.reduceat(np.isfinite(values).astype(np.int64), first) == lengths
            with np.errstate(invalid="ignore", over="ignore"):
                squared_sum = np.add.reduceat(values ** 2, first)
                result[name] = np.where(finite, np.sqrt(squared_sum) / lengths, np.nan)
        else:
            result[name] = averaged(values)
    source_rows = arrays.get("source_row_index", np.arange(len(temperature), dtype=float))
    result["source_row_start"] = source_rows[first].copy()
    result["source_row_stop"] = source_rows[last].copy()
    return result


def convert_to_molar_heat_capacity(
    heat_capacity_uj_k: ArrayLike,
    error_uj_k: ArrayLike,
    *,
    sample_mass_mg: float,
    molar_mass_g_mol: float,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Convert raw sample µJ/K values and errors into J mol⁻¹ K⁻¹."""
    if (
        not np.isfinite(sample_mass_mg)
        or not np.isfinite(molar_mass_g_mol)
        or sample_mass_mg <= 0
        or molar_mass_g_mol <= 0
    ):
        raise AnalysisError("Sample mass and molar mass must be finite and positive.")
    moles = sample_mass_mg * 1e-3 / molar_mass_g_mol
    if not np.isfinite(moles) or moles <= 0:
        raise AnalysisError("The calculated amount of sample is invalid.")
    factor = 1e-6 / moles
    return (
        np.asarray(heat_capacity_uj_k, dtype=float) * factor,
        np.asarray(error_uj_k, dtype=float) * factor,
    )


def segment_heat_capacity(
    temperature_k: ArrayLike,
    field_oe: ArrayLike | None = None,
    *,
    field_threshold_oe: float = 100.0,
) -> list[HeatCapacitySegment]:
    """Split after averaging whenever adjacent field values differ by over 100 Oe."""
    temperature = np.asarray(temperature_k, dtype=float)
    field = np.zeros_like(temperature) if field_oe is None else np.asarray(field_oe, dtype=float)
    if temperature.ndim != 1 or field.shape != temperature.shape or len(field) == 0:
        raise AnalysisError("Temperature and field must be aligned non-empty arrays.")
    if field_threshold_oe < 0:
        raise AnalysisError("The field-change threshold cannot be negative.")
    boundaries = [0]
    boundaries.extend(
        index + 1
        for index, difference in enumerate(np.diff(field))
        if not np.isfinite(difference) or abs(difference) > field_threshold_oe
    )
    boundaries.append(len(temperature))
    segments: list[HeatCapacitySegment] = []
    for start, stop in pairwise(boundaries):
        indices = np.arange(start, stop, dtype=np.int64)
        segment_field = field[indices]
        segments.append(
            HeatCapacitySegment(
                len(segments),
                indices,
                float(np.mean(segment_field)),
                float(np.min(segment_field)),
                float(np.max(segment_field)),
                "field segment",
            )
        )
    return segments


def fit_low_temperature(
    temperature_k: ArrayLike,
    cp_molar_j_mol_k: ArrayLike,
    *,
    minimum_k: float,
    maximum_k: float,
    atoms_per_formula_unit: float | None = None,
    error_j_mol_k: ArrayLike | None = None,
    fix_gamma_zero: bool = False,
) -> LowTemperatureFit:
    """Fit Cp/T = gamma + beta T² in a temperature-defined interval."""
    temperature = np.asarray(temperature_k, dtype=float)
    cp = np.asarray(cp_molar_j_mol_k, dtype=float)
    error = None if error_j_mol_k is None else np.asarray(error_j_mol_k, dtype=float)
    mask = (
        np.isfinite(temperature)
        & np.isfinite(cp)
        & (temperature > 0)
        & (temperature >= minimum_k)
        & (temperature <= maximum_k)
    )
    if error is not None:
        mask &= np.isfinite(error) & (error > 0)
    if np.count_nonzero(mask) < 3:
        raise AnalysisError("At least three valid points are required for the low-T fit.")
    x = temperature[mask] ** 2
    y = cp[mask] / temperature[mask]
    design = x[:, None] if fix_gamma_zero else np.column_stack((np.ones_like(x), x))
    if error is None:
        weights = np.ones_like(x)
    else:
        y_error = error[mask] / temperature[mask]
        weights = 1.0 / y_error**2
    weighted_design = np.sqrt(weights)[:, None] * design
    coefficients, _residual, rank, singular = np.linalg.lstsq(weighted_design, np.sqrt(weights) * y, rcond=None)
    if rank < design.shape[1] or singular[0] / singular[-1] > 1e12:
        raise AnalysisError("The selected temperature range is singular or poorly conditioned; widen the fit range.")
    inverse = np.linalg.pinv(weighted_design)
    covariance_unscaled = inverse @ inverse.T
    fitted_subset = design @ coefficients
    residual = y - fitted_subset
    dof = len(y) - design.shape[1]
    variance = float(np.sum(weights * residual**2) / dof)
    covariance = covariance_unscaled if error is not None else covariance_unscaled * variance
    if fix_gamma_zero:
        gamma, beta = 0.0, float(coefficients[0])
        gamma_error, beta_error = 0.0, float(np.sqrt(max(covariance[0, 0], 0)))
    else:
        gamma, beta = map(float, coefficients)
        gamma_error, beta_error = np.sqrt(np.maximum(np.diag(covariance), 0))
    total = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = 1.0 - float(np.sum(residual**2)) / total if total > 0 else 1.0
    theta = theta_error = None
    if beta > 0 and atoms_per_formula_unit is not None and atoms_per_formula_unit > 0:
        theta = float(
            (12 * np.pi**4 * atoms_per_formula_unit * GAS_CONSTANT / (5 * beta)) ** (1 / 3)
        )
        theta_error = float(theta * beta_error / (3 * beta))
    fitted_all = gamma + beta * temperature**2
    return LowTemperatureFit(
        gamma,
        beta,
        float(gamma_error),
        float(beta_error),
        r_squared,
        len(x),
        (minimum_k, maximum_k),
        theta,
        theta_error,
        fitted_all,
    )


@lru_cache(maxsize=8192)
def _debye_integral(upper_rounded: float) -> float:
    from scipy.integrate import quad

    if upper_rounded >= 150:
        return 4 * math.pi**4 / 15

    def integrand(x: float) -> float:
        if x == 0:
            return 0.0
        exp_negative = math.exp(-x)
        return x**4 * exp_negative / (1.0 - exp_negative) ** 2

    return float(quad(integrand, 0.0, upper_rounded, epsabs=1e-9, epsrel=1e-8)[0])


def debye_heat_capacity(temperature_k: ArrayLike, theta_k: float) -> NDArray[np.float64]:
    """Evaluate one molar Debye oscillator with stable high-theta behavior."""
    temperature = np.asarray(temperature_k, dtype=float)
    if theta_k <= 0 or np.any(temperature <= 0):
        raise AnalysisError("Debye temperature and measurement temperatures must be positive.")
    ratios = theta_k / temperature
    integrals = np.array([_debye_integral(round(float(ratio), 8)) for ratio in ratios], dtype=float)
    return 9 * GAS_CONSTANT * integrals / ratios**3


def einstein_heat_capacity(temperature_k: ArrayLike, theta_k: float) -> NDArray[np.float64]:
    """Evaluate one molar Einstein oscillator without exponential overflow."""
    temperature = np.asarray(temperature_k, dtype=float)
    if theta_k <= 0 or np.any(temperature <= 0):
        raise AnalysisError("Einstein temperature and measurement temperatures must be positive.")
    ratio = theta_k / temperature
    exp_negative = np.exp(-np.minimum(ratio, 745.0))
    return 3 * GAS_CONSTANT * ratio**2 * exp_negative / (1 - exp_negative) ** 2


def debye_einstein_model(
    temperature_k: ArrayLike,
    *,
    theta_d: float,
    weight_d: float,
    gamma: float = 0.0,
    theta_e: tuple[float, ...] = (),
    weight_e: tuple[float, ...] = (),
) -> NDArray[np.float64]:
    """Evaluate gamma T plus one Debye and up to two Einstein terms."""
    if len(theta_e) != len(weight_e) or len(theta_e) > 2:
        raise AnalysisError("Debye–Einstein terms require zero to two matched weights.")
    temperature = np.asarray(temperature_k, dtype=float)
    result = gamma * temperature + weight_d * debye_heat_capacity(temperature, theta_d)
    for theta, weight in zip(theta_e, weight_e, strict=True):
        result += weight * einstein_heat_capacity(temperature, theta)
    return result


def fit_debye_einstein(
    temperature_k: ArrayLike,
    cp_molar_j_mol_k: ArrayLike,
    *,
    minimum_k: float,
    maximum_k: float,
    n_einstein: int = 2,
    initial: dict[str, float] | None = None,
    fixed: dict[str, float] | None = None,
    errors: ArrayLike | None = None,
    excluded_ranges: tuple[tuple[float, float], ...] = (),
    constrain_atoms: float | None = None,
    cancel_event: Event | None = None,
) -> DebyeEinsteinFit:
    """Fit a configurable non-negative Debye–Einstein model."""
    from scipy.optimize import least_squares

    if n_einstein not in {0, 1, 2}:
        raise AnalysisError("The number of Einstein terms must be 0, 1, or 2.")
    temperature = np.asarray(temperature_k, dtype=float)
    cp = np.asarray(cp_molar_j_mol_k, dtype=float)
    sigma = None if errors is None else np.asarray(errors, dtype=float)
    mask = (
        np.isfinite(temperature)
        & np.isfinite(cp)
        & (temperature > 0)
        & (temperature >= minimum_k)
        & (temperature <= maximum_k)
    )
    for lower, upper in excluded_ranges:
        mask &= ~((temperature >= lower) & (temperature <= upper))
    if sigma is not None:
        mask &= np.isfinite(sigma) & (sigma > 0)
    if np.count_nonzero(mask) < 5:
        raise AnalysisError("At least five valid points are required for Debye–Einstein fitting.")
    defaults = {
        "gamma": 0.0,
        "theta_d": 250.0,
        "weight_d": 1.0,
        "theta_e1": 450.0,
        "weight_e1": 1.0,
        "theta_e2": 800.0,
        "weight_e2": 1.0,
    }
    defaults.update(initial or {})
    fixed_values = dict(fixed or {})
    names = ["gamma", "theta_d", "weight_d"]
    for index in range(1, n_einstein + 1):
        names.extend((f"theta_e{index}", f"weight_e{index}"))
    free = [name for name in names if name not in fixed_values]
    lower = [0.0 for _name in free]
    upper = [np.inf for _name in free]
    x0 = np.array([max(defaults[name], 1e-9) for name in free])

    dependent_weight: str | None = None
    if constrain_atoms is not None:
        if constrain_atoms <= 0:
            raise AnalysisError("The atom-count oscillator constraint must be positive.")
        free_weights = [name for name in free if name.startswith("weight_")]
        if free_weights:
            dependent_weight = free_weights[-1]
            position = free.index(dependent_weight)
            free.pop(position)
            x0 = np.delete(x0, position)
            lower.pop(position)
            upper.pop(position)

    def unpack(values: NDArray[np.float64]) -> dict[str, float]:
        parameters = dict(fixed_values)
        parameters.update({name: float(value) for name, value in zip(free, values, strict=True)})
        if dependent_weight is not None and constrain_atoms is not None:
            other = sum(
                parameters.get(name, defaults[name])
                for name in names
                if name.startswith("weight_") and name != dependent_weight
            )
            parameters[dependent_weight] = constrain_atoms - other
        for name in names:
            parameters.setdefault(name, defaults[name])
        return parameters

    fit_t = temperature[mask]
    fit_cp = cp[mask]
    fit_sigma = sigma[mask] if sigma is not None else None

    def evaluate(parameters: dict[str, float], values: NDArray[np.float64]) -> NDArray[np.float64]:
        theta_e = tuple(parameters[f"theta_e{i}"] for i in range(1, n_einstein + 1))
        weight_e = tuple(parameters[f"weight_e{i}"] for i in range(1, n_einstein + 1))
        return debye_einstein_model(
            values,
            theta_d=parameters["theta_d"],
            weight_d=parameters["weight_d"],
            gamma=parameters["gamma"],
            theta_e=theta_e,
            weight_e=weight_e,
        )

    def objective(values: NDArray[np.float64]) -> NDArray[np.float64]:
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Debye–Einstein fit stopped")
        parameters = unpack(values)
        if any(parameters[name] < 0 for name in names):
            return np.full(len(fit_t), 1e12)
        theta_values = [parameters[f"theta_e{i}"] for i in range(1, n_einstein + 1)]
        if theta_values != sorted(theta_values):
            return np.full(len(fit_t), 1e12)
        residual = evaluate(parameters, fit_t) - fit_cp
        return residual if fit_sigma is None else residual / fit_sigma

    # The Debye integral cache rounds its dimensionless upper bound. SciPy's
    # machine-epsilon finite-difference step can therefore sample the same
    # rounded point on one CPU and a neighboring point on another, producing
    # an unstable numerical Jacobian. A deliberate relative step plus
    # Jacobian-based parameter scaling keeps the nonlinear fit reproducible
    # across Windows development and CI hosts.
    result = least_squares(
        objective,
        x0,
        bounds=(lower, upper),
        x_scale="jac",
        diff_step=1e-5,
        ftol=1e-10,
        xtol=1e-10,
        gtol=1e-10,
        max_nfev=8000,
    )
    parameters = unpack(result.x)
    fitted = evaluate(parameters, temperature)
    residuals = cp - fitted
    fit_residual = residuals[mask]
    total = float(np.sum((fit_cp - np.mean(fit_cp)) ** 2))
    r_squared = 1.0 - float(np.sum(fit_residual**2)) / total if total > 0 else 1.0
    dof = len(fit_cp) - len(result.x)
    reduced = (
        float(np.sum((fit_residual / fit_sigma) ** 2) / dof)
        if fit_sigma is not None and dof > 0
        else None
    )
    errors_out = {name: 0.0 for name in fixed_values}
    covariance_warning = ""
    try:
        covariance = np.linalg.inv(result.jac.T @ result.jac)
        if fit_sigma is None and dof > 0:
            covariance *= float(np.sum(result.fun**2) / dof)
        standard = np.sqrt(np.maximum(np.diag(covariance), 0))
        errors_out.update({name: float(value) for name, value in zip(free, standard, strict=True)})
        if dependent_weight is not None:
            errors_out[dependent_weight] = float(
                np.sqrt(
                    sum(
                        errors_out.get(name, 0.0) ** 2
                        for name in names
                        if name.startswith("weight_") and name != dependent_weight
                    )
                )
            )
    except np.linalg.LinAlgError:
        covariance_warning = "Parameter covariance is singular or poorly determined."
        errors_out.update({name: math.nan for name in free})
    return DebyeEinsteinFit(
        parameters,
        errors_out,
        fitted,
        residuals,
        mask,
        r_squared,
        reduced,
        bool(result.success),
        covariance_warning,
    )


def integrate_residual_entropy(
    temperature_k: ArrayLike,
    residual_cp_j_mol_k: ArrayLike,
    *,
    minimum_k: float,
    maximum_k: float,
) -> float:
    """Integrate ΔCp/T over measured points, averaging explicit duplicates."""
    temperature = np.asarray(temperature_k, dtype=float)
    residual = np.asarray(residual_cp_j_mol_k, dtype=float)
    mask = (
        np.isfinite(temperature)
        & np.isfinite(residual)
        & (temperature > 0)
        & (temperature >= minimum_k)
        & (temperature <= maximum_k)
    )
    if np.count_nonzero(mask) < 2:
        raise AnalysisError("At least two measured points are required for entropy integration.")
    selected_t = temperature[mask]
    selected_r = residual[mask]
    unique_t = np.unique(selected_t)
    if len(unique_t) < 2:
        raise AnalysisError("Entropy integration requires at least two distinct temperatures.")
    averaged = np.array([float(np.mean(selected_r[selected_t == value])) for value in unique_t])
    return float(np.trapezoid(averaged / unique_t, unique_t))
