from __future__ import annotations

from threading import Event

import numpy as np
import pytest

from materials_data_studio.core.exceptions import AnalysisError
from materials_data_studio.modules.xrd.cif_backend import UnitCell
from materials_data_studio.modules.xrd.peak_fitting.background import (
    asymmetric_reweighted_linear_fit,
    background_basis,
)
from materials_data_studio.modules.xrd.peak_fitting.models import (
    FitConfiguration,
    FitParameter,
    PeakReflection,
)
from materials_data_studio.modules.xrd.peak_fitting.optimizer import (
    FitCancelledError,
    correlation_safeguards,
    fit_pattern,
    preview_pattern,
)
from materials_data_studio.modules.xrd.peak_fitting.profile import (
    gaussian_profile,
    lorentzian_profile,
    pseudo_voigt_profile,
)
from materials_data_studio.modules.xrd.peak_fitting.reflections import (
    d_spacing,
    group_reflections,
    shifted_two_theta,
)


@pytest.mark.parametrize(
    "profile",
    [
        lambda x: gaussian_profile(x, 0.0, 0.4),
        lambda x: lorentzian_profile(x, 0.0, 0.4),
        lambda x: pseudo_voigt_profile(x, 0.0, 0.4, 0.35),
    ],
)
def test_peak_profiles_are_area_normalized(profile) -> None:  # type: ignore[no-untyped-def]
    x = np.linspace(-1000.0, 1000.0, 1_000_001)
    assert np.trapezoid(profile(x), x) == pytest.approx(1.0, rel=2e-4)


def test_pseudo_voigt_limits_and_fwhm() -> None:
    x = np.array([-0.2, 0.0, 0.2])
    np.testing.assert_allclose(
        pseudo_voigt_profile(x, 0.0, 0.4, 0.0),
        gaussian_profile(x, 0.0, 0.4),
    )
    np.testing.assert_allclose(
        pseudo_voigt_profile(x, 0.0, 0.4, 1.0),
        lorentzian_profile(x, 0.0, 0.4),
    )
    profile = pseudo_voigt_profile(x, 0.0, 0.4, 0.3)
    assert profile[0] == pytest.approx(profile[1] / 2)
    assert profile[2] == pytest.approx(profile[1] / 2)


def test_reflection_geometry_and_shifts() -> None:
    cubic = UnitCell(4.0, 4.0, 4.0, 90.0, 90.0, 90.0)
    tetragonal = UnitCell(4.0, 4.0, 6.0, 90.0, 90.0, 90.0)
    monoclinic = UnitCell(4.0, 5.0, 6.0, 90.0, 105.0, 90.0)
    assert d_spacing((1, 1, 1), cubic) == pytest.approx(4 / np.sqrt(3))
    assert d_spacing((0, 0, 2), tetragonal) == pytest.approx(3.0)
    assert np.isfinite(d_spacing((1, 0, 1), monoclinic))
    base = shifted_two_theta(2.0, 1.540598)
    shifted = shifted_two_theta(
        2.0,
        1.540598,
        zero_shift_deg=0.2,
        displacement_coefficient_deg=0.1,
    )
    theta = np.deg2rad(base / 2)
    assert shifted == pytest.approx(base + 0.2 + 0.1 * np.cos(theta))
    with pytest.raises(AnalysisError, match="Bragg"):
        shifted_two_theta(0.1, 1.540598)


def test_reflection_grouping_preserves_members_and_threshold() -> None:
    reflections = tuple(
        PeakReflection(
            "phase",
            "Phase",
            hkl,
            (hkl,),
            position,
            2.0,
            intensity,
        )
        for hkl, position, intensity in (
            ((1, 0, 0), 20.0, 100.0),
            ((1, 1, 0), 20.09, 50.0),
            ((1, 1, 1), 30.0, 25.0),
        )
    )
    groups = group_reflections(reflections, fwhm_deg=0.4, grouping_factor=0.5)
    assert len(groups) == 2
    assert len(groups[0].reflections) == 2
    assert groups[0].calculated_intensity == pytest.approx(150.0)
    separated = group_reflections(
        reflections, fwhm_deg=0.1, grouping_factor=0.5
    )
    assert len(separated) == 3


def _parameters(cell: UnitCell) -> dict[str, FitParameter]:
    return {
        "a": FitParameter("a", cell.a, 3.5, 4.5, "Å"),
        "b": FitParameter("b", cell.b, 3.5, 4.5, "Å"),
        "c": FitParameter("c", cell.c, 3.5, 4.5, "Å"),
        "alpha": FitParameter("alpha", 90, 80, 100, "deg"),
        "beta": FitParameter("beta", 90, 80, 100, "deg"),
        "gamma": FitParameter("gamma", 90, 80, 100, "deg"),
        "wavelength": FitParameter(
            "wavelength", 1.540598, 1.4, 1.7, "Å"
        ),
        "zero_shift": FitParameter(
            "zero_shift", 0.0, -1.0, 1.0, "deg", True
        ),
        "displacement": FitParameter(
            "displacement", 0.0, -1.0, 1.0, "deg"
        ),
        "fwhm": FitParameter("fwhm", 0.25, 0.05, 1.0, "deg", True),
        "eta": FitParameter("eta", 0.3, 0.0, 1.0, "", False),
        "phase_scale": FitParameter(
            "phase_scale", 100.0, 0.0, 1.0e6, "intensity", True
        ),
    }


def test_hybrid_fit_recovers_shift_width_and_nonnegative_areas() -> None:
    cell = UnitCell(4.0, 4.0, 4.0, 90.0, 90.0, 90.0)
    wavelength = 1.540598
    hkls = ((1, 1, 0), (1, 1, 1), (2, 0, 0))
    reflections = []
    centers = []
    for index, hkl in enumerate(hkls):
        spacing = d_spacing(hkl, cell)
        center = shifted_two_theta(spacing, wavelength)
        centers.append(center)
        reflections.append(
            PeakReflection(
                "phase",
                "Phase",
                hkl,
                (hkl,),
                center,
                spacing,
                100.0 / (index + 1),
            )
        )
    x = np.linspace(20.0, 70.0, 3000)
    true_shift = 0.17
    true_fwhm = 0.32
    areas = (800.0, 450.0, 250.0)
    y = np.full_like(x, 12.0)
    for center, area in zip(centers, areas, strict=True):
        y += area * pseudo_voigt_profile(
            x, center + true_shift, true_fwhm, 0.3
        )
    result = fit_pattern(
        FitConfiguration(
            "data",
            "Synthetic",
            "phase",
            "Phase",
            "phase.cif",
            x,
            y,
            tuple(reflections),
            cell,
            "cubic",
            _parameters(cell),
            (20.0, 70.0),
            background_mode="constant",
        )
    )
    assert result.statistics.success
    assert result.parameters["zero_shift"].value == pytest.approx(
        true_shift, abs=2e-3
    )
    assert result.parameters["fwhm"].value == pytest.approx(
        true_fwhm, abs=2e-3
    )
    assert all(group.fitted_area >= 0 for group in result.groups)
    assert result.statistics.r_p < 0.01
    np.testing.assert_allclose(
        [group.fitted_area for group in result.groups],
        areas,
        rtol=0.02,
    )
    assert result.statistics.cycles > 1
    assert result.parameters["zero_shift"].standard_error is not None
    assert result.parameters["area_1"].standard_error is not None


@pytest.mark.parametrize("mode", ["independent", "cif_ratio"])
def test_cu_doublet_fit_recovers_width_shift_and_total_area(mode):
    from materials_data_studio.modules.xrd.phase_detection import CU_ALPHA1, CU_ALPHA2

    cell = UnitCell(4, 4, 4, 90, 90, 90)
    x = np.linspace(20, 100, 6000)
    y = np.full_like(x, 12.)
    reflections, areas = [], [800., 400., 200.]
    for hkl, area in zip(((1,1,0), (2,0,0), (3,1,0)), areas, strict=True):
        spacing = d_spacing(hkl, cell)
        p1 = shifted_two_theta(spacing, CU_ALPHA1, zero_shift_deg=.13)
        p2 = shifted_two_theta(spacing, CU_ALPHA2, zero_shift_deg=.13)
        y += area / 1.5 * (pseudo_voigt_profile(x,p1,.19,.3) + .5*pseudo_voigt_profile(x,p2,.19,.3))
        reflections.append(PeakReflection("phase", "Phase", hkl, (hkl,), p1-.13, spacing, area))
    result = fit_pattern(FitConfiguration("data", "Generated", "phase", "Phase", "generated.cif", x,y,tuple(reflections),cell,"cubic",_parameters(cell),(20,100),
                                           intensity_mode=mode, background_mode="current", current_background=np.full_like(x,12.), radiation="Cu Kα1/2"))
    assert result.statistics.success and result.statistics.r_p < .002
    assert result.parameters["zero_shift"].value == pytest.approx(.13, abs=.001)
    assert result.parameters["fwhm"].value == pytest.approx(.19, abs=.001)
    np.testing.assert_allclose([g.fitted_area for g in result.groups], areas, rtol=.005)


@pytest.mark.parametrize(
    ("parameter_name", "true_cell_a", "true_wavelength", "expected"),
    [
        ("a", 4.08, 1.540598, 4.08),
        ("wavelength", 4.0, 1.57, 1.57),
    ],
)
def test_position_fit_recovers_lattice_or_wavelength(
    parameter_name: str,
    true_cell_a: float,
    true_wavelength: float,
    expected: float,
) -> None:
    initial_cell = UnitCell(4.0, 4.0, 4.0, 90, 90, 90)
    true_cell = UnitCell(
        true_cell_a, true_cell_a, true_cell_a, 90, 90, 90
    )
    hkls = ((1, 1, 0), (1, 1, 1), (2, 0, 0), (2, 1, 0))
    reflections = []
    centers = []
    for index, hkl in enumerate(hkls):
        spacing = d_spacing(hkl, true_cell)
        center = shifted_two_theta(spacing, true_wavelength)
        centers.append(center)
        reflections.append(
            PeakReflection(
                "phase",
                "Phase",
                hkl,
                (hkl,),
                center,
                spacing,
                100.0 / (index + 1),
            )
        )
    x = np.linspace(20, 80, 3500)
    y = np.full_like(x, 5.0)
    for center, intensity in zip(
        centers, (100.0, 70.0, 50.0, 30.0), strict=True
    ):
        y += intensity * pseudo_voigt_profile(x, center, 0.25, 0.3)
    parameters = _parameters(initial_cell)
    parameters["zero_shift"].refine = False
    parameters["fwhm"].value = 0.25
    parameters["fwhm"].refine = False
    parameters["phase_scale"].value = 100.0
    parameters["wavelength"].value = 1.540598
    parameters[parameter_name].refine = True
    result = fit_pattern(
        FitConfiguration(
            "data",
            "Synthetic",
            "phase",
            "Phase",
            "phase.cif",
            x,
            y,
            tuple(reflections),
            initial_cell,
            "cubic",
            parameters,
            (20, 80),
            intensity_mode="cif_ratio",
            background_mode="constant",
        )
    )
    assert result.parameters[parameter_name].value == pytest.approx(
        expected, rel=2e-4
    )


def test_wavelength_lattice_correlation_warning_is_reported() -> None:
    cell = UnitCell(4, 4, 4, 90, 90, 90)
    parameters = _parameters(cell)
    parameters["a"].refine = True
    parameters["wavelength"].refine = True
    configuration = FitConfiguration(
        "data",
        "Synthetic",
        "phase",
        "Phase",
        "phase.cif",
        np.linspace(20, 80, 100),
        np.ones(100),
        (),
        cell,
        "cubic",
        parameters,
        (20, 80),
    )
    warnings = correlation_safeguards(configuration, 3)
    assert any("Wavelength and lattice" in warning for warning in warnings)


def test_preview_uses_one_geometry_for_profile_and_reflection_ticks() -> None:
    cell = UnitCell(4.0, 4.0, 4.0, 90.0, 90.0, 90.0)
    spacing = d_spacing((1, 1, 1), cell)
    reflection = PeakReflection(
        "phase",
        "Phase",
        (1, 1, 1),
        ((1, 1, 1),),
        shifted_two_theta(spacing, 1.540598),
        spacing,
        100.0,
    )
    x = np.linspace(30.0, 50.0, 5000)
    parameters = _parameters(cell)
    parameters["zero_shift"].value = 0.23
    parameters["displacement"].value = 0.12
    parameters["a"].value = 4.05
    parameters["area_0"] = FitParameter(
        "area_0", 500.0, 0.0, 1.0e6, "intensity", True
    )
    parameters["background_0"] = FitParameter(
        "background_0", 0.0, -1.0e6, 1.0e6, "intensity", True
    )
    calculated, _background, groups = preview_pattern(
        FitConfiguration(
            "data",
            "Synthetic",
            "phase",
            "Phase",
            "phase.cif",
            x,
            np.zeros_like(x),
            (reflection,),
            cell,
            "cubic",
            parameters,
            (30.0, 50.0),
            background_mode="constant",
        )
    )
    peak_x = float(x[int(np.argmax(calculated))])
    assert peak_x == pytest.approx(
        groups[0].fitted_two_theta_deg, abs=float(x[1] - x[0])
    )


def test_le_bail_reports_cycles_and_can_be_cancelled() -> None:
    cell = UnitCell(4.0, 4.0, 4.0, 90.0, 90.0, 90.0)
    spacing = d_spacing((1, 1, 1), cell)
    center = shifted_two_theta(spacing, 1.540598)
    reflection = PeakReflection(
        "phase",
        "Phase",
        (1, 1, 1),
        ((1, 1, 1),),
        center,
        spacing,
        100.0,
    )
    x = np.linspace(center - 3, center + 3, 1000)
    y = 4.0 + 300.0 * pseudo_voigt_profile(x, center, 0.25, 0.3)
    configuration = FitConfiguration(
        "data",
        "Synthetic",
        "phase",
        "Phase",
        "phase.cif",
        x,
        y,
        (reflection,),
        cell,
        "cubic",
        _parameters(cell),
        (float(x[0]), float(x[-1])),
        background_mode="constant",
        le_bail_cycles=4,
        le_bail_tolerance=0.0,
    )
    updates = []
    result = fit_pattern(configuration, progress_callback=updates.append)
    assert [update.cycle for update in updates] == [1, 2, 3, 4]
    assert all(update.total_cycles == 4 for update in updates)
    assert result.statistics.cycles == 4

    cancelled = Event()
    cancelled.set()
    with pytest.raises(FitCancelledError, match="cancelled"):
        fit_pattern(configuration, cancel_event=cancelled)


def test_asymmetric_background_is_not_pulled_up_by_peaks() -> None:
    x = np.linspace(10.0, 80.0, 2000)
    coordinate = 2 * (x - x.min()) / np.ptp(x) - 1
    baseline = 12.0 + 2.5 * coordinate - 0.8 * coordinate**2
    peaks = (
        250 * np.exp(-0.5 * ((x - 28.0) / 0.15) ** 2)
        + 180 * np.exp(-0.5 * ((x - 51.0) / 0.22) ** 2)
        + 220 * np.exp(-0.5 * ((x - 68.0) / 0.12) ** 2)
    )
    design = background_basis(x, "polynomial", 2)
    coefficients = asymmetric_reweighted_linear_fit(
        design,
        baseline + peaks,
        np.ones(len(x)),
        np.full(3, -1.0e6),
        np.full(3, 1.0e6),
        peak_resistant=True,
    )
    fitted = design @ coefficients
    ordinary = design @ np.linalg.lstsq(
        design, baseline + peaks, rcond=None
    )[0]
    assert np.sqrt(np.mean((fitted - baseline) ** 2)) < 0.1
    assert np.sqrt(np.mean((fitted - baseline) ** 2)) < np.sqrt(
        np.mean((ordinary - baseline) ** 2)
    )
