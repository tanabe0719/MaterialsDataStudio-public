"""Lazy pymatgen powder-XRD simulation and display-only broadening."""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray

from materials_data_studio.core.exceptions import AnalysisError
from materials_data_studio.modules.xrd.cif_backend import (
    CifDocument,
    UnitCell,
    extract_cif_tables,
    validate_cif_text,
)

_HKL_PATTERN = re.compile(
    r"\(\s*([+-]?\d+)\s+([+-]?\d+)\s+([+-]?\d+)\s*\)"
)
LOGGER = logging.getLogger(__name__)
_OCCUPANCY_MODEL_METADATA = "cif_occupancy_preview_model"
_OCCUPANCY_CONTRIBUTIONS = "__cif_occupancy_contributions"
_OCCUPANCY_PEAK_INDICES = "__cif_occupancy_peak_indices"
_OCCUPANCY_LORENTZ_FACTORS = "__cif_occupancy_lorentz_factors"


@dataclass(slots=True)
class OccupancyPreviewModel:
    """Vectorized structure-factor terms for synchronous occupancy previews."""

    labels: tuple[str, ...]
    baseline_occupancies: NDArray[np.float64]
    contributions: NDArray[np.complex128]
    peak_indices: NDArray[np.int64]
    lorentz_factors: NDArray[np.float64]
    peak_count: int

    def intensities(self, occupancies: dict[str, float]) -> NDArray[np.float64]:
        values = np.asarray(
            [occupancies.get(label, baseline) for label, baseline in zip(
                self.labels, self.baseline_occupancies, strict=True
            )],
            dtype=float,
        )
        structure_factors = self.contributions @ values
        reflection_intensities = (
            np.abs(structure_factors) ** 2 * self.lorentz_factors
        )
        peaks = np.bincount(
            self.peak_indices,
            weights=reflection_intensities,
            minlength=self.peak_count,
        ).astype(float, copy=False)
        maximum = float(np.max(peaks)) if peaks.size else 0.0
        return peaks * (100.0 / maximum) if maximum > 0 else peaks


def store_occupancy_preview_model(
    model: OccupancyPreviewModel | None,
    processed_data: MutableMapping[str, NDArray[np.generic]],
    metadata: MutableMapping[str, Any],
) -> None:
    """Persist a validated preview cache inside the session-safe dataset payload."""
    for key in (
        _OCCUPANCY_CONTRIBUTIONS,
        _OCCUPANCY_PEAK_INDICES,
        _OCCUPANCY_LORENTZ_FACTORS,
    ):
        processed_data.pop(key, None)
    metadata.pop(_OCCUPANCY_MODEL_METADATA, None)
    if model is None:
        return
    metadata[_OCCUPANCY_MODEL_METADATA] = {
        "labels": list(model.labels),
        "baseline_occupancies": model.baseline_occupancies.tolist(),
        "peak_count": model.peak_count,
    }
    processed_data[_OCCUPANCY_CONTRIBUTIONS] = np.asarray(
        model.contributions, dtype=np.complex128
    )
    processed_data[_OCCUPANCY_PEAK_INDICES] = np.asarray(
        model.peak_indices, dtype=np.int64
    )
    processed_data[_OCCUPANCY_LORENTZ_FACTORS] = np.asarray(
        model.lorentz_factors, dtype=float
    )


def restore_occupancy_preview_model(
    processed_data: Mapping[str, NDArray[np.generic]],
    metadata: Mapping[str, Any],
) -> OccupancyPreviewModel | None:
    """Restore a preview cache, rejecting incomplete or inconsistent payloads."""
    manifest = metadata.get(_OCCUPANCY_MODEL_METADATA)
    if not isinstance(manifest, dict):
        return None
    try:
        labels = tuple(str(value) for value in manifest["labels"])
        baseline = np.asarray(manifest["baseline_occupancies"], dtype=float)
        contributions = np.asarray(
            processed_data[_OCCUPANCY_CONTRIBUTIONS], dtype=np.complex128
        )
        peak_indices = np.asarray(
            processed_data[_OCCUPANCY_PEAK_INDICES], dtype=np.int64
        )
        lorentz = np.asarray(
            processed_data[_OCCUPANCY_LORENTZ_FACTORS], dtype=float
        )
        peak_count = int(manifest["peak_count"])
    except (KeyError, TypeError, ValueError):
        return None
    if (
        not labels
        or baseline.shape != (len(labels),)
        or contributions.ndim != 2
        or contributions.shape[1] != len(labels)
        or peak_indices.shape != (contributions.shape[0],)
        or lorentz.shape != (contributions.shape[0],)
        or peak_count < 1
        or np.any(peak_indices < 0)
        or np.any(peak_indices >= peak_count)
        or not np.all(np.isfinite(baseline))
        or not np.all(np.isfinite(contributions))
        or not np.all(np.isfinite(lorentz))
    ):
        return None
    return OccupancyPreviewModel(
        labels,
        baseline,
        contributions,
        peak_indices,
        lorentz,
        peak_count,
    )


@dataclass(slots=True)
class SimulatedPattern:
    """Stick positions/intensities and optional Gaussian profile."""

    two_theta_deg: NDArray[np.float64]
    intensity: NDArray[np.float64]
    profile_x: NDArray[np.float64] | None = None
    profile_y: NDArray[np.float64] | None = None
    hkl_labels: list[str] | None = None
    d_spacing_a: NDArray[np.float64] | None = None
    occupancy_model: OccupancyPreviewModel | None = None


def _build_occupancy_preview_model(
    structure: Any,
    positions: NDArray[np.float64],
    exact_intensities: NDArray[np.float64],
    *,
    wavelength_a: float,
    two_theta_range: tuple[float, float],
    baseline_occupancies: dict[str, float],
) -> OccupancyPreviewModel | None:
    """Cache per-site structure-factor amplitudes used while a slider is moving."""
    if not baseline_occupancies or len(set(baseline_occupancies)) != len(baseline_occupancies):
        return None
    from pymatgen.analysis.diffraction.xrd import ATOMIC_SCATTERING_PARAMS

    labels = tuple(baseline_occupancies)
    label_indices = {label: index for index, label in enumerate(labels)}
    atomic_labels: list[int] = []
    atomic_numbers: list[float] = []
    coefficients: list[Any] = []
    coordinates: list[Any] = []
    unit_occupancies: list[float] = []
    for site in structure:
        label = str(site.label)
        if label not in label_indices:
            return None
        baseline = float(baseline_occupancies[label])
        if baseline <= 0:
            return None
        for species, occupancy in site.species.items():
            atomic_labels.append(label_indices[label])
            atomic_numbers.append(float(species.Z))
            coefficients.append(ATOMIC_SCATTERING_PARAMS[species.symbol])
            coordinates.append(site.frac_coords)
            unit_occupancies.append(float(occupancy) / baseline)
    if not atomic_labels:
        return None
    atom_label_indices = np.asarray(atomic_labels, dtype=np.int64)
    zs = np.asarray(atomic_numbers, dtype=float)
    coeffs = np.asarray(coefficients, dtype=float)
    frac_coords = np.asarray(coordinates, dtype=float)
    unit_occus = np.asarray(unit_occupancies, dtype=float)

    reciprocal = structure.lattice.reciprocal_lattice_crystallographic
    min_r, max_r = [
        2.0 * math.sin(math.radians(value / 2.0)) / wavelength_a
        for value in two_theta_range
    ]
    reciprocal_points = reciprocal.get_points_in_sphere(
        [[0, 0, 0]], [0, 0, 0], max_r
    )
    rows: list[NDArray[np.complex128]] = []
    peak_indices: list[int] = []
    lorentz_factors: list[float] = []
    for hkl_values, g_hkl, _index, _image in reciprocal_points:
        if not (g_hkl >= min_r and g_hkl > 0):
            continue
        hkl = np.asarray([round(value) for value in hkl_values], dtype=float)
        theta = math.asin(wavelength_a * g_hkl / 2.0)
        two_theta = math.degrees(2.0 * theta)
        nearest = int(np.argmin(np.abs(positions - two_theta)))
        if abs(float(positions[nearest]) - two_theta) >= 1.0e-4:
            continue
        s2 = (g_hkl / 2.0) ** 2
        scattering = zs - 41.78214 * s2 * np.sum(
            coeffs[:, :, 0] * np.exp(-coeffs[:, :, 1] * s2), axis=1
        )
        phases = np.exp(2j * np.pi * (frac_coords @ hkl))
        atomic_terms = scattering * unit_occus * phases
        row = np.zeros(len(labels), dtype=np.complex128)
        np.add.at(row, atom_label_indices, atomic_terms)
        rows.append(row)
        peak_indices.append(nearest)
        lorentz_factors.append(
            (1.0 + math.cos(2.0 * theta) ** 2)
            / (math.sin(theta) ** 2 * math.cos(theta))
        )
    if not rows or len(set(peak_indices)) != len(positions):
        return None
    model = OccupancyPreviewModel(
        labels,
        np.asarray([baseline_occupancies[label] for label in labels], dtype=float),
        np.asarray(rows, dtype=np.complex128),
        np.asarray(peak_indices, dtype=np.int64),
        np.asarray(lorentz_factors, dtype=float),
        len(positions),
    )
    reconstructed = model.intensities(baseline_occupancies)
    if not np.allclose(reconstructed, exact_intensities, rtol=2.0e-6, atol=2.0e-6):
        LOGGER.debug("Occupancy preview model did not reproduce the exact baseline pattern.")
        return None
    return model


def _pymatgen_classes():  # type: ignore[no-untyped-def]
    """Import pymatgen only for structure conversion and simulation."""
    try:
        from pymatgen.analysis.diffraction.xrd import XRDCalculator
        from pymatgen.core import Structure
    except ImportError as exc:
        raise AnalysisError(
            "XRD simulation requires pymatgen. Synchronize the project environment first."
        ) from exc
    return Structure, XRDCalculator


def simulate_cif(
    document: CifDocument,
    *,
    wavelength_a: float,
    two_theta_range: tuple[float, float] = (1.0, 90.0),
    fwhm_deg: float | None = None,
    profile_step_deg: float = 0.01,
    profile_points: int | None = None,
    radiation: str = "Custom",
    alpha2_ratio: float = 0.5,
    occupancy_preview: bool = True,
) -> SimulatedPattern:
    """Simulate sticks and an optional Gaussian display profile from edited CIF."""
    validate_cif_text(document.edited_text)
    Structure, XRDCalculator = _pymatgen_classes()
    try:
        structure = Structure.from_str(document.edited_text, fmt="cif")
        calculator_class = cast(Any, XRDCalculator)
        pattern = calculator_class(wavelength=wavelength_a).get_pattern(
            structure, two_theta_range=two_theta_range
        )
    except Exception as exc:
        raise AnalysisError(f"Could not simulate the CIF pattern: {exc}") from exc
    positions = np.asarray(pattern.x, dtype=float)
    intensities = np.asarray(pattern.y, dtype=float)
    hkl_labels = [
        ", ".join(
            "(" + " ".join(str(index) for index in reflection["hkl"]) + ")" for reflection in peak
        )
        for peak in pattern.hkls
    ]
    d_spacing = np.asarray(pattern.d_hkls, dtype=float)
    try:
        if not occupancy_preview:
            raise ValueError("Occupancy preview not requested")
        _cell, sites, _warnings = extract_cif_tables(document)
        occupancy_model = _build_occupancy_preview_model(
            structure,
            positions,
            intensities,
            wavelength_a=wavelength_a,
            two_theta_range=two_theta_range,
            baseline_occupancies={site.label: site.occupancy for site in sites},
        )
    except Exception as exc:
        LOGGER.debug("Occupancy preview cache was not available: %s", exc)
        occupancy_model = None
    sticks = SimulatedPattern(
        positions,
        intensities,
        hkl_labels=hkl_labels,
        d_spacing_a=d_spacing,
        occupancy_model=occupancy_model,
    )
    if fwhm_deg is None:
        return sticks
    return broaden_pattern(
        sticks,
        two_theta_range=two_theta_range,
        fwhm_deg=fwhm_deg,
        profile_step_deg=profile_step_deg,
        profile_points=profile_points,
        radiation=radiation,
        alpha2_ratio=alpha2_ratio,
    )


def preview_pattern_for_cell(
    pattern: SimulatedPattern,
    *,
    cell: UnitCell,
    wavelength_a: float,
    two_theta_range: tuple[float, float] | None = None,
) -> SimulatedPattern:
    """Shift existing reflections instantly from the current unit-cell metric.

    This geometry-only preview deliberately retains the last exact intensities.
    The background pymatgen calculation replaces it after slider interaction
    settles, including any intensity changes caused by structure or occupancy.
    """
    labels = pattern.hkl_labels or []
    if len(labels) != len(pattern.two_theta_deg):
        raise AnalysisError("Reflection indices are required for a live lattice preview.")
    indices: list[tuple[int, int, int]] = []
    for label in labels:
        match = _HKL_PATTERN.search(label)
        if match is None:
            raise AnalysisError(f"Could not read reflection indices from '{label}'.")
        indices.append(
            (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        )

    alpha, beta, gamma = np.deg2rad((cell.alpha, cell.beta, cell.gamma))
    metric = np.asarray(
        (
            (cell.a**2, cell.a * cell.b * np.cos(gamma), cell.a * cell.c * np.cos(beta)),
            (cell.a * cell.b * np.cos(gamma), cell.b**2, cell.b * cell.c * np.cos(alpha)),
            (cell.a * cell.c * np.cos(beta), cell.b * cell.c * np.cos(alpha), cell.c**2),
        ),
        dtype=float,
    )
    try:
        reciprocal_metric = np.linalg.inv(metric)
    except np.linalg.LinAlgError as exc:
        raise AnalysisError("The unit-cell metric is singular.") from exc
    hkl = np.asarray(indices, dtype=float)
    inverse_d_squared = np.einsum("ni,ij,nj->n", hkl, reciprocal_metric, hkl)
    valid = np.isfinite(inverse_d_squared) & (inverse_d_squared > 0)
    d_spacing = np.full(len(hkl), np.nan, dtype=float)
    d_spacing[valid] = 1.0 / np.sqrt(inverse_d_squared[valid])
    bragg_ratio = wavelength_a / (2.0 * d_spacing)
    valid &= np.isfinite(bragg_ratio) & (bragg_ratio > 0) & (bragg_ratio <= 1.0)
    two_theta = np.full(len(hkl), np.nan, dtype=float)
    two_theta[valid] = np.rad2deg(2.0 * np.arcsin(bragg_ratio[valid]))
    if two_theta_range is not None:
        valid &= (two_theta >= two_theta_range[0]) & (two_theta <= two_theta_range[1])
    selected = np.flatnonzero(valid)
    selected = selected[np.argsort(two_theta[selected])]
    return SimulatedPattern(
        two_theta[selected],
        np.asarray(pattern.intensity, dtype=float)[selected],
        hkl_labels=[labels[index] for index in selected],
        d_spacing_a=d_spacing[selected],
        occupancy_model=pattern.occupancy_model,
    )


def preview_pattern_for_occupancies(
    pattern: SimulatedPattern,
    occupancies: dict[str, float],
) -> SimulatedPattern:
    """Update relative stick intensities synchronously from cached structure factors."""
    if pattern.occupancy_model is None:
        raise AnalysisError("An occupancy preview model is not available yet.")
    return SimulatedPattern(
        np.asarray(pattern.two_theta_deg, dtype=float),
        pattern.occupancy_model.intensities(occupancies),
        hkl_labels=list(pattern.hkl_labels or []),
        d_spacing_a=(
            None
            if pattern.d_spacing_a is None
            else np.asarray(pattern.d_spacing_a, dtype=float)
        ),
        occupancy_model=pattern.occupancy_model,
    )


def display_pattern_data(pattern: SimulatedPattern, radiation: str = "Custom") -> tuple[np.ndarray, np.ndarray]:
    """Expand display sticks while retaining one physical reflection per hkl."""
    if pattern.profile_x is not None and pattern.profile_y is not None:
        return pattern.profile_x, pattern.profile_y
    positions, intensity = pattern.two_theta_deg, pattern.intensity
    if radiation == "Cu Kα1/2":
        from materials_data_studio.modules.xrd.phase_detection import CU_ALPHA1, CU_ALPHA2
        argument = np.sin(np.deg2rad(positions / 2)) * CU_ALPHA2 / CU_ALPHA1
        valid = (argument > 0) & (argument < 1)
        positions = np.concatenate([positions, np.rad2deg(2 * np.arcsin(argument[valid]))])
        intensity = np.concatenate([intensity, intensity[valid] * .5])
        order = np.argsort(positions, kind="stable")
        positions, intensity = positions[order], intensity[order]
    return positions, intensity


def broaden_pattern(
    pattern: SimulatedPattern,
    *,
    two_theta_range: tuple[float, float],
    fwhm_deg: float,
    profile_step_deg: float = 0.01,
    profile_points: int | None = None,
    radiation: str = "Custom",
    alpha2_ratio: float = 0.5,
) -> SimulatedPattern:
    """Create a Gaussian profile from existing sticks without rerunning diffraction."""
    if fwhm_deg <= 0:
        raise AnalysisError("FWHM must be positive.")
    if profile_step_deg <= 0 or not np.isfinite(profile_step_deg):
        raise AnalysisError("Profile step must be positive and finite.")
    if profile_points is None:
        span = two_theta_range[1] - two_theta_range[0]
        profile_points = max(2, int(np.ceil(span / profile_step_deg)) + 1)
    elif profile_points < 2:
        raise AnalysisError("At least two profile points are required.")
    profile_x = np.linspace(*two_theta_range, profile_points)
    sigma = fwhm_deg / (2.0 * np.sqrt(2.0 * np.log(2.0)))
    profile_y = np.zeros_like(profile_x)
    # Gaussian values beyond six sigma are below 1.6e-8 of the peak. Updating
    # only that local window avoids allocating/evaluating the complete profile
    # once per reflection and keeps live lattice previews comfortably interactive.
    half_window = 6.0 * sigma
    positions, intensities = pattern.two_theta_deg, pattern.intensity
    if radiation == "Cu Kα1/2":
        from materials_data_studio.modules.xrd.phase_detection import CU_ALPHA1, CU_ALPHA2
        if not np.isfinite(alpha2_ratio) or not 0 <= alpha2_ratio <= 1:
            raise AnalysisError("Cu Kα2 / Kα1 ratio must be between 0 and 1")
        arguments = np.sin(np.deg2rad(positions / 2)) * CU_ALPHA2 / CU_ALPHA1
        valid = (arguments > 0) & (arguments < 1)
        positions = np.concatenate([positions, np.rad2deg(2 * np.arcsin(arguments[valid]))])
        intensities = np.concatenate([intensities, intensities[valid] * alpha2_ratio])
    for position, intensity in zip(positions, intensities, strict=True):
        start = int(np.searchsorted(profile_x, position - half_window, side="left"))
        stop = int(np.searchsorted(profile_x, position + half_window, side="right"))
        if start >= stop:
            continue
        delta = (profile_x[start:stop] - position) / sigma
        profile_y[start:stop] += intensity * np.exp(-0.5 * delta**2)
    if np.max(profile_y) > 0:
        profile_y *= 100.0 / np.max(profile_y)
    return SimulatedPattern(
        pattern.two_theta_deg,
        pattern.intensity,
        profile_x,
        profile_y,
        pattern.hkl_labels,
        pattern.d_spacing_a,
        pattern.occupancy_model,
    )
