"""Synthetic regressions found by executing converted local sample families.

No private procedure text, names, input data, or research literals are copied.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

from materials_data_studio.tools.graph_document import load_graph_document, save_graph_document
from materials_data_studio.tools.graph_waves import empty_document
from materials_data_studio.tools.igor_procedure_conversion import analyze_procedures, build_python
from materials_data_studio.tools.procedure_sandbox import launch

WINDOWS = pytest.mark.skipif(sys.platform != "win32", reason="Windows AppContainer integration")


def run_converted(tmp_path: Path, source: str, entry: str):
    (tmp_path / "input").mkdir()
    (tmp_path / "output").mkdir()
    report = analyze_procedures(source)
    assert all(r.supported for r in report.routines), report
    converted = build_python(report, entry, [])
    save_graph_document(tmp_path / "input/input.mdsg", empty_document())
    (tmp_path / "input/procedure.py").write_text(converted, encoding="utf-8")
    result = launch(tmp_path)
    log = (tmp_path / "output/procedure.log").read_text(encoding="utf-8")
    assert result == 0, log
    assert "Traceback" not in log
    assert "Unable to import Axes3D" not in log
    return load_graph_document(tmp_path / "output/output.mdsg")


@WINDOWS
def test_converted_rich_axis_text_and_case_insensitive_infinity_in_worker(tmp_path: Path) -> None:
    source = r'''#pragma rtGlobals=3
Function StyledMeasurement()
    Make/D/N=5 coordinate,signal
    coordinate=p
    signal=2*p+1
    Display/N=StyledSynthetic signal vs coordinate
    ModifyGraph tickEnab(left)={-inf,INF},tick=2,mirror=1,font="Arial"
    Label left "\\F'Arial'\\Z16Response \\S-1\\M"
    Label bottom "\\f02Position\\f00"
End'''
    document = run_converted(tmp_path, source, "StyledMeasurement")
    np.testing.assert_array_equal(document.render_source().wave("signal").data, [1, 3, 5, 7, 9])
    graph = document.graph("StyledSynthetic")
    axis = next(axis for axis in graph.axes if axis.name == "left")
    assert axis.style.tick_enable == (-float("inf"), float("inf"))
    assert graph.style.axis_defaults.tick == 2
    assert graph.style.axis_defaults.mirror == 1
    assert "Response" in axis.label_raw


@WINDOWS
def test_explicit_legacy_point_access_preserves_igor_compatibility_in_worker(tmp_path: Path) -> None:
    source = '''#pragma rtGlobals=1
Function LegacyPointAccess()
    Make/D/N=3 values
    values=p+1
    Variable count=numpnts(values),i,total=0
    for(i=0;i<=count;i+=1)
        total+=values[i]
    endfor
    Make/D/N=1 accumulated
    accumulated[0]=total
    values[count]=9
End'''
    document = run_converted(tmp_path, source, "LegacyPointAccess")
    np.testing.assert_array_equal(document.render_source().wave("values").data, [1, 2, 9])
    np.testing.assert_array_equal(document.render_source().wave("accumulated").data, [9])


@WINDOWS
def test_parameter_panel_accepts_igor_foreground_color_in_worker(tmp_path: Path) -> None:
    source = '''#pragma rtGlobals=3
Function SyntheticControls()
    NewDataFolder/O/S root:controlSettings
    Variable/G mass=7,molarMass=125
    SetDataFolder root:
    NewPanel/W=(20,30,420,180)/N=SyntheticControls as "Measurement inputs"
    GroupBox sample,pos={10,10},size={370,110},title="Input values"
    SetVariable mass,pos={20,35},size={160,20},title="Mass",value=root:controlSettings:mass
    SetVariable molar,pos={20,65},size={160,20},title="Molar mass",value=root:controlSettings:molarMass
    Button run,pos={200,35},size={160,50},title="Calculate",proc=CalculateSynthetic,fColor=(50000,40000,30000)
End
Function CalculateSynthetic(event):ButtonControl
    STRUCT WMButtonAction &event
    if(event.eventCode!=2)
        return 0
    endif
    NVAR mass=root:controlSettings:mass
    Make/D/N=1 answer
    answer[0]=mass
End'''
    document = run_converted(tmp_path, source, "SyntheticControls")
    panels = document.current.environment["igor_parameter_panels"]
    assert len(panels) == 1
    panel = panels[0]
    assert panel["name"] == "SyntheticControls"
    assert panel["rect"] == [20, 30, 420, 180]
    assert panel["groups"][0]["pos"] == [10, 10]
    assert panel["groups"][0]["size"] == [370, 110]
    assert len(panel["fields"]) == 2
    assert panel["fields"][0]["pos"] == [20, 35]
    assert panel["fields"][0]["size"] == [160, 20]
    assert panel["buttons"][0]["title"] == "Calculate"
    assert panel["buttons"][0]["python_callable"] == "mdsRoutine2"
    assert panel["buttons"][0]["pos"] == [200, 35]
    assert panel["buttons"][0]["size"] == [160, 50]
    assert panel["buttons"][0]["fcolor"] == [50000, 40000, 30000]
    assert document.render_source().wave("answer") is None
