from __future__ import annotations

import numpy as np
import pytest

from materials_data_studio.modules.mpms.models import BranchSplit
from materials_data_studio.modules.mpms.mt_analysis import (
    automatic_split,
    calculate_susceptibility,
    legacy_automatic_split,
    split_branches,
)


def test_susceptibility_legacy_definition() -> None:
    chi, inverse = calculate_susceptibility(
        [0.002, 0.004],
        [1000, 1000],
        sample_weight_mg=100,
        molar_mass_g_mol=50,
    )
    np.testing.assert_allclose(chi, [0.001, 0.002])
    np.testing.assert_allclose(inverse, [1000, 500])


def test_automatic_split_first_decreasing_point_starts_fc() -> None:
    split = automatic_split([5, 10, 20, 19, 15])
    assert split.automatic_index == 2
    zfc, fc = split_branches(np.arange(5), split)
    np.testing.assert_array_equal(zfc, [0, 1, 2])
    np.testing.assert_array_equal(fc, [3, 4])


def test_legacy_function_name_uses_corrected_automatic_boundary() -> None:
    assert legacy_automatic_split([5, 10, 20, 19]).automatic_index == 2


def test_one_branch_uses_last_index() -> None:
    split = legacy_automatic_split([1, 2, 3])
    assert split.automatic_index == 2
    _, fc = split_branches([1, 2, 3], split)
    assert len(fc) == 0


def test_manual_split_overrides_but_preserves_automatic() -> None:
    split = BranchSplit(automatic_index=3, manual_index=1)
    assert split.active_index == 1
    assert split.automatic_index == 3


def test_invalid_molar_information() -> None:
    with pytest.raises(Exception, match="Molar mass"):
        calculate_susceptibility([1], [1], sample_weight_mg=1, molar_mass_g_mol=0)
