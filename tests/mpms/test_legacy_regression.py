from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from materials_data_studio.modules.mpms.curie_weiss import (
    curie_weiss_inverse,
    fit_curie_weiss,
)


def load_legacy_module():
    workspace = Path(__file__).parents[2]
    reference = (
        workspace.parent
        / "CurieWeiss_fitting"
        / "source"
        / "mpms2chiinvT_with_fitting_GUI_tabs_v4.py"
    )
    if not reference.is_file():
        pytest.skip("Adjacent legacy reference is unavailable.")
    spec = importlib.util.spec_from_file_location("legacy_mpms_reference", reference)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("fix_c", "fix_chi0"),
    [(True, True), (True, False), (False, True), (False, False)],
)
def test_fit_matches_attached_reference(fix_c: bool, fix_chi0: bool) -> None:
    legacy = load_legacy_module()
    rng = np.random.default_rng(42)
    temperature = np.linspace(150, 350, 250)
    inverse = curie_weiss_inverse(temperature, 3.1, 45.0, 2e-4)
    inverse += rng.normal(0, 0.003, size=temperature.size)
    frame = pd.DataFrame(
        {"Temperature_K": temperature, "susceptibility_inv": inverse}
    )
    legacy_result = legacy.run_curie_weiss_fit(
        frame,
        c_init=3.0,
        theta_init=100.0,
        chi0_init=1e-4,
        fix_c=fix_c,
        fix_chi0=fix_chi0,
    )
    result = fit_curie_weiss(
        temperature,
        inverse,
        c_init=3.0,
        theta_init=100.0,
        chi0_init=1e-4,
        fix_c=fix_c,
        fix_chi0=fix_chi0,
    )
    for field in ("c", "theta", "chi0", "c_err", "theta_err", "chi0_err", "r2", "mu_eff_calc"):
        assert getattr(result, field) == pytest.approx(
            getattr(legacy_result, field), rel=1e-12, abs=1e-12
        )
