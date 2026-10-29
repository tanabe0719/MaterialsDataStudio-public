from __future__ import annotations

import numpy as np
import pytest

from materials_data_studio.core.exceptions import AnalysisError
from materials_data_studio.modules.xrd.background import arpls_background


def test_arpls_recovers_smooth_baseline_below_diffraction_peaks() -> None:
    x = np.linspace(0, 20, 1001)
    baseline = 8.0 + 0.15 * x + 0.004 * x**2
    peaks = (
        80 * np.exp(-0.5 * ((x - 5.0) / 0.08) ** 2)
        + 45 * np.exp(-0.5 * ((x - 12.0) / 0.15) ** 2)
        + 65 * np.exp(-0.5 * ((x - 17.0) / 0.1) ** 2)
    )
    noise = np.random.default_rng(7).normal(0.0, 0.08, x.size)
    fitted = arpls_background(
        baseline + peaks + noise,
        smoothness=1.0e6,
        convergence_ratio=1.0e-3,
        max_iterations=100,
    )
    np.testing.assert_allclose(fitted, baseline, atol=0.5)
    assert np.all(fitted < baseline + peaks + 0.6)


@pytest.mark.parametrize(
    ("values", "kwargs", "message"),
    [
        ([1, 2], {}, "at least three"),
        ([1, np.nan, 2], {}, "finite"),
        ([1, 2, 3], {"smoothness": 0}, "smoothness"),
        ([1, 2, 3], {"convergence_ratio": 1}, "ratio"),
        ([1, 2, 3], {"max_iterations": 0}, "iterations"),
    ],
)
def test_arpls_validation(values, kwargs, message: str) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(AnalysisError, match=message):
        arpls_background(values, **kwargs)
