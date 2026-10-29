from __future__ import annotations

import numpy as np
import pytest

from materials_data_studio.core.exceptions import AnalysisError
from materials_data_studio.modules.ppms.resistivity import (
    BOLTZMANN_EV_K,
    fit_arrhenius,
    fit_mott_vrh,
    geometry_factor,
    resistance_to_resistivity,
    segment_transport,
    source_resistivity_to_ohm_cm,
)


@pytest.mark.parametrize(
    ("mode", "kwargs"),
    [
        ("area_length", {"area_cm2": 0.02, "length_cm": 0.1}),
        (
            "width_thickness_length",
            {"width_cm": 0.2, "thickness_cm": 0.1, "length_cm": 0.1},
        ),
        ("factor", {"factor_cm": 0.2}),
        ("volume_length", {"volume_cm3": 0.002, "length_cm": 0.1}),
    ],
)
def test_geometry_modes(mode: str, kwargs: dict[str, float]) -> None:
    factor = geometry_factor(mode, **kwargs)
    assert factor == pytest.approx(0.2)
    np.testing.assert_allclose(resistance_to_resistivity([5], factor), [1])


def test_true_resistivity_conversion_and_invalid_geometry() -> None:
    np.testing.assert_allclose(source_resistivity_to_ohm_cm([2], "ohm_m"), [200])
    with pytest.raises(AnalysisError):
        geometry_factor("factor", factor_cm=0)


def test_rho_t_and_rho_h_segmentation() -> None:
    temperature = np.array([5, 4, 3, 4, 5, 10, 10, 10, 10], dtype=float)
    field = np.array([0, 1, -1, 0, 1, -100, 0, 100, 0], dtype=float)
    rho_t = segment_transport(temperature[:5], field[:5], mode="rho_t")
    assert [segment.direction for segment in rho_t] == ["cooling", "warming"]
    rho_h = segment_transport(temperature[5:], field[5:], mode="rho_h")
    assert [segment.direction for segment in rho_h] == ["increasing", "decreasing"]
    assert [len(segment.indices) for segment in rho_h] == [3, 1]


def test_transport_segmentation_uses_inclusive_constant_thresholds() -> None:
    rho_t = segment_transport(
        [1.0, 1.05, 1.11, 1.05],
        [0, 0, 0, 0],
        mode="rho_t",
    )
    assert [segment.direction for segment in rho_t] == [
        "constant",
        "warming",
        "cooling",
    ]
    assert [len(segment.indices) for segment in rho_t] == [2, 1, 1]

    rho_h = segment_transport(
        [2, 2, 2, 2],
        [0, 50, 101, 50],
        mode="rho_h",
    )
    assert [segment.direction for segment in rho_h] == [
        "constant",
        "increasing",
        "decreasing",
    ]
    assert [len(segment.indices) for segment in rho_h] == [2, 1, 1]


def test_arrhenius_fit_uses_internal_inverse_temperature() -> None:
    temperature = np.linspace(100, 300, 100)
    rho0 = 0.003
    activation = 0.12
    rho = rho0 * np.exp(activation / (BOLTZMANN_EV_K * temperature))
    fit = fit_arrhenius(temperature, rho, minimum_k=100, maximum_k=300)
    assert fit.activation_energy_ev == pytest.approx(activation, rel=1e-8)
    assert fit.prefactor == pytest.approx(rho0, rel=1e-8)


@pytest.mark.parametrize("dimension", [1, 2, 3])
def test_mott_fit_recovers_parameters(dimension: int) -> None:
    temperature = np.linspace(20, 300, 100)
    p = 1 / (dimension + 1)
    rho0, t0 = 0.02, 800
    rho = rho0 * np.exp((t0 / temperature) ** p)
    fit = fit_mott_vrh(
        temperature, rho, dimension=dimension, minimum_k=20, maximum_k=300
    )
    assert fit.characteristic_temperature_k == pytest.approx(t0, rel=1e-7)
    assert fit.prefactor == pytest.approx(rho0, rel=1e-7)
