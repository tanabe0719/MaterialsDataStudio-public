from __future__ import annotations

import pytest

from materials_data_studio.modules.mpms.formula import (
    calculate_molar_mass,
    parse_formula,
)


@pytest.mark.parametrize(
    ("formula", "expected"),
    [
        ("FeS2", 55.845 + 2 * 32.06),
        ("SrFeO2.5", 87.62 + 55.845 + 2.5 * 15.999),
        ("Ca3(PO4)2", 3 * 40.078 + 2 * (30.973761998 + 4 * 15.999)),
        ("K4(ON(SO3)2)2", 4 * 39.0983 + 14 * 15.999 + 2 * 14.007 + 4 * 32.06),
        ("CuSO4·5H2O", 63.546 + 32.06 + 4 * 15.999 + 5 * (2 * 1.008 + 15.999)),
    ],
)
def test_formula_mass(formula: str, expected: float) -> None:
    assert calculate_molar_mass(formula) == pytest.approx(expected)


def test_decimal_counts() -> None:
    assert parse_formula("La0.5Sr0.5CoO3")["La"] == 0.5


@pytest.mark.parametrize("formula", ["", "Xx2", "Fe(OH", "Fe)2", "Fe-2"])
def test_invalid_formula(formula: str) -> None:
    with pytest.raises(ValueError):
        calculate_molar_mass(formula)

