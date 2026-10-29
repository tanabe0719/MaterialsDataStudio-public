from __future__ import annotations

from pathlib import Path

import numpy as np

from materials_data_studio.core.models import Dataset, ModuleType
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.core.session import load_session, save_session


def test_ppms_settings_and_results_round_trip(tmp_path: Path) -> None:
    dataset = Dataset(
        "Heat capacity",
        ModuleType.PPMS,
        {
            "temperature_k": np.array([2.0, 3.0]),
            "field_oe": np.array([0.0, 0.0]),
            "cp_raw_uj_k": np.array([1.0, 2.0]),
        },
        processed_data={"low_t_fit": np.array([0.1, 0.2])},
        metadata={
            "ppms_kind": "heat_capacity",
            "sample_mass_mg": 12.4,
            "formula": "Ba2FeS2CuAgTe2",
            "molar_mass_g_mol": 821.24,
            "atoms_per_formula_unit": 9,
            "selected_segment": 1,
            "debye_einstein_config": {"n_einstein": 2, "fixed": {"gamma": 0}},
        },
        analysis_results={
            "low_temperature": {"gamma_j_mol_k2": 0.01, "beta_j_mol_k4": 0.001},
            "entropy": {"value_j_mol_k": 2.5, "minimum_k": 2, "maximum_k": 3},
        },
    )
    state = ProjectState(
        [dataset],
        active_module=ModuleType.PPMS,
        module_states={
            "ppms": {
                "active_tab": 0,
                "heat_capacity": {"plot_mode": "cp_over_t", "unit": "molar"},
            }
        },
    )
    path = tmp_path / "ppms.mdss"
    save_session(state, path)
    loaded = load_session(path)
    restored = loaded.datasets[0]
    assert restored.metadata["formula"] == "Ba2FeS2CuAgTe2"
    assert restored.metadata["debye_einstein_config"]["n_einstein"] == 2
    assert restored.analysis_results["entropy"]["value_j_mol_k"] == 2.5
    np.testing.assert_allclose(restored.processed_data["low_t_fit"], [0.1, 0.2])
    assert loaded.module_states["ppms"]["heat_capacity"]["unit"] == "molar"
