from __future__ import annotations

import numpy as np
import pytest

from materials_data_studio.core.units import (
    EMU_PER_MOL_TO_BOHR_MAGNETON_PER_FORMULA_UNIT,
    FieldUnit,
    MagnetizationUnit,
    convert_field,
    convert_moment,
)


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        (FieldUnit.OE, 10_000.0),
        (FieldUnit.KOE, 10.0),
        (FieldUnit.TESLA, 1.0),
    ],
)
def test_field_conversions(unit: FieldUnit, expected: float) -> None:
    assert convert_field([10_000], unit)[0] == pytest.approx(expected)


def test_moment_conversions() -> None:
    moment = np.array([2.0])
    assert convert_moment(
        moment,
        MagnetizationUnit.MOMENT_EMU,
        sample_weight_mg=100,
        molar_mass_g_mol=50,
    )[0] == 2
    assert convert_moment(
        moment,
        MagnetizationUnit.EMU_PER_G,
        sample_weight_mg=100,
        molar_mass_g_mol=50,
    )[0] == 20
    molar = convert_moment(
        moment,
        MagnetizationUnit.EMU_PER_MOL,
        sample_weight_mg=100,
        molar_mass_g_mol=50,
    )[0]
    assert molar == 1000
    per_fu = convert_moment(
        moment,
        MagnetizationUnit.BOHR_MAGNETON_PER_FU,
        sample_weight_mg=100,
        molar_mass_g_mol=50,
    )[0]
    assert per_fu == pytest.approx(1000 * EMU_PER_MOL_TO_BOHR_MAGNETON_PER_FORMULA_UNIT)


def test_invalid_sample_information_is_rejected() -> None:
    with pytest.raises(ValueError, match="weight"):
        convert_moment(
            [1],
            MagnetizationUnit.EMU_PER_G,
            sample_weight_mg=0,
            molar_mass_g_mol=1,
        )

