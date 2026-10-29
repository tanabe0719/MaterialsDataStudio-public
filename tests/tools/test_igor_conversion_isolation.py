"""Converted synthetic Igor code must retain the real OS-isolated commit path."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PySide6.QtCore import Qt
from tests.tools.test_igor_virtual_workflows import (
    MAGNETIC_SOURCE,
    MAP_SOURCE,
    PROFILE_SOURCE,
    VIRTUAL_FILE_SOURCE,
    resource,
)

from materials_data_studio.tools.graph_document import load_graph_document, save_graph_document
from materials_data_studio.tools.graph_waves import empty_document
from materials_data_studio.tools.graph_window import GraphEditorWindow
from materials_data_studio.tools.igor_input_resources import write_input_resources
from materials_data_studio.tools.igor_procedure_conversion import analyze_procedures, build_python
from materials_data_studio.tools.procedure_policy import validate
from materials_data_studio.tools.procedure_sandbox import launch
from materials_data_studio.tools.procedure_worker import run

WINDOWS = pytest.mark.skipif(sys.platform != "win32", reason="Windows AppContainer integration")
SYNTHETIC_IGOR = """Function ScaleConverted(w)
    Wave w
    w*=2
End
Function CreateConverted()
    Make/D/N=8 convertedX,convertedY
    convertedX=p
    convertedY=p+1.5
    ScaleConverted(convertedY)
    Display/N=ConvertedGraph convertedY vs convertedX
    ModifyGraph lsize(convertedY)=2,rgb(convertedY)=(12000,24000,48000),tick=2,mirror=1
End
"""


def converted() -> str:
    report = analyze_procedures(SYNTHETIC_IGOR)
    assert report.routines[0].supported, report.routines[0].diagnostics
    source = build_python(report, "CreateConverted", [])
    validate(source)
    return source


@WINDOWS
def test_converted_procedure_applies_one_undoable_isolated_result(qtbot: Any) -> None:
    document = empty_document()
    window = GraphEditorWindow()
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    qtbot.addWidget(window)
    window.bind_graph_document(document)
    command = window.command_workspace
    source = converted() + '\nfrom mds import command\ncommand("ModifyGraph/W=ConvertedGraph marker(convertedY)=8")\n'
    try:
        # Conversion itself cannot create waves or graphs or start a worker.
        assert not document.render_source().waves and not command.is_running()
        command.start(source)
        qtbot.waitUntil(lambda: command.process is None, timeout=90000)
        output = command.output.toPlainText()
        assert "Windows AppContainer active" in output, output
        assert "Python procedure completed." in output, output
        x, y = document.render_source().wave("convertedX"), document.render_source().wave("convertedY")
        assert x is not None and y is not None
        np.testing.assert_array_equal(x.data, np.arange(8.))
        np.testing.assert_array_equal(y.data, 2 * np.arange(8.) + 3)
        graph = document.graph("ConvertedGraph")
        assert graph.traces[0].x_wave == "root:convertedX"
        assert graph.traces[0].line_width == 2 and graph.traces[0].marker == 8
        assert graph.traces[0].color_igor == (12000, 24000, 48000)
        assert graph.style.axis_defaults.tick == 2 and graph.style.axis_defaults.mirror == 1
        assert window.editor is not None and window.editor.history.count() == 1
        window.editor.history.undo()
        assert document.render_source().wave("convertedY") is None and not document.current.graphs
        window.editor.history.redo()
        assert document.render_source().wave("convertedY") is not None
    finally:
        if command.process is not None:
            command.stop()
            qtbot.waitUntil(lambda: command.process is None, timeout=10000)
        window._discard_approved = True
        window.close()


@WINDOWS
@pytest.mark.parametrize("outcome", ["failure", "stop"])
def test_converted_failed_or_stopped_worker_never_applies_partial_edits(qtbot: Any, outcome: str) -> None:
    document = empty_document()
    window = GraphEditorWindow()
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    qtbot.addWidget(window)
    window.bind_graph_document(document)
    command = window.command_workspace
    before = document.current
    suffix = ('raise RuntimeError("synthetic converted failure")\n' if outcome == "failure" else
              'print("converted worker ready for Stop",flush=True)\nwhile True:\n    pass\n')
    try:
        command.start(converted() + 'print("converted edits staged in worker",flush=True)\n' + suffix)
        if outcome == "stop":
            qtbot.waitUntil(lambda: "converted worker ready for Stop" in command.output.toPlainText(), timeout=90000)
            command.stop()
        qtbot.waitUntil(lambda: command.process is None, timeout=90000)
        output = command.output.toPlainText()
        assert "discarded" in output, output
        assert "converted edits staged in worker" in output, output
        assert document.current is before
        assert not document.render_source().waves and not document.current.graphs
        assert window.editor is not None and window.editor.history.count() == 0
    finally:
        if command.process is not None:
            command.stop()
            qtbot.waitUntil(lambda: command.process is None, timeout=10000)
        window._discard_approved = True
        window.close()


@WINDOWS
def test_public_command_does_not_grant_converted_code_host_dispatch(tmp_path: Path) -> None:
    (tmp_path / "input").mkdir()
    (tmp_path / "output").mkdir()
    document = tmp_path / "input/input.mdsg"
    source = tmp_path / "input/procedure.py"
    output = tmp_path / "output/output.mdsg"
    save_graph_document(document, empty_document())
    before = hashlib.sha256(document.read_bytes()).hexdigest()
    source.write_text(converted() + '\nfrom mds import command\ncommand(\'Execute "not permitted"\')\n', encoding="utf-8")
    assert launch(tmp_path) == 1
    assert not output.exists()
    assert hashlib.sha256(document.read_bytes()).hexdigest() == before
    assert "Unsupported document command: execute" in (tmp_path / "output/procedure.log").read_text(encoding="utf-8")
    # Existing worker preflight has already checked native host-file/network
    # denial before allowing even the supported converted document operations.
    assert (tmp_path / "input/permit").exists()
    assert not (tmp_path / "sandbox.json").exists()


def test_converted_worker_refuses_unconfined_direct_execution(tmp_path: Path) -> None:
    (tmp_path / "input").mkdir()
    (tmp_path / "output").mkdir()
    document, source = tmp_path / "input/input.mdsg", tmp_path / "input/procedure.py"
    output = tmp_path / "output/output.mdsg"
    save_graph_document(document, empty_document())
    source.write_text(converted(), encoding="utf-8")
    assert run(document, source, output) == 1
    assert not output.exists()


@WINDOWS
@pytest.mark.parametrize("family", ["map", "magnetic", "profile"])
def test_prepared_scientific_workflow_uses_actual_appcontainer_and_fixed_sidecar(tmp_path: Path, family: str) -> None:
    (tmp_path / "input").mkdir()
    (tmp_path / "output").mkdir()
    input_path, source_path = tmp_path / "input/input.mdsg", tmp_path / "input/procedure.py"
    output_path = tmp_path / "output/output.mdsg"
    if family == "map":
        source, entry = MAP_SOURCE, "CreateMap"
        inputs = [resource("scan_1.txt", "Synthetic map input\n0 2\n1 4\n2 6\n"),
                  resource("scan_2.txt", "Synthetic map input\n0 3\n1 6\n2 9\n")]
    elif family == "magnetic":
        source = VIRTUAL_FILE_SOURCE + "\n" + MAGNETIC_SOURCE + '\nFunction Workflow()\nImportPrepared()\nAnalyzeMagnetic(8,120)\nEnd'
        entry = "Workflow"
        inputs = [resource("measurement.txt", "Synthetic magnetic input\r\n[Data]\r\nTemperature,Field,Moment\r\n10,100,1\r\n20,100,-2\r\n30,-100,3\r\n20,-100,-4\r\n10,100,5\r\n")]
    else:
        source, entry = PROFILE_SOURCE, "Profile"
        inputs = [resource("profile.txt", "Synthetic profile input\r\n1 2 3\r\n[Curve]\r\nangle observed calculated\r\n0 8 7\r\n1 11 9\r\n2 6 8\r\n")]
    report = analyze_procedures(source)
    code = build_python(report, entry, [])
    validate(code)
    save_graph_document(input_path, empty_document())
    sidecar = tmp_path / "input/igor_inputs.json"
    write_input_resources(sidecar, inputs)
    original_sha = hashlib.sha256(input_path.read_bytes()).hexdigest()
    sidecar_sha = hashlib.sha256(sidecar.read_bytes()).hexdigest()
    source_path.write_text(code, encoding="utf-8")
    result = launch(tmp_path)
    log = (tmp_path / "output/procedure.log").read_text(encoding="utf-8")
    assert result == 0, log
    document = load_graph_document(output_path)
    assert "igor_input_resources" not in document.current.environment
    assert hashlib.sha256(input_path.read_bytes()).hexdigest() == original_sha
    assert hashlib.sha256(sidecar.read_bytes()).hexdigest() == sidecar_sha
    assert (tmp_path / "input/permit").exists()  # native host-file/network denial preflight completed
    assert not (tmp_path / "sandbox.json").exists()
    assert "Traceback" not in log
    if family == "map":
        wave = document.render_source().wave("map")
        assert wave is not None
        np.testing.assert_allclose(wave.data, [[2, 3], [4, 6], [6, 9]])
        assert wave.dimension_scaling == ((0., 1.), (1., 1.))
        assert document.graph("MapGraph").images[0].reverse
    elif family == "magnetic":
        wave = document.render_source().wave("susceptibility")
        assert wave is not None
        np.testing.assert_allclose(wave.data, [150, -300, -450, 600, 750])
        assert len(document.graph("Susceptibility").traces) == 2
    else:
        wave = document.render_source().wave("residual")
        assert wave is not None
        np.testing.assert_allclose(wave.data, [1, 2, -2])
        assert document.graph("ProfileGraph").traces[-1].name == "observed#1"
