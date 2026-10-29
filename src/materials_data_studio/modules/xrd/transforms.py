"""Pure XRD axis and intensity transforms."""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike, NDArray

from materials_data_studio.core.exceptions import InvalidAxisConversionError
from materials_data_studio.core.models import AxisType, NormalizationMode
from materials_data_studio.modules.xrd.models import TofCalibration


def _positive_wavelength(wavelength_a: float) -> None:
    if wavelength_a <= 0 or not np.isfinite(wavelength_a):
        raise InvalidAxisConversionError("Wavelength must be a positive finite value.")


def two_theta_to_d(two_theta_deg: ArrayLike, wavelength_a: float) -> NDArray[np.float64]:
    """Convert 2θ in degrees to d spacing in angstroms."""
    _positive_wavelength(wavelength_a)
    values = np.asarray(two_theta_deg, dtype=float)
    sine = np.sin(np.deg2rad(values / 2.0))
    invalid = ~np.isfinite(sine) | (sine <= 0)
    if np.any(invalid):
        raise InvalidAxisConversionError("2θ values must produce a positive Bragg sine.")
    return wavelength_a / (2.0 * sine)


def d_to_two_theta(d_a: ArrayLike, wavelength_a: float) -> NDArray[np.float64]:
    """Convert d spacing in angstroms to 2θ in degrees."""
    _positive_wavelength(wavelength_a)
    values = np.asarray(d_a, dtype=float)
    argument = wavelength_a / (2.0 * values)
    invalid = ~np.isfinite(argument) | (values <= 0) | (argument < -1) | (argument > 1)
    if np.any(invalid):
        raise InvalidAxisConversionError(
            "d spacing must be positive and satisfy wavelength/(2d) ≤ 1."
        )
    return np.rad2deg(2.0 * np.arcsin(argument))


def d_to_inverse_d(d_a: ArrayLike) -> NDArray[np.float64]:
    """Convert positive d spacing to reciprocal angstroms."""
    values = np.asarray(d_a, dtype=float)
    if np.any(~np.isfinite(values)) or np.any(values <= 0):
        raise InvalidAxisConversionError("d spacing must be positive and finite.")
    return 1.0 / values


def tof_to_d(tof: ArrayLike, calibration: TofCalibration) -> NDArray[np.float64]:
    """Solve the conventional TOF calibration for its positive d root."""
    if not calibration.valid:
        raise InvalidAxisConversionError("Valid DIFA or DIFC calibration is required.")
    values = np.asarray(tof, dtype=float)
    shifted = values - calibration.tzero
    if calibration.difa == 0:
        roots = shifted / calibration.difc
    else:
        discriminant = calibration.difc**2 + 4.0 * calibration.difa * shifted
        if np.any(discriminant < 0):
            raise InvalidAxisConversionError("TOF calibration has a negative discriminant.")
        square_root = np.sqrt(discriminant)
        root_a = (-calibration.difc + square_root) / (2.0 * calibration.difa)
        root_b = (-calibration.difc - square_root) / (2.0 * calibration.difa)
        roots = np.where(root_a > 0, root_a, root_b)
    if np.any(~np.isfinite(roots)) or np.any(roots <= 0):
        raise InvalidAxisConversionError("TOF calibration did not produce positive d spacing.")
    return np.asarray(roots, dtype=float)


def convert_x_axis(
    values: ArrayLike,
    source_axis: AxisType,
    display_axis: AxisType,
    *,
    wavelength_a: float,
    tof_calibration: TofCalibration | None = None,
) -> NDArray[np.float64]:
    """Convert an XRD axis without applying Bragg conversion directly to TOF."""
    source_values = np.asarray(values, dtype=float)
    if source_axis is AxisType.TOF:
        if display_axis is AxisType.TOF:
            return source_values.copy()
        if tof_calibration is None:
            raise InvalidAxisConversionError("TOF-to-d calibration is not configured.")
        d_values = tof_to_d(source_values, tof_calibration)
    elif source_axis is AxisType.TWO_THETA:
        if display_axis is AxisType.TWO_THETA:
            return source_values.copy()
        d_values = two_theta_to_d(source_values, wavelength_a)
    elif source_axis is AxisType.D_SPACING:
        d_values = source_values
    elif source_axis is AxisType.INV_D:
        if np.any(source_values <= 0):
            raise InvalidAxisConversionError("1/d values must be positive.")
        d_values = 1.0 / source_values
    else:
        raise InvalidAxisConversionError(f"Unsupported source axis: {source_axis}")

    if display_axis is AxisType.D_SPACING:
        return np.asarray(d_values, dtype=float)
    if display_axis is AxisType.INV_D:
        return d_to_inverse_d(d_values)
    if display_axis is AxisType.TWO_THETA:
        return d_to_two_theta(d_values, wavelength_a)
    raise InvalidAxisConversionError("Calibrated d spacing cannot be converted back to raw TOF.")


def normalize_intensity(
    x: ArrayLike, intensity: ArrayLike, mode: NormalizationMode
) -> NDArray[np.float64]:
    """Apply a reversible normalization to a copied intensity array."""
    x_values = np.asarray(x, dtype=float)
    values = np.asarray(intensity, dtype=float).copy()
    return values * normalization_factor(x_values, values, mode)


def normalization_factor(
    x: ArrayLike, intensity: ArrayLike, mode: NormalizationMode
) -> float:
    """Return the scalar used to normalize an intensity array."""
    x_values = np.asarray(x, dtype=float)
    values = np.asarray(intensity, dtype=float)
    if mode is NormalizationMode.NONE:
        return 1.0
    if values.size == 0:
        return 1.0
    if mode in {NormalizationMode.MAX_1, NormalizationMode.MAX_100}:
        maximum = float(np.nanmax(np.abs(values)))
        if maximum == 0 or not np.isfinite(maximum):
            raise ValueError("Maximum-intensity normalization requires a nonzero finite maximum.")
        target = 1.0 if mode is NormalizationMode.MAX_1 else 100.0
        return target / maximum
    area = float(np.trapezoid(values, x_values))
    if area == 0 or not np.isfinite(area):
        raise ValueError("Area normalization requires a nonzero finite integrated area.")
    return 1.0 / area


def process_intensity(
    x: ArrayLike,
    raw_intensity: ArrayLike,
    *,
    normalization: NormalizationMode,
    scale: float = 1.0,
    offset: float = 0.0,
    background: ArrayLike | None = None,
    subtract_background: bool = False,
) -> NDArray[np.float64]:
    """Apply background, normalization, scale, and offset non-destructively."""
    values = np.asarray(raw_intensity, dtype=float).copy()
    if subtract_background:
        if background is None:
            raise ValueError("A calculated background is required before subtraction.")
        values -= np.asarray(background, dtype=float)
    return normalize_intensity(x, values, normalization) * scale + offset
