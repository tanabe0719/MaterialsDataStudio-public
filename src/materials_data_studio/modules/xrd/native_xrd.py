"""Optional compiled XRD kernels with the existing Python calculation as fallback."""
from __future__ import annotations

import ctypes
import math
import sys
from bisect import bisect_left
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

_POINTER = ctypes.c_void_p
_COUNT = ctypes.c_size_t
_FLOAT = ctypes.c_double


@lru_cache(maxsize=1)
def library() -> ctypes.CDLL | None:
    name = "mds_xrd.dll" if sys.platform == "win32" else "libmds_xrd.dylib" if sys.platform == "darwin" else "libmds_xrd.so"
    try:
        result = ctypes.CDLL(str(Path(__file__).resolve().with_name(name)))
        result.mds_diffraction.argtypes = [_COUNT, _POINTER, _POINTER, _COUNT, _COUNT, _POINTER, _FLOAT, _POINTER, _POINTER]
        result.mds_diffraction.restype = None
        result.mds_peak_score.argtypes = [_COUNT, _POINTER, _POINTER, _COUNT, _POINTER, _POINTER, *([_FLOAT] * 7)]
        result.mds_peak_score.restype = _FLOAT
    except (OSError, AttributeError):
        return None
    return result


def diffraction(structure: Any, wavelength: float, angular_range: tuple[float, float]) -> Any:
    compiled = library()
    if compiled is None:
        return None
    from pymatgen.analysis.diffraction.core import (
        AbstractDiffractionPatternCalculator,
        DiffractionPattern,
        get_unique_families,
    )
    from pymatgen.analysis.diffraction.xrd import ATOMIC_SCATTERING_PARAMS

    lower, upper = (2 * math.sin(math.radians(angle / 2)) / wavelength for angle in angular_range)
    hkl, lengths, _, _ = structure.lattice.reciprocal_lattice_crystallographic.get_points_in_sphere(
        [[0, 0, 0]], [0, 0, 0], upper, zip_results=False,
    )
    valid = (lengths >= lower) & (lengths > 0)
    hkl, lengths = hkl[valid], lengths[valid]
    if not len(lengths) or np.any(wavelength * lengths / 2 > 1):
        return None
    indices = np.lexsort((-hkl[:, 2], -hkl[:, 1], -hkl[:, 0], lengths))
    lengths = np.ascontiguousarray(lengths[indices], dtype=np.float64)
    hkl = np.ascontiguousarray(np.rint(hkl[indices]), dtype=np.float64)
    atoms = []
    for site in structure:
        for species, occupancy in site.species.items():
            coefficients = ATOMIC_SCATTERING_PARAMS.get(species.symbol)
            if coefficients is None:
                return None
            atoms.append([species.Z, *site.frac_coords, occupancy, *(v for pair in coefficients for v in pair)])
    if not atoms:
        return None
    parameters = np.ascontiguousarray(atoms, dtype=np.float64)
    angles = np.empty_like(lengths)
    intensities = np.empty_like(lengths)
    compiled.mds_diffraction(
        len(lengths), hkl.ctypes.data, lengths.ctypes.data, len(parameters), parameters.shape[1],
        parameters.ctypes.data, wavelength, angles.ctypes.data, intensities.ctypes.data,
    )
    if not np.isfinite(intensities).all():
        return None
    # Match the reference's first peak within tolerance and preserve family
    # ordering, including four-index hexagonal reflections.
    tolerance = AbstractDiffractionPatternCalculator.TWO_THETA_TOL
    positions: list[float] = []
    grouped_intensity: list[float] = []
    ds: list[float] = []
    families: list[list[tuple[int, ...]]] = []
    hexagonal = structure.lattice.is_hexagonal()
    for angle, intensity, length, (h, k, ell) in zip(
        angles.tolist(), intensities.tolist(), lengths.tolist(), hkl.astype(np.int64).tolist(), strict=True,
    ):
        indices_hkl = (h, k, -h-k, ell) if hexagonal else (h, k, ell)
        group = bisect_left(positions, angle - tolerance)
        if group and abs(abs(positions[group - 1] - angle) - tolerance) < 1e-11:
            return None
        if group < len(positions) and abs(abs(positions[group] - angle) - tolerance) < 1e-11:
            return None
        if group < len(positions) and abs(positions[group] - angle) < tolerance:
            grouped_intensity[group] += intensity
            families[group].append(indices_hkl)
        else:
            positions.append(angle)
            grouped_intensity.append(intensity)
            ds.append(1 / length)
            families.append([indices_hkl])
    maximum = max(grouped_intensity, default=0.)
    if maximum <= 0:
        return None
    threshold = AbstractDiffractionPatternCalculator.SCALED_INTENSITY_TOL
    x, y, miller, spacing = [], [], [], []
    for position, intensity, family, d in zip(positions, grouped_intensity, families, ds, strict=True):
        normalized = intensity / maximum * 100
        if normalized > threshold:
            x.append(position)
            y.append(normalized)
            miller.append([{"hkl": key, "multiplicity": count} for key, count in get_unique_families(family).items()])
            spacing.append(d)
    return DiffractionPattern(x, y, miller, spacing)
