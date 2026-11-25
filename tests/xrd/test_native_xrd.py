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
