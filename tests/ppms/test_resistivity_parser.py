from __future__ import annotations

import numpy as np

from materials_data_studio.modules.ppms.resistivity_parser import parse_resistivity_text


def test_parser_distinguishes_bridge_sources_and_empty_bridge() -> None:
    text = """[Header]
INFO,BFSAT,Sample1 Name
INFO,1,Sample1 Cross Section
INFO,2,Sample1 Length
INFO,Ohm,Sample1 Units
INFO,Fourth,Sample4 Name
INFO,Ohm-m,Sample4 Units
[Data]
Time Stamp (sec),Temperature (K),Magnetic Field (Oe),Bridge 1 Resistivity (Ohm),Bridge 1 Resistance (Ohms),Bridge 2 Resistivity (Ohm-cm),Bridge 4 Resistivity (Ohm-m)
1,2,0,99,10,0.5,
2,3,1,98,11,0.6,
"""
    parsed = parse_resistivity_text(text)
    assert parsed.bridges[1].sample_name == "BFSAT"
    assert parsed.bridges[1].cross_section == 1
    assert parsed.bridges[1].length == 2
    assert parsed.bridges[1].selected_source == "resistance"
    assert parsed.bridges[1].selected_kind == "resistance"
    assert parsed.bridges[2].selected_source == "resistivity_ohm_cm"
    assert parsed.bridges[2].selected_kind == "resistivity"
    assert not parsed.bridges[4].available
    np.testing.assert_allclose(parsed.bridge_data[1]["resistance"], [10, 11])
