from __future__ import annotations

import numpy as np
import pytest

from materials_data_studio.modules.mpms.curie_weiss import (
    c_from_mu_eff,
    curie_weiss_inverse,
    fit_curie_weiss,
    mu_eff_from_c,
)


@pytest.mark.parametrize(
    ("fix_c", "fix_chi0"),
    [(True, True), (True, False), (False, True), (False, False)],
)
def test_all_fixed_free_combinations(fix_c: bool, fix_chi0: bool) -> None:
    temperature = np.linspace(150, 350, 201)
    c, theta, chi0 = 3.2, 42.0, 1.5e-4
    inverse = curie_weiss_inverse(temperature, c, theta, chi0)
    result = fit_curie_weiss(
        temperature,
        inverse,
        c_init=c,
        theta_init=30,
        chi0_init=chi0,
        fix_c=fix_c,
        fix_chi0=fix_chi0,
        branch="FC",
    )
    assert result.c == pytest.approx(c, rel=1e-7)
    assert result.theta == pytest.approx(theta, rel=1e-7)
    assert result.chi0 == pytest.approx(chi0, rel=1e-7, abs=1e-10)
    assert result.r2 == pytest.approx(1.0)
    assert result.point_count == 201
    assert result.branch == "FC"
    assert result.c_err == 0 if fix_c else result.c_err >= 0
    assert result.chi0_err == 0 if fix_chi0 else result.chi0_err >= 0


def test_uncertainties_and_r2_with_noise() -> None:
    rng = np.random.default_rng(1234)
    temperature = np.linspace(100, 300, 300)
    exact = curie_weiss_inverse(temperature, 2.5, -10, 2e-4)
    observed = exact + rng.normal(scale=0.01, size=temperature.size)
    result = fit_curie_weiss(
        temperature,
        observed,
        c_init=2.4,
        theta_init=0,
        chi0_init=0,
    )
    assert result.c_err > 0
    assert result.theta_err > 0
    assert result.chi0_err > 0
    assert result.r2 > 0.999


def test_effective_moment_relations() -> None:
    assert c_from_mu_eff(4) == 2
    assert mu_eff_from_c(2) == 4
    assert np.isnan(mu_eff_from_c(-1))


def test_too_few_points_is_rejected() -> None:
    with pytest.raises(Exception, match="points"):
        fit_curie_weiss([1, 2, 3], [1, 2, 3], c_init=1)

