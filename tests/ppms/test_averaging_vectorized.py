"""The bulk implementation retains the original greedy scientific rule."""

from __future__ import annotations

import numpy as np
import pytest

from materials_data_studio.modules.ppms.heat_capacity import average_adjacent_heat_capacity


@pytest.mark.parametrize("size", [0, 1, 2, 3, 1001])
def test_averaging_matches_explicit_original_pair_rule(size: int):
    random = np.random.default_rng(144)
    temperature = np.cumsum(random.choice([0.01, 0.05, 0.3], size=size))
    field = random.choice([0.0, 50.0, 500.0], size=size)
    signal = random.normal(size=size)
    error = random.uniform(0, 0.1, size=size)
    if size > 10:
        temperature[3] = np.nan
        signal[5] = np.nan
        signal[6] = np.inf
        error[7] = np.nan
        field[9] = np.nan
    arrays = {"signal": signal, "signal_error": error, "field_oe": field}
    actual = average_adjacent_heat_capacity(temperature, arrays)
    groups = []
    index = 0
    while index < size:
        stop = index + 1
        if (
            stop < size
            and np.isfinite(temperature[index : stop + 1]).all()
            and abs(temperature[stop] - temperature[index]) <= 0.1
            and np.isfinite(field[index : stop + 1]).all()
            and abs(field[stop] - field[index]) <= 100
        ):
            stop += 1
        groups.append(np.arange(index, stop))
        index = stop
    for name, values in {"temperature_k": temperature, **arrays}.items():
        expected = []
        for group in groups:
            selected = values[group]
            if "error" in name:
                expected.append(
                    np.sqrt(np.sum(selected**2)) / len(group)
                    if np.isfinite(selected).all()
                    else np.nan
                )
            else:
                expected.append(
                    float(np.nanmean(selected)) if np.isfinite(selected).any() else np.nan
                )
        np.testing.assert_allclose(actual[name], expected, rtol=0, atol=0, equal_nan=True)
    np.testing.assert_array_equal(actual["source_row_start"], [group[0] for group in groups])
    np.testing.assert_array_equal(actual["source_row_stop"], [group[-1] for group in groups])
