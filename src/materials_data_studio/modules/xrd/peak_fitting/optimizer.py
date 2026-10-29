"""Hybrid bounded nonlinear and constrained-linear whole-pattern optimizer."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from threading import Event

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import least_squares

from materials_data_studio.core.exceptions import AnalysisError
from materials_data_studio.modules.xrd.cif_backend import UnitCell
from materials_data_studio.modules.xrd.peak_fitting.background import (
    asymmetric_reweighted_linear_fit,
    background_basis,
)
from materials_data_studio.modules.xrd.peak_fitting.models import (
    FitConfiguration,
    FitParameter,
    FitProgress,
    FitStatistics,
    PeakFitResult,
    PeakReflection,
    ReflectionGroup,
)
from materials_data_studio.modules.xrd.peak_fitting.profile import (
    pseudo_voigt_profile,
)
from materials_data_studio.modules.xrd.peak_fitting.reflections import (
    d_spacing,
    group_reflections,
    independent_lattice_parameters,
    shifted_two_theta,
)

_NONLINEAR_NAMES = {
    "a",
    "b",
    "c",
    "alpha",
    "beta",
    "gamma",
    "wavelength",
    "zero_shift",
    "displacement",
    "fwhm",
    "eta",
}


def _copy_parameters(
    parameters: dict[str, FitParameter],
) -> dict[str, FitParameter]:
    return {name: dataclasses.replace(value) for name, value in parameters.items()}


def _validate_parameter(parameter: FitParameter) -> None:
    values = (parameter.value, parameter.lower, parameter.upper)
    if any(not np.isfinite(value) for value in values):
        raise AnalysisError(f"{parameter.name} contains a non-finite value or bound.")
    if parameter.lower >= parameter.upper:
        raise AnalysisError(f"{parameter.name} requires lower bound < upper bound.")
    if not parameter.lower <= parameter.value <= parameter.upper:
        raise AnalysisError(f"{parameter.name} lies outside its displayed bounds.")


def _constrained_cell(
    initial: UnitCell, values: dict[str, float], crystal_system: str
) -> UnitCell:
    cell_values = {
        name: values.get(name, float(getattr(initial, name)))
        for name in ("a", "b", "c", "alpha", "beta", "gamma")
    }
    system = crystal_system.lower()
    if system == "cubic":
        cell_values.update(
            b=cell_values["a"],
            c=cell_values["a"],
            alpha=90.0,
            beta=90.0,
            gamma=90.0,
        )
    elif system == "tetragonal":
        cell_values.update(
            b=cell_values["a"], alpha=90.0, beta=90.0, gamma=90.0
        )
    elif system == "orthorhombic":
        cell_values.update(alpha=90.0, beta=90.0, gamma=90.0)
    elif system == "hexagonal":
        cell_values.update(
            b=cell_values["a"], alpha=90.0, beta=90.0, gamma=120.0
        )
    elif system == "trigonal":
        if abs(initial.gamma - 120.0) < 1.0:
            cell_values.update(
                b=cell_values["a"], alpha=90.0, beta=90.0, gamma=120.0
            )
        else:
            cell_values.update(
                b=cell_values["a"],
                c=cell_values["a"],
                beta=cell_values["alpha"],
                gamma=cell_values["alpha"],
            )
    elif system == "monoclinic":
        cell_values.update(alpha=90.0, gamma=90.0)
    return UnitCell(**cell_values)


def correlation_safeguards(
    configuration: FitConfiguration, active_groups: int
) -> list[str]:
    """Return warnings for known position-parameter correlations."""
    parameters = configuration.parameters
    warnings = []
    lattice_names = independent_lattice_parameters(configuration.crystal_system)
    refined_lattice = [
        name for name in lattice_names if parameters.get(name, FitParameter(name, 0, -1, 1)).refine
    ]
    if parameters["wavelength"].refine and refined_lattice:
        warnings.append(
            "Wavelength and lattice parameters are strongly correlated when refined together."
        )
    angular_span = configuration.fit_range[1] - configuration.fit_range[0]
    if (
        parameters["zero_shift"].refine
        and parameters["displacement"].refine
        and angular_span < 30
    ):
        warnings.append(
            "Zero shift and displacement shift may be strongly correlated over this narrow range."
        )
    if len(refined_lattice) >= max(1, active_groups):
        warnings.append(
            "The number of refined lattice parameters is high for the available peak groups."
        )
    return warnings


def _fit_masks(
    configuration: FitConfiguration,
) -> tuple[NDArray[np.bool_], NDArray[np.bool_]]:
    x, y = configuration.x, configuration.y
    finite = np.isfinite(x) & np.isfinite(y)
    inside = (
        finite
        & (x >= configuration.fit_range[0])
        & (x <= configuration.fit_range[1])
    )
    excluded = np.zeros(len(x), dtype=bool)
    for lower, upper in configuration.excluded_ranges:
        if lower >= upper:
            raise AnalysisError("Every excluded range requires minimum < maximum.")
        excluded |= (x >= lower) & (x <= upper)
    return inside & ~excluded, inside & excluded


def _weights(
    observed: NDArray[np.float64],
    configuration: FitConfiguration,
    mask: NDArray[np.bool_],
) -> NDArray[np.float64]:
    if configuration.weighting == "uniform":
        return np.ones(np.count_nonzero(mask))
    if configuration.weighting == "poisson":
        return 1.0 / np.maximum(
            np.abs(observed[mask]), configuration.intensity_floor
        )
    if configuration.weighting == "custom":
        if configuration.uncertainty is None:
            raise AnalysisError("Custom weighting requires an uncertainty column.")
        uncertainty = np.asarray(configuration.uncertainty, dtype=float)[mask]
        if np.any(~np.isfinite(uncertainty)) or np.any(uncertainty <= 0):
            raise AnalysisError("Custom uncertainties must be positive and finite.")
        return 1.0 / uncertainty**2
    raise AnalysisError(f"Unknown weighting mode: {configuration.weighting}")


def _group_geometry(
    groups: list[ReflectionGroup],
    cell: UnitCell,
    wavelength: float,
    zero_shift: float,
    displacement: float,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    centers = []
    group_d = []
    for group in groups:
        weights = np.asarray(
            [max(item.calculated_intensity, 0.0) for item in group.reflections],
            dtype=float,
        )
        if not np.any(weights > 0):
            weights = np.ones(len(group.reflections))
        spacings = np.asarray(
            [d_spacing(item.hkl, cell) for item in group.reflections], dtype=float
        )
        positions = np.asarray(
            [
                shifted_two_theta(
                    spacing,
                    wavelength,
                    zero_shift_deg=zero_shift,
                    displacement_coefficient_deg=displacement,
                )
                for spacing in spacings
            ],
            dtype=float,
        )
        centers.append(float(np.average(positions, weights=weights)))
        group_d.append(float(np.average(spacings, weights=weights)))
    return np.asarray(centers), np.asarray(group_d)


def _reflections_at_geometry(
    configuration: FitConfiguration,
    parameters: dict[str, FitParameter],
) -> tuple[PeakReflection, ...]:
    """Return CIF reflections positioned with the current preview parameters."""
    values = {name: parameter.value for name, parameter in parameters.items()}
    cell = _constrained_cell(
        configuration.initial_cell, values, configuration.crystal_system
    )
    updated = []
    for reflection in configuration.reflections:
        spacing = d_spacing(reflection.hkl, cell)
        position = shifted_two_theta(
            spacing,
            values["wavelength"],
            zero_shift_deg=values["zero_shift"],
            displacement_coefficient_deg=values["displacement"],
        )
        updated.append(
            dataclasses.replace(
                reflection,
                calculated_two_theta_deg=position,
                calculated_d_a=spacing,
            )
        )
    return tuple(updated)


@dataclasses.dataclass(slots=True)
class _LinearSolution:
    calculated: NDArray[np.float64]
    background: NDArray[np.float64]
    coefficients: dict[str, float]
    linear_count: int
    linear_names: list[str]
    linear_design: NDArray[np.float64]


def _source_profile(x, center, fwhm, eta, configuration, parameters, geometry=None):
    """One physical reflection, with constrained Cu components and total-area normalization."""
    primary = pseudo_voigt_profile(x, center, fwhm, eta)
    if configuration.radiation != "Cu Kα1/2":
        return primary
    from materials_data_studio.modules.xrd.phase_detection import CU_ALPHA1, CU_ALPHA2
    ratio = configuration.alpha2_ratio
    if not np.isfinite(ratio) or not 0 <= ratio <= 1:
        raise AnalysisError("Cu Kα2 / Kα1 ratio must be between 0 and 1")
    values = geometry or {name: parameter.value for name, parameter in parameters.items()}
    zero, displacement = values["zero_shift"], values["displacement"]
    unshifted = center - zero
    for _ in range(5):
        angle = np.deg2rad(unshifted / 2)
        unshifted -= (unshifted + zero + displacement * np.cos(angle) - center) / (1 - displacement * np.sin(angle) * np.pi / 360)
    argument = np.sin(np.deg2rad(unshifted / 2)) * CU_ALPHA2 / CU_ALPHA1
    if not 0 < argument < 1:
        return primary / (1 + ratio)
    secondary = float(np.rad2deg(2 * np.arcsin(argument)))
    secondary += zero + displacement * np.cos(np.deg2rad(secondary / 2))
    return (primary + ratio * pseudo_voigt_profile(x, secondary, fwhm, eta)) / (1 + ratio)


def _solve_linear(
    x: NDArray[np.float64],
    observed: NDArray[np.float64],
    mask: NDArray[np.bool_],
    weights: NDArray[np.float64],
    groups: list[ReflectionGroup],
    centers: NDArray[np.float64],
    fwhm: float,
    eta: float,
    configuration: FitConfiguration,
    parameters: dict[str, FitParameter],
    *,
    solve_peak_areas: bool = True,
    geometry: dict[str, float] | None = None,
) -> _LinearSolution:
    profiles = [
        _source_profile(x, center, fwhm, eta, configuration, parameters, geometry) for center in centers
    ]
    fixed = np.zeros(len(x), dtype=float)
    terms: list[NDArray[np.float64]] = []
    names: list[str] = []
    lowers: list[float] = []
    uppers: list[float] = []
    if configuration.intensity_mode == "independent":
        for group, profile in zip(groups, profiles, strict=True):
            name = f"area_{group.group_id}"
            parameter = parameters.setdefault(
                name,
                FitParameter(
                    name,
                    max(group.calculated_intensity, 0.0),
                    0.0,
                    1.0e15,
                    "intensity·deg",
                    True,
                ),
            )
            if parameter.refine and solve_peak_areas:
                terms.append(profile)
                names.append(name)
                lowers.append(max(0.0, parameter.lower))
                uppers.append(parameter.upper)
            else:
                fixed += max(0.0, parameter.value) * profile
    elif configuration.intensity_mode == "cif_ratio":
        combined = np.sum(
            [
                group.calculated_intensity / 100.0 * profile
                for group, profile in zip(groups, profiles, strict=True)
            ],
            axis=0,
        )
        parameter = parameters["phase_scale"]
        if parameter.refine:
            terms.append(combined)
            names.append("phase_scale")
            lowers.append(max(0.0, parameter.lower))
            uppers.append(parameter.upper)
        else:
            fixed += max(0.0, parameter.value) * combined
    else:
        raise AnalysisError("Unknown peak-intensity fitting mode.")

    if configuration.background_mode == "current":
        if configuration.current_background is None:
            raise AnalysisError(
                "Use current processed background requires an estimated background."
            )
        current = np.asarray(configuration.current_background, dtype=float)
        if current.shape != x.shape:
            raise AnalysisError("The current background is not aligned with the XRD data.")
        fixed += current
        background_fixed = current
    else:
        basis = background_basis(
            x, configuration.background_mode, configuration.background_order
        )
        background_fixed = np.zeros(len(x), dtype=float)
        for index in range(basis.shape[1]):
            name = f"background_{index}"
            parameter = parameters.setdefault(
                name,
                FitParameter(name, 0.0, -1.0e15, 1.0e15, "intensity", True),
            )
            if parameter.refine:
                terms.append(basis[:, index])
                names.append(name)
                lowers.append(parameter.lower)
                uppers.append(parameter.upper)
            else:
                fixed += parameter.value * basis[:, index]
                background_fixed += parameter.value * basis[:, index]

    coefficients: dict[str, float] = {}
    design = np.empty((len(x), 0), dtype=float)
    if terms:
        design = np.column_stack(terms)
        solution_values = asymmetric_reweighted_linear_fit(
            design[mask],
            observed[mask] - fixed[mask],
            weights,
            np.asarray(lowers),
            np.asarray(uppers),
            peak_resistant=any(
                name.startswith("background_") for name in names
            ),
        )
        fitted_linear = design @ solution_values
        coefficients = {
            name: float(value)
            for name, value in zip(names, solution_values, strict=True)
        }
    else:
        fitted_linear = np.zeros(len(x), dtype=float)

    calculated = fixed + fitted_linear
    background = background_fixed.copy()
    if configuration.background_mode != "current":
        basis = background_basis(
            x, configuration.background_mode, configuration.background_order
        )
        for index in range(basis.shape[1]):
            parameter_name = f"background_{index}"
            value = coefficients.get(
                parameter_name, parameters[parameter_name].value
            )
            background += value * basis[:, index]
    return _LinearSolution(
        calculated,
        background,
        coefficients,
        len(names),
        names,
        design,
    )


class FitCancelledError(AnalysisError):
    """Raised cooperatively when the user stops a running fit."""


def _check_cancel(cancel_event: Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise FitCancelledError("Fit cancelled by user.")


def _ensure_area_parameters(
    parameters: dict[str, FitParameter], groups: list[ReflectionGroup]
) -> None:
    for group in groups:
        name = f"area_{group.group_id}"
        parameters.setdefault(
            name,
            FitParameter(
                name,
                max(group.calculated_intensity, 1.0e-12),
                0.0,
                1.0e15,
                "intensity·deg",
                True,
            ),
        )


def _le_bail_intensity_cycle(
    x: NDArray[np.float64],
    observed: NDArray[np.float64],
    mask: NDArray[np.bool_],
    background: NDArray[np.float64],
    groups: list[ReflectionGroup],
    centers: NDArray[np.float64],
    fwhm: float,
    eta: float,
    parameters: dict[str, FitParameter],
    intensity_floor: float,
    configuration: FitConfiguration,
) -> float:
    """Apply one Rietveld-decomposition intensity redistribution cycle."""
    profiles = np.asarray(
        [
            _source_profile(x, center, fwhm, eta, configuration, parameters)
            for center in centers
        ],
        dtype=float,
    )
    areas = np.asarray(
        [
            max(parameters[f"area_{group.group_id}"].value, 1.0e-12)
            for group in groups
        ],
        dtype=float,
    )
    contributions = areas[:, None] * profiles
    calculated_signal = np.sum(contributions, axis=0)
    observed_signal = np.maximum(observed - background, 0.0)
    spacing = np.abs(np.gradient(x))
    active = mask & np.isfinite(observed_signal) & np.isfinite(calculated_signal)
    peak_floor = max(
        float(np.nanmax(calculated_signal)) * 1.0e-12,
        np.finfo(float).tiny,
        intensity_floor * 1.0e-12,
    )
    active &= calculated_signal > peak_floor
    denominator = np.maximum(calculated_signal, peak_floor)
    ratio = np.zeros_like(observed_signal)
    ratio[active] = observed_signal[active] / denominator[active]
    maximum_change = 0.0
    for index, group in enumerate(groups):
        parameter = parameters[f"area_{group.group_id}"]
        if not parameter.refine:
            continue
        coverage = float(np.sum(profiles[index, active] * spacing[active]))
        if coverage <= 0:
            continue
        updated = areas[index] * float(
            np.sum(profiles[index, active] * ratio[active] * spacing[active])
        ) / coverage
        updated = float(np.clip(updated, max(0.0, parameter.lower), parameter.upper))
        relative_change = abs(updated - parameter.value) / max(
            abs(parameter.value), 1.0
        )
        maximum_change = max(maximum_change, relative_change)
        parameter.value = updated
        parameter.status = "Le Bail extracted"
    return maximum_change


def _assign_linearized_uncertainties(
    x: NDArray[np.float64],
    fit_mask: NDArray[np.bool_],
    weights: NDArray[np.float64],
    residual: NDArray[np.float64],
    groups: list[ReflectionGroup],
    centers: NDArray[np.float64],
    parameters: dict[str, FitParameter],
    configuration: FitConfiguration,
    solution: _LinearSolution,
    degrees_of_freedom: int,
) -> bool:
    """Assign local standard errors for extracted areas and linear terms."""
    names: list[str] = []
    columns: list[NDArray[np.float64]] = []
    if configuration.intensity_mode == "independent":
        for group, center in zip(groups, centers, strict=True):
            name = f"area_{group.group_id}"
            if parameters[name].refine:
                names.append(name)
                columns.append(
                    _source_profile(
                        x,
                        center,
                        parameters["fwhm"].value,
                        parameters["eta"].value,
                        configuration, parameters,
                    )
                )
    for index, name in enumerate(solution.linear_names):
        if name not in names:
            names.append(name)
            columns.append(solution.linear_design[:, index])
    if not columns or degrees_of_freedom <= 0:
        return False
    design = np.column_stack(columns)
    weighted = design[fit_mask] * np.sqrt(weights)[:, None]
    normal = weighted.T @ weighted
    condition = float(np.linalg.cond(normal))
    if not np.isfinite(condition) or condition >= 1.0e14:
        return False
    variance = float(
        np.sum((residual[fit_mask] * np.sqrt(weights)) ** 2)
        / degrees_of_freedom
    )
    covariance = np.linalg.inv(normal) * variance
    errors = np.sqrt(np.maximum(np.diag(covariance), 0.0))
    for name, error in zip(names, errors, strict=True):
        parameters[name].standard_error = float(error)
    return True


def preview_pattern(
    configuration: FitConfiguration,
) -> tuple[
    NDArray[np.float64],
    NDArray[np.float64],
    list[ReflectionGroup],
]:
    """Calculate a parameter-consistent preview without optimizing anything."""
    x = np.asarray(configuration.x, dtype=float)
    parameters = _copy_parameters(configuration.parameters)
    groups = group_reflections(
        _reflections_at_geometry(configuration, parameters),
        fwhm_deg=parameters["fwhm"].value,
        grouping_factor=configuration.grouping_factor,
        fit_range=configuration.fit_range,
        excluded_ranges=configuration.excluded_ranges,
    )
    if not groups:
        return np.zeros_like(x), np.zeros_like(x), []
    _ensure_area_parameters(parameters, groups)
    values = {name: parameter.value for name, parameter in parameters.items()}
    cell = _constrained_cell(
        configuration.initial_cell, values, configuration.crystal_system
    )
    centers, spacings = _group_geometry(
        groups,
        cell,
        values["wavelength"],
        values["zero_shift"],
        values["displacement"],
    )
    fixed_parameters = {
        name: dataclasses.replace(parameter, refine=False)
        for name, parameter in parameters.items()
    }
    solution = _solve_linear(
        x,
        np.asarray(configuration.y, dtype=float),
        np.ones(len(x), dtype=bool),
        np.ones(len(x), dtype=float),
        groups,
        centers,
        values["fwhm"],
        values["eta"],
        configuration,
        fixed_parameters,
        solve_peak_areas=False,
    )
    if configuration.intensity_mode == "independent":
        areas = [
            fixed_parameters[f"area_{group.group_id}"].value
            for group in groups
        ]
    else:
        scale = fixed_parameters["phase_scale"].value
        areas = [
            scale * group.calculated_intensity / 100.0 for group in groups
        ]
    for group, center, spacing, area in zip(
        groups, centers, spacings, areas, strict=True
    ):
        group.fitted_two_theta_deg = float(center)
        group.fitted_d_a = float(spacing)
        group.fitted_area = max(0.0, float(area))
        group.fwhm_deg = values["fwhm"]
        group.status = "Preview"
    return solution.calculated, solution.background, groups


def fit_pattern(
    configuration: FitConfiguration,
    *,
    progress_callback: Callable[[FitProgress], None] | None = None,
    cancel_event: Event | None = None,
) -> PeakFitResult:
    """Fit one experimental angle-dispersive dataset against one CIF phase."""
    x = np.asarray(configuration.x, dtype=float)
    observed = np.asarray(configuration.y, dtype=float)
    if x.ndim != 1 or x.shape != observed.shape or len(x) < 4:
        raise AnalysisError("Fitting requires aligned one-dimensional XRD arrays.")
    if configuration.fit_range[0] >= configuration.fit_range[1]:
        raise AnalysisError("Fit minimum must be smaller than fit maximum.")
    parameters = _copy_parameters(configuration.parameters)
    for parameter in parameters.values():
        _validate_parameter(parameter)
    required = {"wavelength", "zero_shift", "displacement", "fwhm", "eta", "phase_scale"}
    if missing := required - parameters.keys():
        raise AnalysisError("Missing fit parameters: " + ", ".join(sorted(missing)))
    if parameters["wavelength"].value <= 0:
        raise AnalysisError("Wavelength must be positive.")
    if parameters["fwhm"].value <= 0:
        raise AnalysisError("FWHM must be positive.")
    if not 0 <= parameters["eta"].value <= 1:
        raise AnalysisError("Eta must be between 0 and 1.")
    if configuration.le_bail_cycles < 1:
        raise AnalysisError("Le Bail cycles must be at least 1.")
    fit_mask, excluded_mask = _fit_masks(configuration)
    if np.count_nonzero(fit_mask) < 4:
        raise AnalysisError("The selected fit range contains too few valid data points.")
    groups = group_reflections(
        _reflections_at_geometry(configuration, parameters),
        fwhm_deg=parameters["fwhm"].value,
        grouping_factor=configuration.grouping_factor,
        fit_range=configuration.fit_range,
        excluded_ranges=configuration.excluded_ranges,
    )
    if not groups:
        raise AnalysisError("No calculated CIF reflection lies inside the fit range.")
    _ensure_area_parameters(parameters, groups)
    weights = _weights(observed, configuration, fit_mask)
    nonlinear_names = [
        name
        for name, parameter in parameters.items()
        if name in _NONLINEAR_NAMES and parameter.refine
    ]
    independent = set(independent_lattice_parameters(configuration.crystal_system))
    nonlinear_names = [
        name
        for name in nonlinear_names
        if name not in {"a", "b", "c", "alpha", "beta", "gamma"}
        or name in independent
    ]
    if configuration.intensity_mode == "independent":
        extracted_count = sum(
            parameters.get(
                f"area_{group.group_id}",
                FitParameter("", 0.0, 0.0, 1.0, refine=True),
            ).refine
            for group in groups
        )
        linear_estimate = extracted_count
    else:
        extracted_count = 0
        linear_estimate = int(parameters["phase_scale"].refine)
    if configuration.background_mode != "current":
        order = (
            0
            if configuration.background_mode == "constant"
            else configuration.background_order
        )
        linear_estimate += sum(
            parameters.get(
                f"background_{index}",
                FitParameter("", 0.0, -1.0, 1.0, refine=True),
            ).refine
            for index in range(order + 1)
        )
    if np.count_nonzero(fit_mask) <= len(nonlinear_names) + linear_estimate:
        raise AnalysisError(
            "The fit is underdetermined: observations must exceed refined parameters."
        )

    last_linear: _LinearSolution | None = None
    last_centers = np.empty(0)
    last_d = np.empty(0)

    def evaluate(
        nonlinear_values: NDArray[np.float64],
        *,
        fwhm_floor: float = 0.0,
    ) -> tuple[_LinearSolution, NDArray[np.float64], NDArray[np.float64]]:
        value_map = {name: parameter.value for name, parameter in parameters.items()}
        value_map.update(
            {
                name: float(value)
                for name, value in zip(
                    nonlinear_names, nonlinear_values, strict=True
                )
            }
        )
        cell = _constrained_cell(
            configuration.initial_cell, value_map, configuration.crystal_system
        )
        centers, spacings = _group_geometry(
            groups,
            cell,
            value_map["wavelength"],
            value_map["zero_shift"],
            value_map["displacement"],
        )
        solution = _solve_linear(
            x,
            observed,
            fit_mask,
            weights,
            groups,
            centers,
            max(value_map["fwhm"], fwhm_floor),
            value_map["eta"],
            configuration,
            parameters,
            solve_peak_areas=configuration.intensity_mode != "independent",
            geometry=value_map,
        )
        return solution, centers, spacings

    def objective(nonlinear_values: NDArray[np.float64]) -> NDArray[np.float64]:
        nonlocal last_linear, last_centers, last_d
        _check_cancel(cancel_event)
        last_linear, last_centers, last_d = evaluate(nonlinear_values)
        return (
            (last_linear.calculated[fit_mask] - observed[fit_mask])
            * np.sqrt(weights)
        )

    initial = np.asarray([parameters[name].value for name in nonlinear_names])
    lower = np.asarray([parameters[name].lower for name in nonlinear_names])
    upper = np.asarray([parameters[name].upper for name in nonlinear_names])
    refined = None
    evaluations = 0
    completed_cycles = 0
    success = True
    message = "Le Bail intensity extraction completed."
    if nonlinear_names:
        # A narrow calculated peak can have almost no overlap with its observed
        # counterpart when the starting lattice or wavelength is modestly off.
        # In that case the local least-squares gradient vanishes.  First align
        # position parameters with a deliberately broad profile, then perform
        # the requested fit with the real profile width.
        position_names = {
            "a",
            "b",
            "c",
            "alpha",
            "beta",
            "gamma",
            "wavelength",
            "zero_shift",
            "displacement",
        }
        if position_names.intersection(nonlinear_names):
            broad_fwhm = max(2.0, 4.0 * parameters["fwhm"].value)

            def broad_objective(
                nonlinear_values: NDArray[np.float64],
            ) -> NDArray[np.float64]:
                _check_cancel(cancel_event)
                solution, _, _ = evaluate(
                    nonlinear_values, fwhm_floor=broad_fwhm
                )
                return (
                    (solution.calculated[fit_mask] - observed[fit_mask])
                    * np.sqrt(weights)
                )

            preliminary = least_squares(
                broad_objective,
                initial,
                bounds=(lower, upper),
                method="trf",
                loss=configuration.loss,
                max_nfev=100,
            )
            evaluations += int(preliminary.nfev)
            if preliminary.success and np.all(np.isfinite(preliminary.x)):
                initial = preliminary.x
    final_values = initial
    cycle_count = (
        configuration.le_bail_cycles
        if configuration.intensity_mode == "independent"
        else 1
    )
    for cycle in range(1, cycle_count + 1):
        _check_cancel(cancel_event)
        before = {
            name: float(value)
            for name, value in zip(
                nonlinear_names, final_values, strict=True
            )
        }
        before_areas = {
            group.group_id: parameters[f"area_{group.group_id}"].value
            for group in groups
        }
        last_linear, last_centers, last_d = evaluate(final_values)
        maximum_area_change = 0.0
        if configuration.intensity_mode == "independent":
            value_map = {
                name: parameter.value for name, parameter in parameters.items()
            }
            value_map.update(before)
            maximum_area_change = _le_bail_intensity_cycle(
                x,
                observed,
                fit_mask,
                last_linear.background,
                groups,
                last_centers,
                value_map["fwhm"],
                value_map["eta"],
                parameters,
                configuration.intensity_floor,
                configuration,
            )
        if nonlinear_names:
            refined = least_squares(
                objective,
                final_values,
                bounds=(lower, upper),
                method="trf",
                loss=configuration.loss,
                max_nfev=50,
            )
            evaluations += int(refined.nfev)
            final_values = refined.x
            success = bool(refined.success)
            message = str(refined.message)
            for name, value in zip(
                nonlinear_names, final_values, strict=True
            ):
                parameters[name].value = float(value)
                parameters[name].status = "Refined"
        last_linear, last_centers, last_d = evaluate(final_values)
        for name, value in last_linear.coefficients.items():
            parameters[name].value = value
            parameters[name].status = "Refined"
        changes = {
            name: float(value) - before[name]
            for name, value in zip(
                nonlinear_names, final_values, strict=True
            )
        }
        maximum_parameter_change = max(
            (abs(value) for value in changes.values()), default=0.0
        )
        selected_observed = observed[fit_mask]
        selected_calculated = last_linear.calculated[fit_mask]
        denominator = float(np.sum(weights * selected_observed**2))
        r_wp_cycle = (
            float(
                np.sqrt(
                    np.sum(
                        weights * (selected_observed - selected_calculated) ** 2
                    )
                    / denominator
                )
            )
            if denominator > 0
            else np.nan
        )
        completed_cycles = cycle
        if progress_callback is not None:
            progress_callback(
                FitProgress(
                    cycle,
                    cycle_count,
                    r_wp_cycle,
                    maximum_parameter_change,
                    changes,
                    maximum_area_change,
                )
            )
        if configuration.intensity_mode != "independent":
            break
        area_changed = max(
            (
                abs(
                    parameters[f"area_{group.group_id}"].value
                    - before_areas[group.group_id]
                )
                / max(abs(before_areas[group.group_id]), 1.0)
                for group in groups
            ),
            default=0.0,
        )
        if (
            cycle >= 2
            and maximum_parameter_change < configuration.le_bail_tolerance
            and area_changed < configuration.le_bail_tolerance
        ):
            message = f"Le Bail converged after {cycle} cycles."
            break
    last_linear, last_centers, last_d = evaluate(final_values)
    for name, value in last_linear.coefficients.items():
        parameters[name].value = value
        parameters[name].status = "Refined"

    warnings = correlation_safeguards(configuration, len(groups))
    if configuration.loss != "linear":
        warnings.append(
            "Parameter uncertainties from a robust-loss fit are an approximation "
            "and are not equivalent to conventional least-squares covariance."
        )
    correlation_names: list[str] = []
    correlation_matrix = None
    if refined is not None and len(nonlinear_names):
        jacobian = np.asarray(refined.jac, dtype=float)
        normal = jacobian.T @ jacobian
        condition = float(np.linalg.cond(normal))
        dof = (
            np.count_nonzero(fit_mask)
            - len(nonlinear_names)
            - linear_estimate
        )
        if dof > 0 and np.isfinite(condition) and condition < 1.0e12:
            covariance = (
                np.linalg.inv(normal)
                * float(np.sum(objective(refined.x) ** 2))
                / dof
            )
            standard = np.sqrt(np.maximum(np.diag(covariance), 0.0))
            for name, error in zip(nonlinear_names, standard, strict=True):
                parameters[name].standard_error = float(error)
            denominator = np.outer(standard, standard)
            correlation_matrix = np.divide(
                covariance,
                denominator,
                out=np.zeros_like(covariance),
                where=denominator > 0,
            )
            correlation_names = list(nonlinear_names)
            for row in range(len(nonlinear_names)):
                for column in range(row + 1, len(nonlinear_names)):
                    if abs(correlation_matrix[row, column]) > 0.95:
                        warnings.append(
                            "Strong parameter correlation: "
                            f"{nonlinear_names[row]} / {nonlinear_names[column]} "
                            f"({correlation_matrix[row, column]:.3f})."
                        )
        else:
            warnings.append(
                "Parameter uncertainties are unreliable because the covariance matrix is singular or poorly conditioned."
            )

    fwhm = parameters["fwhm"].value
    if configuration.intensity_mode == "independent":
        areas = [
            parameters[f"area_{group.group_id}"].value for group in groups
        ]
    else:
        scale = parameters["phase_scale"].value
        areas = [scale * group.calculated_intensity / 100.0 for group in groups]
    for group, center, spacing, area in zip(
        groups, last_centers, last_d, areas, strict=True
    ):
        group.fitted_two_theta_deg = float(center)
        group.fitted_d_a = float(spacing)
        group.fitted_area = max(0.0, float(area))
        group.fwhm_deg = fwhm
        group.status = "Fitted" if success else "Not converged"

    residual = observed - last_linear.calculated
    weighted_residual = np.zeros_like(residual)
    weighted_residual[fit_mask] = residual[fit_mask] * np.sqrt(weights)
    selected_residual = residual[fit_mask]
    selected_observed = observed[fit_mask]
    weighted_sse = float(np.sum(weighted_residual[fit_mask] ** 2))
    parameter_count = len(nonlinear_names) + linear_estimate
    dof = int(np.count_nonzero(fit_mask) - parameter_count)
    if not _assign_linearized_uncertainties(
        x,
        fit_mask,
        weights,
        residual,
        groups,
        last_centers,
        parameters,
        configuration,
        last_linear,
        dof,
    ) and linear_estimate:
        warnings.append(
            "Some linearized parameter errors could not be estimated because "
            "the final design matrix is singular or poorly conditioned."
        )
    if extracted_count:
        warnings.append(
            "Le Bail intensity errors are local linearized estimates; strong "
            "reflection overlap can make them optimistic."
        )
    denominator_rp = float(np.sum(np.abs(selected_observed)))
    denominator_rwp = float(np.sum(weights * selected_observed**2))
    statistics = FitStatistics(
        success,
        message,
        int(np.count_nonzero(fit_mask)),
        len(nonlinear_names),
        linear_estimate,
        len(groups),
        int(np.count_nonzero(excluded_mask)),
        float(np.sum(selected_residual**2)),
        float(np.sqrt(np.mean(selected_residual**2))),
        (
            float(np.sum(np.abs(selected_residual)) / denominator_rp)
            if denominator_rp > 0
            else np.nan
        ),
        (
            float(np.sqrt(weighted_sse / denominator_rwp))
            if denominator_rwp > 0
            else np.nan
        ),
        weighted_sse / dof if dof > 0 else None,
        evaluations,
        completed_cycles,
    )
    if np.count_nonzero(fit_mask) <= parameter_count:
        raise AnalysisError(
            "The fit is underdetermined: observations must exceed refined parameters."
        )
    return PeakFitResult(
        parameters,
        groups,
        x,
        observed,
        last_linear.calculated,
        last_linear.background,
        residual,
        weighted_residual,
        fit_mask,
        excluded_mask,
        statistics,
        warnings,
        correlation_names,
        correlation_matrix,
    )
