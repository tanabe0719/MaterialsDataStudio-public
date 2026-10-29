"""Scientific equivalence and bounded storage regressions for optimized MP search."""
from __future__ import annotations

import struct
import zlib
from threading import Event

import numpy as np
import pytest
from scipy.optimize import minimize_scalar

from materials_data_studio.modules.xrd.mp_codec import (
    MAGIC,
    decode_pattern,
    encode_pattern,
)
from materials_data_studio.modules.xrd.phase_models import CalculationSettings, match_peaks
from materials_data_studio.modules.xrd.phase_reference import select_mp_scale
from materials_data_studio.modules.xrd.phase_score import PreparedPeakScore


@pytest.mark.parametrize("seed", range(12))
def test_prepared_score_and_scale_equal_public_matcher(seed):
    random = np.random.default_rng(seed)
    settings = CalculationSettings(lower=10, upper=120, zero_shift=[-.5, 0, .5][seed % 3], resolution=[0, .08, .5][seed % 3])
    positions = random.uniform(5, 125, 90)
    positions[1:5] = [40, 40, 40.08, 120 - settings.zero_shift]
    intensities = random.uniform(0, 100, len(positions))
    intensities[0] = 0
    peaks = [{"position": float(p), "intensity": float(i), "included": n % 9 != 0} for n, (p, i) in enumerate(zip(positions[::4], intensities[::4], strict=True))]
    scorer = PreparedPeakScore(peaks, settings)
    assert scorer.score(positions, intensities) == match_peaks(peaks, positions.tolist(), intensities.tolist(), settings)["score"]
    ds = settings.wavelength / (2 * np.sin(np.deg2rad(positions / 2)))
    limit = [0, .01, .03][seed % 3]
    def objective(scale):
        argument = settings.wavelength / (2 * ds * scale)
        valid = (argument > 0) & (argument < 1) & (intensities > 0)
        x = np.rad2deg(2 * np.arcsin(argument[valid]))
        return -(match_peaks(peaks, x.tolist(), intensities[valid].tolist(), settings)["score"] - 200 * abs(scale - 1))
    if limit:
        grid = np.linspace(1 - limit, 1 + limit, 21)
        values = [objective(float(s)) for s in grid]
        best = int(np.argmin(values))
        expected = float(grid[best])
        fitted = minimize_scalar(objective, bounds=(float(grid[max(0, best - 1)]), float(grid[min(20, best + 1)])), method="bounded", options={"xatol": 1e-6, "maxiter": 25})
        if fitted.fun < values[best]:
            expected = float(fitted.x)
    else:
        expected = 1.
    scale, penalty = select_mp_scale(peaks, ds, intensities, settings, limit, Event())
    assert scale == expected
    assert penalty == 200 * abs(expected - 1)


def test_binary_codec_preserves_float_bits_hkl_and_unknown_fields():
    p = {"d": [3., 2., 1., .5], "intensities": [100., -0., 1e-12, 3.141592653589793],
         "hkl": [[{"hkl": [1, -2, 3], "multiplicity": 48}], [], [{"hkl": [-1, 0, 1, 2], "multiplicity": 6}], [{"hkl": [0, 0, 0], "multiplicity": 1}]]}
    encoded = encode_pattern(p)
    assert encoded.startswith(MAGIC)
    decoded = decode_pattern(encoded)
    assert decoded == p
    for k in ("d", "intensities"):
        assert np.asarray(decoded[k], dtype="<f8").tobytes() == np.asarray(p[k], dtype="<f8").tobytes()
    search = decode_pattern(encoded, include_hkl=False)
    checked = decode_pattern(encoded, include_hkl=False, validate_hkl=True)
    np.testing.assert_array_equal(checked["d"], p["d"])
    assert "hkl" not in search
    np.testing.assert_array_equal(search["d"], p["d"])
    extended = {**p, "future_field": {"preserved": True}}
    assert decode_pattern(encode_pattern(extended)) == extended
    with pytest.raises(ValueError, match=r"length|header"):
        decode_pattern(encoded[:-1])
    with pytest.raises((ValueError, zlib.error)):
        decode_pattern(encoded[:-1] + bytes([encoded[-1] ^ 1]), include_hkl=False, validate_hkl=True)
    with pytest.raises(ValueError, match="header"):
        decode_pattern(MAGIC)
    with pytest.raises(ValueError, match="length"):
        decode_pattern(MAGIC + struct.pack("<IIII", 20001, 0, 0, 0))
    with pytest.raises(ValueError, match="size limit"):
        decode_pattern(zlib.compress(b"x" * (64 * 1024**2 + 1)))


def test_prepared_rejects_invalid_observations_and_large_patterns():
    with pytest.raises(ValueError, match="finite"):
        PreparedPeakScore([{"position": float("nan"), "intensity": 1}], CalculationSettings())
    with pytest.raises(ValueError, match="1–500"):
        PreparedPeakScore([], CalculationSettings())
    scorer = PreparedPeakScore([{"position": 30, "intensity": 1}], CalculationSettings())
    with pytest.raises(ValueError, match="excessive"):
        scorer.score(np.ones(20001), np.ones(20001))
