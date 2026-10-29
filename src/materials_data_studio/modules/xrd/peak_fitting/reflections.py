"""Reflection geometry, peak shifts, CIF metadata conversion, and grouping."""

from __future__ import annotations

import re

import numpy as np

from materials_data_studio.core.exceptions import AnalysisError
from materials_data_studio.modules.xrd.cif_backend import UnitCell
from materials_data_studio.modules.xrd.peak_fitting.models import (
    PeakReflection,
    ReflectionGroup,
)

_HKL_PATTERN = re.compile(r"\(\s*(-?\d+)\s+(-?\d+)\s+(-?\d+)\s*\)")


def d_spacing(hkl: tuple[int, int, int], cell: UnitCell) -> float:
    """Calculate d(hkl) from the inverse direct-lattice metric tensor."""
    if min(cell.a, cell.b, cell.c) <= 0:
        raise AnalysisError("Lattice lengths must be positive.")
    if any(not 0 < angle < 180 for angle in (cell.alpha, cell.beta, cell.gamma)):
        raise AnalysisError("Lattice angles must be between 0 and 180 degrees.")
    alpha, beta, gamma = np.deg2rad((cell.alpha, cell.beta, cell.gamma))
    metric = np.array(
        [
            [cell.a**2, cell.a * cell.b * np.cos(gamma), cell.a * cell.c * np.cos(beta)],
            [cell.a * cell.b * np.cos(gamma), cell.b**2, cell.b * cell.c * np.cos(alpha)],
            [cell.a * cell.c * np.cos(beta), cell.b * cell.c * np.cos(alpha), cell.c**2],
        ],
        dtype=float,
    )
    try:
        reciprocal_metric = np.linalg.inv(metric)
    except np.linalg.LinAlgError as exc:
        raise AnalysisError("The lattice metric is singular.") from exc
    indices = np.asarray(hkl, dtype=float)
    inverse_d_squared = float(indices @ reciprocal_metric @ indices)
    if not np.isfinite(inverse_d_squared) or inverse_d_squared <= 0:
        raise AnalysisError("The reflection does not have a positive finite d spacing.")
    return float(1.0 / np.sqrt(inverse_d_squared))


def shifted_two_theta(
    d_a: float,
    wavelength_a: float,
    *,
    zero_shift_deg: float = 0.0,
    displacement_coefficient_deg: float = 0.0,
) -> float:
    """Apply Bragg's law and global angular shifts in degrees of 2θ."""
    if wavelength_a <= 0 or not np.isfinite(wavelength_a):
        raise AnalysisError("Wavelength must be positive and finite.")
    argument = wavelength_a / (2.0 * d_a)
    if not np.isfinite(argument) or not 0 <= argument <= 1:
        raise AnalysisError("A reflection lies outside the valid Bragg-law domain.")
    theta = float(np.arcsin(argument))
    return float(
        np.rad2deg(2.0 * theta)
        + zero_shift_deg
        + displacement_coefficient_deg * np.cos(theta)
    )


def parse_hkl_label(label: str) -> tuple[tuple[int, int, int], ...]:
    """Extract all integer hkl triples retained in a simulation label."""
    values = tuple(
        (int(match[0]), int(match[1]), int(match[2]))
        for match in _HKL_PATTERN.findall(label)
    )
    if not values:
        raise AnalysisError(f"Could not read reflection indices from '{label}'.")
    return values


def reflections_from_metadata(
    *,
    phase_id: str,
    phase_name: str,
    labels: list[str],
    positions: list[float],
    d_spacings: list[float],
    intensities: list[float],
) -> tuple[PeakReflection, ...]:
    """Convert the existing simulated-stick metadata into fit reflections."""
    if not (
        len(labels) == len(positions) == len(d_spacings) == len(intensities)
    ):
        raise AnalysisError("The CIF reflection metadata is incomplete or misaligned.")
    reflections = []
    for label, position, spacing, intensity in zip(
        labels, positions, d_spacings, intensities, strict=True
    ):
        members = parse_hkl_label(label)
        reflections.append(
            PeakReflection(
                phase_id,
                phase_name,
                members[0],
                members,
                float(position),
                float(spacing),
                max(0.0, float(intensity)),
            )
        )
    return tuple(reflections)


def group_reflections(
    reflections: tuple[PeakReflection, ...],
    *,
    fwhm_deg: float,
    grouping_factor: float,
    fit_range: tuple[float, float] | None = None,
    excluded_ranges: tuple[tuple[float, float], ...] = (),
) -> list[ReflectionGroup]:
    """Group visible unresolved reflections by a fraction of initial FWHM."""
    if fwhm_deg <= 0 or grouping_factor < 0:
        raise AnalysisError("FWHM must be positive and grouping factor non-negative.")
    selected = [
        reflection
        for reflection in reflections
        if reflection.enabled
        and (
            fit_range is None
            or fit_range[0] <= reflection.calculated_two_theta_deg <= fit_range[1]
        )
        and not any(
            lower <= reflection.calculated_two_theta_deg <= upper
            for lower, upper in excluded_ranges
        )
    ]
    selected.sort(key=lambda reflection: reflection.calculated_two_theta_deg)
    clustered: list[list[PeakReflection]] = []
    threshold = fwhm_deg * grouping_factor
    for reflection in selected:
        if (
            not clustered
            or reflection.calculated_two_theta_deg
            - clustered[-1][-1].calculated_two_theta_deg
            >= threshold
        ):
            clustered.append([reflection])
        else:
            clustered[-1].append(reflection)
    groups = []
    for group_id, members in enumerate(clustered, start=1):
        intensity = float(sum(item.calculated_intensity for item in members))
        weights = np.asarray(
            [item.calculated_intensity for item in members], dtype=float
        )
        if not np.any(weights > 0):
            weights = np.ones(len(members))
        positions = np.asarray(
            [item.calculated_two_theta_deg for item in members], dtype=float
        )
        spacings = np.asarray([item.calculated_d_a for item in members], dtype=float)
        groups.append(
            ReflectionGroup(
                group_id,
                members[0].phase_id,
                members[0].phase_name,
                tuple(members),
                float(np.average(positions, weights=weights)),
                float(np.average(spacings, weights=weights)),
                intensity,
            )
        )
    return groups


def independent_lattice_parameters(crystal_system: str) -> tuple[str, ...]:
    """Return the safe independent metric parameters for a crystal system."""
    return {
        "cubic": ("a",),
        "tetragonal": ("a", "c"),
        "orthorhombic": ("a", "b", "c"),
        "hexagonal": ("a", "c"),
        "monoclinic": ("a", "b", "c", "beta"),
        "trigonal": ("a", "c"),
        "triclinic": ("a", "b", "c", "alpha", "beta", "gamma"),
    }.get(crystal_system.lower(), ("a", "b", "c", "alpha", "beta", "gamma"))
