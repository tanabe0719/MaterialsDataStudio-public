from __future__ import annotations

import numpy as np

from materials_data_studio.modules.mpms.mh_analysis import (
    center_field,
    detect_sweep_segments,
)


def test_reversal_creates_separate_segments() -> None:
    field = np.array([-2, -1, 0, 1, 2, 1, 0, -1, -2], dtype=float) * 1000
    temperature = np.full_like(field, 10.0)
    segments = detect_sweep_segments(temperature, field)
    assert [segment.direction for segment in segments] == ["increasing", "decreasing"]
    assert all(segment.nominal_temperature_k == 10 for segment in segments)


def test_temperature_change_is_not_merged() -> None:
    field = [-1000, 0, 1000, -1000, 0, 1000]
    temperature = [10, 10, 10, 20, 20, 20]
    segments = detect_sweep_segments(temperature, field)
    assert {segment.nominal_temperature_k for segment in segments} == {10, 20}


def test_center_field_uses_extrema_midpoint() -> None:
    np.testing.assert_allclose(center_field([-900, 100, 1100]), [-1000, 0, 1000])

