from __future__ import annotations

import numpy as np
import pytest

from materials_data_studio.core.exceptions import InvalidAxisConversionError
from materials_data_studio.core.models import AxisType, NormalizationMode
from materials_data_studio.modules.xrd.models import TofCalibration
from materials_data_studio.modules.xrd.transforms import (
    convert_x_axis,
    d_to_inverse_d,
    d_to_two_theta,
    normalize_intensity,
    process_intensity,
    tof_to_d,
    two_theta_to_d,
)


def test_bragg_round_trip() -> None:
    angles = np.array([20.0, 40.0, 80.0])
    d = two_theta_to_d(angles, 1.540598)
    np.testing.assert_allclose(d_to_two_theta(d, 1.540598), angles)
    np.testing.assert_allclose(d_to_inverse_d(d), 1 / d)


def test_different_wavelengths_produce_different_d() -> None:
    angle = [30.0]
    assert two_theta_to_d(angle, 1.540598)[0] != pytest.approx(
        two_theta_to_d(angle, 0.709317)[0]
    )


@pytest.mark.parametrize(
    ("values", "wavelength"),
    [([0.0], 1.54), ([-10.0], 1.54), ([20.0], 0.0)],
)
def test_invalid_bragg_inputs(values: list[float], wavelength: float) -> None:
    with pytest.raises(InvalidAxisConversionError):
        two_theta_to_d(values, wavelength)


def test_reverse_bragg_domain_is_checked() -> None:
    with pytest.raises(InvalidAxisConversionError):
        d_to_two_theta([0.1], 1.54)


def test_tof_linear_and_quadratic_positive_solutions() -> None:
    d = tof_to_d([2100], TofCalibration(difa=0, difc=1000, tzero=100))
    np.testing.assert_allclose(d, [2])
    quadratic = TofCalibration(difa=10, difc=1000, tzero=100)
    tof = 10 * 4 + 1000 * 2 + 100
    np.testing.assert_allclose(tof_to_d([tof], quadratic), [2])


def test_tof_never_uses_bragg_without_calibration() -> None:
    with pytest.raises(InvalidAxisConversionError, match="calibration"):
        convert_x_axis(
            [1000],
            AxisType.TOF,
            AxisType.D_SPACING,
            wavelength_a=1.54,
        )


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (NormalizationMode.NONE, [1, 2, 4]),
        (NormalizationMode.MAX_1, [0.25, 0.5, 1]),
        (NormalizationMode.MAX_100, [25, 50, 100]),
    ],
)
def test_reversible_normalization(
    mode: NormalizationMode, expected: list[float]
) -> None:
    raw = np.array([1.0, 2.0, 4.0])
    result = normalize_intensity([0, 1, 2], raw, mode)
    np.testing.assert_allclose(result, expected)
    np.testing.assert_allclose(raw, [1, 2, 4])


def test_processing_order_background_normalization_scale_offset() -> None:
    raw = np.array([2.0, 4.0])
    result = process_intensity(
        [0, 1],
        raw,
        normalization=NormalizationMode.MAX_1,
        scale=2,
        offset=3,
        background=[1, 1],
        subtract_background=True,
    )
    np.testing.assert_allclose(result, [3 + 2 / 3, 5])
    np.testing.assert_allclose(raw, [2, 4])

