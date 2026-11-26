"""Scientific parity of the compiled Windows kernels and their reference paths."""
import os
from threading import Event

import numpy as np
import pytest

from materials_data_studio.modules.xrd import native_xrd
from materials_data_studio.modules.xrd.phase_models import CalculationSettings
from materials_data_studio.modules.xrd.phase_reference import select_mp_scale


@pytest.fixture
def compiled():
    library = native_xrd.library()
    if library is None:
        if os.environ.get("MDS_REQUIRE_NATIVE_XRD") == "1":
            pytest.fail("The required Rust XRD library could not be loaded.")
        pytest.skip("Rust XRD library is not built for this checkout.")
    return library


@pytest.mark.parametrize("hexagonal", [False, True])
def test_native_diffraction_matches_reference(compiled, hexagonal):
    from pymatgen.analysis.diffraction.xrd import XRDCalculator
    from pymatgen.core import Lattice, Structure

    lattice = Lattice.hexagonal(3.2, 5.2) if hexagonal else Lattice.rhombohedral(4.6, 81)
    structure = Structure(lattice, [{"Na": .3, "K": .5}, "Cl"], [[0, 0, 0], [.4, .4, .4]])
    wavelength = 1.540598
    limits = (1., 150.)
    expected = XRDCalculator(wavelength=wavelength).get_pattern(structure, two_theta_range=limits)
    actual = native_xrd.diffraction(structure, wavelength, limits)
    assert actual is not None
    np.testing.assert_allclose(actual.x, expected.x, atol=1e-10, rtol=0)
    np.testing.assert_allclose(actual.y, expected.y, atol=1e-9, rtol=0)
    np.testing.assert_allclose(actual.d_hkls, expected.d_hkls, atol=1e-12, rtol=0)
    assert actual.hkls == expected.hkls


@pytest.mark.parametrize("conflict", [False, True])
def test_native_lattice_scale_matches_reference(compiled, monkeypatch, conflict):
    settings = CalculationSettings(tolerance=.18)
    positions = np.array([25.0, 37.5, 57.0, 65.0])
    if conflict:
        positions = np.array([25.0, 25.19, 57.0, 65.0])
    peaks = [{"position": float(x) + .1, "intensity": float(i)} for x, i in zip(positions, [20., 100., 40., 8.], strict=True)]
    d = settings.wavelength / (2 * np.sin(np.deg2rad(positions / 2)))
    intensities = np.array([20., 100., 40., 8.])
    with monkeypatch.context() as restore:
        restore.setattr(native_xrd, "library", lambda: None)
        expected = select_mp_scale(peaks, d, intensities, settings, .01, Event())
    actual = select_mp_scale(peaks, d, intensities, settings, .01, Event())
    np.testing.assert_allclose(actual[0], expected[0], atol=1e-6, rtol=0)
    np.testing.assert_allclose(actual[1], expected[1], atol=2e-4, rtol=0)


def test_native_final_phase_assignment_matches_reference(compiled, monkeypatch):
    from materials_data_studio.modules.xrd.phase_models import match_peaks

    observed_positions = np.linspace(8., 89., 200)
    predicted_positions = np.linspace(7.1, 89.9, 2200)
    rng = np.random.default_rng(16)
    observed_intensities = rng.uniform(1., 100., len(observed_positions))
    predicted_intensities = rng.uniform(1., 100., len(predicted_positions))
    settings = CalculationSettings(resolution=.02)
    assert native_xrd.peak_pairs(
        observed_positions, observed_intensities / max(observed_intensities),
        predicted_positions, predicted_intensities / max(predicted_intensities), settings.tolerance,
    ) is not None
    peaks = [{"position": x, "intensity": y} for x, y in zip(observed_positions, observed_intensities, strict=True)]
    actual = match_peaks(peaks, predicted_positions.tolist(), predicted_intensities.tolist(), settings)
    monkeypatch.setattr(native_xrd, "peak_pairs", lambda *args: None)
    expected = match_peaks(peaks, predicted_positions.tolist(), predicted_intensities.tolist(), settings)
    for key in ("assignments", "groups", "unmatched", "missing"):
        assert actual[key] == expected[key]
    assert actual["score"] == pytest.approx(expected["score"], rel=1e-14)
    assert actual["components"] == pytest.approx(expected["components"], rel=1e-14)


@pytest.mark.parametrize("conflict", ["coupled", "tied"])
def test_native_final_phase_assignment_defers_to_scipy_for_conflicts(compiled, conflict):
    native_xrd._peak_pairs_kernel.cache_clear()
    assert native_xrd._peak_pairs_kernel() is not None
    observed = np.arange(20., 90.)
    if conflict == "coupled":
        observed[1] = 20.0625
    predicted = np.concatenate(([19.875, 20.125], np.arange(22., 90.)))
    assert native_xrd.peak_pairs(
        observed, np.ones(len(observed)), predicted, np.ones(len(predicted)), .1875,
    ) is None


@pytest.mark.parametrize("hexagonal", [False, True])
def test_native_diffraction_with_many_reflections_preserves_group_order(compiled, monkeypatch, hexagonal):
    from pymatgen.core import Lattice, Structure

    lattice = Lattice.hexagonal(11, 21) if hexagonal else Lattice.cubic(19)
    structure = Structure(lattice, ["Na", "Cl"], [[.01, .039, .081], [.5, .5, .5]])
    group = native_xrd._grouped_pattern
    called = []

    def accelerated(*args):
        result = group(*args)
        called.append(result is not None)
        return result

    monkeypatch.setattr(native_xrd, "_grouped_pattern", accelerated)
    actual = native_xrd.diffraction(structure, 1.540598, (5., 125.))
    assert called == [True]
    monkeypatch.setattr(native_xrd, "_grouped_pattern", lambda *args: None)
    expected = native_xrd.diffraction(structure, 1.540598, (5., 125.))
    assert actual is not None and expected is not None
    np.testing.assert_array_equal(actual.x, expected.x)
    np.testing.assert_allclose(actual.y, expected.y, rtol=0, atol=1e-12)
    np.testing.assert_array_equal(actual.d_hkls, expected.d_hkls)
    assert actual.hkls == expected.hkls


def test_large_reference_numeric_fields_keep_exact_values_and_invalid_input_checks(compiled):
    from materials_data_studio.core.native_numeric_text import parse_reference_numbers
    from materials_data_studio.modules.xrd.reference_index import numbers

    data = " , 1.23456789123456e-4, -0.0, 3.5," * 50
    native = parse_reference_numbers(data, 200)
    assert native is not None
    np.testing.assert_array_equal(native, [float(token) for token in data.split(",") if token.strip()])
    assert np.signbit(native[1])
    with pytest.raises(ValueError, match="nonfinite"):
        numbers(data + " inf")
    with pytest.raises(ValueError, match="too many"):
        numbers(data, limit=20)
    assert numbers(data + "1_000")[-1] == 1000.
