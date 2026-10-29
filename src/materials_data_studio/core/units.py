"""Explicit unit conversions used by analysis modules."""

from __future__ import annotations

from enum import StrEnum

import numpy as np
from numpy.typing import ArrayLike, NDArray

AVOGADRO_CONSTANT = 6.022_140_76e23
BOHR_MAGNETON_EMU = 9.274_010_0657e-21
EMU_PER_MOL_TO_BOHR_MAGNETON_PER_FORMULA_UNIT = 1.0 / (
    AVOGADRO_CONSTANT * BOHR_MAGNETON_EMU
)


class FieldUnit(StrEnum):
    """Magnetic-field display units."""

    OE = "Oe"
    KOE = "kOe"
    TESLA = "T"


class MagnetizationUnit(StrEnum):
    """MPMS vertical-axis units."""

    MOMENT_EMU = "emu"
    EMU_PER_G = "emu/g"
    EMU_PER_MOL = "emu/mol"
    BOHR_MAGNETON_PER_FU = "mu_B/f.u."


def convert_field(field_oe: ArrayLike, unit: FieldUnit) -> NDArray[np.float64]:
    """Convert a field array stored in Oe to a selected display unit."""
    values = np.asarray(field_oe, dtype=float)
    factors = {FieldUnit.OE: 1.0, FieldUnit.KOE: 1e-3, FieldUnit.TESLA: 1e-4}
    return values * factors[unit]


def convert_moment(
    moment_emu: ArrayLike,
    unit: MagnetizationUnit,
    *,
    sample_weight_mg: float,
    molar_mass_g_mol: float,
) -> NDArray[np.float64]:
    """Convert moment in emu to a mass, molar, or per-formula-unit quantity."""
    values = np.asarray(moment_emu, dtype=float)
    if unit is MagnetizationUnit.MOMENT_EMU:
        return values.copy()
    sample_weight_g = sample_weight_mg / 1000.0
    if sample_weight_g <= 0:
        raise ValueError("Sample weight must be positive.")
    if unit is MagnetizationUnit.EMU_PER_G:
        return values / sample_weight_g
    if molar_mass_g_mol <= 0:
        raise ValueError("Molar mass must be positive.")
    molar_values = values / (sample_weight_g / molar_mass_g_mol)
    if unit is MagnetizationUnit.EMU_PER_MOL:
        return molar_values
    return molar_values * EMU_PER_MOL_TO_BOHR_MAGNETON_PER_FORMULA_UNIT

