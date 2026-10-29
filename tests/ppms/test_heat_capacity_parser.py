from __future__ import annotations

import numpy as np

from materials_data_studio.modules.ppms.heat_capacity_parser import (
    parse_heat_capacity_text,
)


def test_parser_extracts_semantic_header_and_skips_messages() -> None:
    text = """[Header]
INFO,12.4,MASS:Sample Mass (mg)
INFO,0.1,MASSERR:Sample Mass Error (mg)
INFO,821.24,MOLWGHT:Formula Weight (g/mole)
INFO,9,ATOMS:Atoms per Formula Unit
INFO,Heat run,TITLE
[Data]
Time Stamp (sec),Field (Oersted),Sample Temp (Kelvin),Samp HC (�J/K),Samp HC Err (μJ/K),Fit Deviation
CALFILE: calibration.cal
1,0.2,2.0,10,1,0.01
Error: Temperature of platform does not match
2,-0.1,3.0,20,2,0.02
"""
    parsed = parse_heat_capacity_text(text)
    assert parsed.metadata.sample_mass_mg == 12.4
    assert parsed.metadata.sample_mass_error_mg == 0.1
    assert parsed.metadata.molar_mass_g_mol == 821.24
    assert parsed.metadata.atoms_per_formula_unit == 9
    np.testing.assert_allclose(parsed.temperature_k, [2, 3])
    np.testing.assert_allclose(parsed.heat_capacity_error_uj_k, [1, 2])
    assert parsed.skipped_rows == 2
    assert "CALFILE" in parsed.messages[0]
    assert "Error:" in parsed.messages[1]
