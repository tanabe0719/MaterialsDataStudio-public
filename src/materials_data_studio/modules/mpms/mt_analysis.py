"""MPMS M–T susceptibility calculation and branch splitting."""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike, NDArray

from materials_data_studio.core.exceptions import AnalysisError
from materials_data_studio.modules.mpms.models import BranchSplit


def calculate_susceptibility(
    moment_emu: ArrayLike,
    field_oe: ArrayLike,
    *,
    sample_weight_mg: float,
    molar_mass_g_mol: float,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Calculate legacy molar susceptibility and its inverse."""
    if sample_weight_mg <= 0:
        raise AnalysisError("Sample weight must be positive.")
    if molar_mass_g_mol <= 0:
        raise AnalysisError("Molar mass must be positive.")
    moment = np.asarray(moment_emu, dtype=float)
    field = np.asarray(field_oe, dtype=float)
    if moment.shape != field.shape:
        raise AnalysisError("Moment and field arrays must have the same shape.")
    moles = (sample_weight_mg / 1000.0) / molar_mass_g_mol
    with np.errstate(divide="ignore", invalid="ignore"):
        susceptibility = moment / (field * moles)
        inverse = 1.0 / susceptibility
    return susceptibility, inverse


def automatic_split(temperature_k: ArrayLike) -> BranchSplit:
    """End ZFC immediately before the first decreasing-temperature point."""
    temperature = np.asarray(temperature_k, dtype=float)
    if temperature.ndim != 1 or temperature.size == 0:
        raise AnalysisError("Temperature must be a non-empty one-dimensional array.")
    candidates = np.flatnonzero(np.diff(temperature) < 0)
    index = int(candidates[0]) if candidates.size else len(temperature) - 1
    return BranchSplit(automatic_index=index)


# Preserve the public name used by older scripts while the UI now calls the
# corrected behavior simply "Automatic".
legacy_automatic_split = automatic_split


def split_branches(
    values: ArrayLike, split: BranchSplit
) -> tuple[NDArray[np.generic], NDArray[np.generic]]:
    """Split any aligned array using the active branch boundary."""
    array = np.asarray(values)
    index = split.active_index
    if not 0 <= index < len(array):
        raise AnalysisError("The branch split index is outside the dataset.")
    return array[: index + 1], array[index + 1 :]
