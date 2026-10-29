"""Public, invented large-profile regression through the actual OS sandbox."""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import numpy as np
import pytest

from materials_data_studio.tools import igor_procedure_runtime as runtime
from materials_data_studio.tools.command_services import DocumentServices
from materials_data_studio.tools.graph_document import load_graph_document, save_graph_document
from materials_data_studio.tools.graph_waves import empty_document
from materials_data_studio.tools.igor_input_resources import write_input_resources
from materials_data_studio.tools.igor_procedure_conversion import analyze_procedures, build_python
from materials_data_studio.tools.procedure_policy import validate
from materials_data_studio.tools.procedure_sandbox import launch

PROFILE_POINTS = 13_000
REFLECTIONS = 2_700

# Original synthetic source: no private procedure, filenames, or measurements.
PROFILE_SOURCE = '''Function PlotInventedProfile()
    LoadWave/G/D/N=curve "invented-profile.txt"
    Rename curve0,angle
    Rename curve1,measured
    Rename curve2,model
    Rename curve3,accepted
    Wave angle,measured,model,accepted
    Wave unusedSideband=absentSideband
    Make/D/N=(numpnts(angle)) fitted=model
    Make/D/N=(numpnts(angle)) difference=measured-model
    SetScale/P x,0,1,"point",accepted
    Variable count=numpnts(angle),i
    for(i=0;i<count;i+=1)
        if(accepted(i)==0)
            fitted[i]=NaN
            difference[i]=NaN
        endif
    endfor
    Display/N=InventedProfile measured,fitted,difference vs angle
    ModifyGraph mode(measured)=3,marker(measured)=8,lsize(fitted)=1.5
    ModifyGraph offset(difference)={0,-1600},tick=2,mirror=1
    LoadWave/G/D/N=reflection "invented-reflections.txt"
    AddInventedPhase(1)
    AddInventedPhase(2)
End
Function AddInventedPhase(phase)
    Variable phase
    Wave membership=reflection3,position=reflection4
    Variable count=numpnts(position),i,n=0,j=0
    for(i=0;i<count;i+=1)
        if(membership[i]==phase)
            n+=1
        endif
    endfor
    String positionsName="phase"+num2str(phase)+"Positions"
    String ticksName="phase"+num2str(phase)+"Ticks"
    Make/D/N=(n) $positionsName
    Make/D/N=(n) $ticksName
    Wave destination=$positionsName
    for(i=0;i<count;i+=1)
        if(membership[i]==phase)
            destination[j]=position[i]
            j+=1
        endif
    endfor
    AppendToGraph $ticksName vs $positionsName
    ModifyGraph mode($ticksName)=3,marker($ticksName)=10,offset($ticksName)={0,-1700-100*phase}
End'''


def test_one_million_step_budget_is_finite_and_shared_by_all_scopes() -> None:
    assert runtime.MAX_RUNTIME_STEPS == 1_000_000
    api = runtime.IgorAPI(DocumentServices(empty_document()))
    first, second = api.scope(), api.scope()
    # Trusted test setup only: converted procedures cannot access this private
    # counter. Reach the exact boundary without a million expensive operations.
    api._steps = runtime.MAX_RUNTIME_STEPS - 2
    assert first.condition("1")
    assert second.evaluate("2+3") == 5
    assert api.steps == runtime.MAX_RUNTIME_STEPS
    for scope in (first, second, api.scope()):
        with pytest.raises(RuntimeError, match="1000000-step budget"):
            scope.condition("1")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows AppContainer integration")
def test_large_invented_profile_runs_in_appcontainer_with_two_phase_ticks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    index = np.arange(PROFILE_POINTS)
    angle = 12 + index * .008
    model = 80 + .2 * angle
    for center, width, height in ((24., .3, 900.), (49., .6, 500.), (83., .4, 700.)):
        model += height * np.exp(-((angle - center) / width) ** 2)
    measured = model + 3 * np.sin(index * .11)
    accepted = (index % 9 >= 4).astype(int)
    profile_text = "Invented diffraction profile\nangle measured model accepted\n" + "".join(
        f"{x:.12g} {y:.12g} {fit:.12g} {keep}\n"
        for x, y, fit, keep in zip(angle, measured, model, accepted, strict=True)
    )
    reflection_index = np.arange(REFLECTIONS)
    phase = 1 + (reflection_index % 7 == 0).astype(int)
    positions = 14 + reflection_index * .037
    reflections_text = "Invented conventional reflections\nh k l phase angle\n" + "".join(
        f"{i % 9 - 4} {(i // 9) % 7 - 3} {(i // 63) % 5} {group} {x:.12g}\n"
        for i, group, x in zip(reflection_index, phase, positions, strict=True)
    )
    inputs = [
        {"name": name, "path": "C:\\prepared\\" + name, "text": contents}
        for name, contents in (
            ("invented-profile.txt", profile_text),
            ("invented-reflections.txt", reflections_text),
        )
    ]
    report = analyze_procedures(PROFILE_SOURCE)
    assert all(routine.supported for routine in report.routines), [
        routine.diagnostics for routine in report.routines
    ]
    source = build_python(report, "PlotInventedProfile", [])
    # Read-only telemetry proves this workload crosses the old limit without
    # executing any converted macro in the host test process.
    source += '\nprint("Invented profile runtime steps:", igor.steps)\n'
    validate(source)
    (tmp_path / "input").mkdir()
    (tmp_path / "output").mkdir()
    input_path = tmp_path / "input/input.mdsg"
    source_path = tmp_path / "input/procedure.py"
    sidecar_path = tmp_path / "input/igor_inputs.json"
    save_graph_document(input_path, empty_document())
    source_path.write_text(source, encoding="utf-8")
    write_input_resources(sidecar_path, inputs)
    originals = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (input_path, source_path, sidecar_path)
    }

    result = launch(tmp_path)
    log = (tmp_path / "output/procedure.log").read_text(encoding="utf-8")
    assert result == 0, log
    assert "Windows AppContainer active" in capsys.readouterr().out
    assert (tmp_path / "input/permit").read_text(encoding="utf-8") == "isolated"
    assert not (tmp_path / "sandbox.json").exists()
    assert "Traceback" not in log
    assert "WAVE reference is null (no wave was created): unusedSideband" in log
    steps = re.search(r"Invented profile runtime steps: (\d+)", log)
    assert steps is not None, log
    assert 100_000 < int(steps[1]) < runtime.MAX_RUNTIME_STEPS
    for path, digest in originals.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest

    document = load_graph_document(tmp_path / "output/output.mdsg")
    waves = document.render_source()
    assert "igor_input_resources" not in document.current.environment
    assert waves.wave("absentSideband") is None
    assert waves.wave("unusedSideband") is None
    for name, expected in (("angle", angle), ("measured", measured), ("model", model)):
        wave = waves.wave(name)
        assert wave is not None and wave.shape == (PROFILE_POINTS,)
        np.testing.assert_allclose(wave.data, expected, rtol=1e-10, atol=1e-8)
    excluded = accepted == 0
    assert 0 < np.count_nonzero(excluded) < PROFILE_POINTS
    for name, expected in (("fitted", model), ("difference", measured - model)):
        wave = waves.wave(name)
        assert wave is not None and wave.shape == (PROFILE_POINTS,)
        np.testing.assert_array_equal(np.isnan(wave.data), excluded)
        np.testing.assert_allclose(wave.data[~excluded], expected[~excluded], atol=1e-8)
    for group in (1, 2):
        position_wave = waves.wave(f"phase{group}Positions")
        tick_wave = waves.wave(f"phase{group}Ticks")
        assert position_wave is not None and tick_wave is not None
        np.testing.assert_allclose(position_wave.data, positions[phase == group], atol=1e-10)
        np.testing.assert_array_equal(tick_wave.data, np.zeros(np.count_nonzero(phase == group)))

    assert len(document.current.graphs) == 1
    graph = document.graph("InventedProfile")
    assert [trace.name for trace in graph.traces] == [
        "measured", "fitted", "difference", "phase1Ticks", "phase2Ticks",
    ]
    assert [trace.x_wave for trace in graph.traces] == [
        "root:angle", "root:angle", "root:angle", "root:phase1Positions", "root:phase2Positions",
    ]
    assert graph.traces[0].mode == 3 and graph.traces[0].marker == 8
    assert graph.traces[1].line_width == 1.5
    assert graph.traces[2].offset == (0., -1600.)
    for trace, offset in zip(graph.traces[3:], (-1800., -1900.), strict=True):
        assert trace.mode == 3 and trace.marker == 10 and trace.offset == (0., offset)
    assert graph.style.axis_defaults.tick == 2 and graph.style.axis_defaults.mirror == 1
