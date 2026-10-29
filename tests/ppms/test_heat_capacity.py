from __future__ import annotations

import numpy as np
import pytest

from materials_data_studio.modules.ppms.heat_capacity import (
    GAS_CONSTANT,
    average_adjacent_heat_capacity,
    convert_to_molar_heat_capacity,
    debye_einstein_model,
    fit_debye_einstein,
    fit_low_temperature,
    integrate_residual_entropy,
    segment_heat_capacity,
)


def test_molar_conversion() -> None:
    cp, error = convert_to_molar_heat_capacity(
        [1000.0], [10.0], sample_mass_mg=10.0, molar_mass_g_mol=100.0
    )
    np.testing.assert_allclose(cp, [10.0])
    np.testing.assert_allclose(error, [0.1])


def test_adjacent_pair_averaging_aligns_all_parameters() -> None:
    result = average_adjacent_heat_capacity(
        [2.00, 2.08, 3.0, 4.0, 4.1],
        {
            "field_oe": [0, 10, 100, 200, 220],
            "cp_raw_uj_k": [10, 14, 30, 40, 50],
        },
    )
    np.testing.assert_allclose(result["temperature_k"], [2.04, 3.0, 4.05])
    np.testing.assert_allclose(result["field_oe"], [5, 100, 210])
    np.testing.assert_allclose(result["cp_raw_uj_k"], [12, 30, 45])


def test_heat_capacity_segmentation_uses_100_oe_field_changes_only() -> None:
    temperature = np.array([5, 4, 3, 2, 1], dtype=float)
    field = np.array([0, 50, 100, 201, 250], dtype=float)
    segments = segment_heat_capacity(temperature, field)
    assert [segment.direction for segment in segments] == [
        "field segment",
        "field segment",
    ]
    assert [len(segment.indices) for segment in segments] == [3, 2]


def test_low_temperature_fit_recovers_gamma_beta_and_debye() -> None:
    temperature = np.linspace(2, 10, 80)
    gamma = 0.012
    beta = 0.00042
    cp = gamma * temperature + beta * temperature**3
    result = fit_low_temperature(
        temperature,
        cp,
        minimum_k=2,
        maximum_k=10,
        atoms_per_formula_unit=5,
    )
    assert result.gamma_j_mol_k2 == pytest.approx(gamma, rel=1e-10)
    assert result.beta_j_mol_k4 == pytest.approx(beta, rel=1e-10)
    expected = (12 * np.pi**4 * 5 * GAS_CONSTANT / (5 * beta)) ** (1 / 3)
    assert result.debye_temperature_k == pytest.approx(expected)


def test_low_temperature_fit_can_fix_gamma_to_zero() -> None:
    temperature = np.linspace(2, 10, 80)
    beta = 0.00042
    result = fit_low_temperature(
        temperature,
        beta * temperature**3,
        minimum_k=2,
        maximum_k=10,
        fix_gamma_zero=True,
    )
    assert result.gamma_j_mol_k2 == 0
    assert result.gamma_error == 0
    assert result.beta_j_mol_k4 == pytest.approx(beta)


def test_debye_einstein_fit_recovers_synthetic_curve() -> None:
    temperature = np.linspace(8, 220, 80)
    cp = debye_einstein_model(
        temperature,
        theta_d=260,
        weight_d=2.5,
        gamma=0.006,
        theta_e=(520,),
        weight_e=(2.5,),
    )
    result = fit_debye_einstein(
        temperature,
        cp,
        minimum_k=8,
        maximum_k=220,
        n_einstein=1,
        initial={
            "theta_d": 240,
            "weight_d": 2.2,
            "gamma": 0.005,
            "theta_e1": 490,
            "weight_e1": 2.8,
        },
        constrain_atoms=5,
    )
    assert result.converged
    assert result.parameters["theta_d"] == pytest.approx(260, rel=0.02)
    assert result.parameters["theta_e1"] == pytest.approx(520, rel=0.03)
    assert result.parameters["weight_d"] + result.parameters["weight_e1"] == pytest.approx(5)


def test_entropy_integrates_delta_cp_over_temperature() -> None:
    temperature = np.linspace(2, 10, 1001)
    residual = 3 * temperature
    entropy = integrate_residual_entropy(
        temperature, residual, minimum_k=2, maximum_k=10
    )
    assert entropy == pytest.approx(24.0, rel=1e-5)
