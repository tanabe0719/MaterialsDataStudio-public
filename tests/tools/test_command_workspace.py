"""Synthetic command/Python/Undo/storage acceptance tests. No research data."""
from __future__ import annotations

import json
import sys
import zipfile
from dataclasses import replace

import numpy as np
import pytest
from PySide6.QtCore import Qt

from materials_data_studio.tools.command_engine import CommandEngine
from materials_data_studio.tools.command_language import evaluate, expression, parse, split
from materials_data_studio.tools.command_services import DocumentServices
from materials_data_studio.tools.graph_document import (
    EditableGraphDocument,
    load_graph_document,
    save_graph_document,
)
from materials_data_studio.tools.graph_window import GraphEditorWindow
from materials_data_studio.tools.procedure_api import install_api
from materials_data_studio.tools.procedure_sandbox import launch
from materials_data_studio.tools.procedure_worker import run
from materials_data_studio.tools.pxp_model import PxpDocument


@pytest.fixture
def engine():
    source = PxpDocument("generated", "synthetic.pxp", "", 0, "10", "synthetic", "", status="FULL")
    return CommandEngine(DocumentServices(EditableGraphDocument.from_pxp(source)))


def ok(engine, source):
    result = engine.execute(source)
    assert result.success, result.message
    return result


def discard_test_window(window):
    """Assertion failures must not hide their traceback behind a modal close prompt."""
    command = window.command_workspace
    process = command.process
    if process is not None:
        command.stop()
        process.waitForFinished(5000)
    window._discard_approved = True


def test_numeric_expressions_and_lexer(engine):
    ok(engine, 'Variable a=2,b=3; String text="a;b // not a comment"; Make/D/N=5 y; y=a*p+b; Print sqrt(9),text')
    np.testing.assert_array_equal(engine.services.wave("y").data, [3, 5, 7, 9, 11])
    assert split('Print "a;b"; // ignore\nPrint 2', ";") == ['Print "a;b"', 'Print 2']
    assert evaluate(expression("2^3+floor(2.5)"), lambda _: None) == 10
    assert len(parse('Make/N=(2,3) map; ModifyGraph rgb(y)=(1,2,3)')) == 2
    before = engine.services.document.current
    ok(engine, "Print y")
    assert engine.services.document.current == before
    ok(engine, "WaveStats y; Print V_avg")
    assert engine.services.resolve("V_avg") == 7
    ok(engine, 'Make/N=3 single; single=p*0.1; text=text+"!"')
    assert engine.services.wave("single").data.dtype == np.float32


def test_typed_python_serializer(engine, monkeypatch):
    from materials_data_studio.tools.command_python import to_python
    monkeypatch.setitem(sys.modules, "mds", None)
    install_api(engine.services)
    source = to_python('Make/D/N=8 y; y=sin(p/4); Display/N=G y; Label left "Signal"; SetAxis bottom 0,7', engine.services)
    assert "exec(" not in source and "execute(" not in source
    exec(source, {})
    assert engine.services.graph("G").traces[0].y_wave == "root:y"
    np.testing.assert_allclose(engine.services.wave("y").data, np.sin(np.arange(8)/4))
    with pytest.raises(NotImplementedError):
        to_python("Execute bad", engine.services)


@pytest.mark.parametrize("code", ["Print __import__('os')", "Print y.__class__", "Print [v for v in y]", "Print (lambda:1)()", "Make/N=3 z; Execute \"danger\"", "Make/N=-2 x", "Make/N=(1,2,3,4,5) x", "Make/UNSUPPORTED x", "Variable a=Inf", 'String a=3', 'Variable a="bad"'])
def test_reject_and_atomic_rollback(engine, code):
    ok(engine, "Make/N=4 y")
    before = engine.services.document.current
    context = engine.services.context.copy()
    result = engine.execute(code)
    assert not result.success
    assert engine.services.document.current is before
    assert engine.services.context == context


def test_folders_and_liberal_names(engine):
    ok(engine, "NewDataFolder/S root:scan; Make/D/N=3 'sample signal'; 'sample signal'=p; SetDataFolder root:; Make/N=3 y; y=root:scan:'sample signal'+1")
    np.testing.assert_array_equal(engine.services.wave("root:y").data, [1, 2, 3])
    assert engine.services.wave("root:scan:sample signal").igor_path == "root:scan:sample signal"
    assert not engine.execute("KillDataFolder root:scan").success
    ok(engine, "NewDataFolder root:empty; KillDataFolder root:empty")
    assert "root:empty" not in engine.services.document.render_source().folders


@pytest.mark.parametrize("code", ['String s=y', 'Variable v=y', 'Variable y=2', 'String s="text"; s=3', 'Variable v=2; Make/N=3 v', 'Print (2**1024)**1024', 'Make/N=4 z; Redimension/N=2.5 z', 'Make/N=3 z; InsertPoints 0.5,2,z', 'Histogram/B=1000000000 y,hist'])
def test_typed_scalar_and_resource_boundaries(engine, code):
    ok(engine, "Make/D/N=4 y")
    before = engine.services.document.current
    assert not engine.execute(code).success
    assert engine.services.document.current is before


def test_wave_operations_and_identity(engine):
    ok(engine, 'Make/D/N=6 y,x; y=p*2; x=p; SetScale/P x,2,0.5,"K",y; SetScale d,0,1,"emu",y; Duplicate y,z; Rename z,copy')
    s = engine.services
    assert s.wave("y").id != s.wave("copy").id
    assert s.wave("copy").dimension_scaling == ((2, .5),)
    assert s.wave("copy").data_units == "emu"
    ok(engine, "Extract y,positive,y>4")
    np.testing.assert_array_equal(s.wave("positive").data, [6, 8, 10])
    assert len({w.id for w in s.document.render_source().waves}) == 4
    ok(engine, "Concatenate/NP {y,copy},joined")
    assert s.wave("joined").shape == (12,)
    assert s.wave("joined").data_units == "emu"
    ok(engine, "Redimension/N=4 joined; InsertPoints 1,2,joined; DeletePoints 1,2,joined")
    assert s.wave("joined").shape == (4,)
    ok(engine, "x=5-p; Sort x,x,y; KillWaves copy,joined")
    np.testing.assert_array_equal(s.wave("y").data, [10, 8, 6, 4, 2, 0])


def test_graph_styles_annotations_errors_and_tables(engine, tmp_path):
    ok(engine, 'Make/D/N=8 y,x,err; x=p; y=p^2; err=0.1; Display/N=G y vs x; Label left "Signal"; SetAxis bottom 0,7; ModifyGraph lsize(y)=2,marker(y)=8,rgb(y)=(65535,0,0),mirror=1,tick=2; ErrorBars y X,wave=(err,err); TextBox/N=note "hello"; AppendText " world"; Tag/N=tag y,2,"linked"; Legend; Edit/N=T x,y; AppendToTable/W=T err; RemoveFromTable/W=T err')
    graph = engine.services.graph("G")
    assert graph.traces[0].line_width == 2
    assert graph.traces[0].x_error_low == "root:err"
    assert next(a for a in graph.annotations if a.name == "note").raw == "hello world"
    assert next(a for a in graph.annotations if a.name == "tag").point == 2
    assert engine.services.document.render_source().table_definitions[0].columns == ("root:x", "root:y")
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    from materials_data_studio.tools.graph_render import IgorMatplotlibRenderer
    figure = Figure()
    FigureCanvasAgg(figure)
    IgorMatplotlibRenderer().render(figure, engine.services.document, "G")
    figure.canvas.draw()
    target = tmp_path / "state.mdsg"
    save_graph_document(target, engine.services.document)
    loaded = load_graph_document(target)
    assert loaded.graph("G") == graph
    ok(engine, "RenameWindow G,Renamed; DoWindow/F Renamed; SetAxis/A bottom; RemoveFromGraph y; KillWindow T")
    assert not engine.services.document.render_source().tables
    assert not engine.services.graph().traces


def test_igor_rich_text_and_completion_tokens(qtbot, engine):
    ok(engine, r'Make/N=5 y; Display/N=G y; Label left "\f02I\f00"; TextBox/N=note "\Z16first\rsecond"')
    assert engine.services.graph().axes[0].label_raw == r"\f02I\f00"
    assert engine.services.graph().annotations[0].raw == "\\Z16first\rsecond"
    window = GraphEditorWindow()
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    qtbot.addWidget(window, before_close_func=discard_test_window)
    window.bind_graph_document(engine.services.document)
    command = window.command_workspace
    command.input.setText("Print 1; App")
    assert command.completer.splitPath(command.input.text()) == ["App"]
    assert "AppendToGraph" in command.hint.text()
    assert "root:y" in command.completer.model().stringList()
    window._discard_approved = True
    window.close()


def test_image_free_axes_drawings_reordering(engine):
    ok(engine, 'Make/D/N=(4,3) map; Make/D/N=4 a,b,c; Display/N=G a,b,c; ReorderTraces a,{c}; NewFreeAxis/R rightExtra; ModifyFreeAxis freePos(rightExtra)={0.8,kwFraction}; AppendToGraph/R=rightExtra b; AppendImage map; ModifyImage map ctab={0,1,Grays,0}; ColorScale image=map; AppendText "Intensity"; SetDrawEnv linefgc=(65535,0,0),linethick=2; DrawLine 0.1,0.1,0.9,0.9; DrawRect 0,0,0.2,0.2; DrawText 0.5,0.5,"note"')
    graph = engine.services.graph()
    assert graph.traces[0].name == "c"
    assert graph.images[0].colorbar_label_raw == "Intensity"
    assert graph.drawings[0].line_width == 2
    ok(engine, "RemoveImage map; RemoveFromGraph b#1; KillFreeAxis rightExtra")
    assert not engine.services.graph().images


def test_graph_table_active_context_and_deletion(engine):
    ok(engine, "Make/N=3 y; Display/N=G y; Edit/N=T y; DoWindow/F T; Display/N=New y")
    assert engine.services.context["selected_kind"] == "graph"
    assert engine.services.context["graph"] == "New"
    ok(engine, "KillWindow New")
    assert "graph" not in engine.services.context


@pytest.mark.parametrize("command,result", [("Differentiate y", "y_differentiate"), ("Integrate y", "y_integrate"), ("Smooth 3,y", "y"), ("Smooth/S=2 5,y", "y"), ("SmoothDerived 3,y", "y_smooth"), ("HistogramDerived/B=4 y,hist", "hist"), ("FFTDerived y", "y_fft"), ("CurveFitDerived line,y,x", "y_curvefit"), ("FFT/DEST=spectrum y", "spectrum"), ("CurveFit line,y,x", "W_coef")])
def test_analysis(engine, command, result):
    ok(engine, 'Make/D/N=20 y,x; x=p; y=2*p+3; SetScale/P x,0,1,"s",y')
    ok(engine, command)
    assert engine.services.wave(result).data.size > 0
    if command.startswith("Differentiate"):
        np.testing.assert_allclose(engine.services.wave(result).data, 2)
    if command.startswith("Integrate"):
        np.testing.assert_allclose(engine.services.wave(result).data, np.arange(20)**2 + 3*np.arange(20))
    assert ok(engine, "FindPeak y; FindLevel y,10").message


def test_python_public_api_and_transactions(engine, monkeypatch):
    monkeypatch.setitem(sys.modules, "mds", None)
    mds = install_api(engine.services)
    mds.folder.create("root:scan")
    mds.folder.set("root:scan")
    t = mds.wave.create("Temperature", np.arange(1., 7.), units="K")
    m = mds.wave.create("Magnetization", t.values**2, x=t.values, units="emu")
    d = mds.wave.create("dMdT", np.gradient(m.values, t.values))
    g = mds.graph.new("Derivative")
    trace = g.append(d, t)
    trace.color = "#2361ab"
    trace.line_width = 2
    g.axis("left").label = "dM/dT"
    g.axis("bottom").set_range(1, 6)
    assert not m.values.flags.writeable
    assert m.shape == (6,) and m.scaling == ((1., 1.),)
    before = engine.services.document.current.clone()
    with pytest.raises(ValueError, match="abort"), mds.transaction():
        m.values = m.values + 1
        raise ValueError("abort")
    assert engine.services.document.current == before
    d.name = "derivative"
    assert "root:scan:derivative" in mds.wave.names()
    assert mds.graph.active().trace("dMdT").line_width == 2


def test_worker_explicit_run_and_failure(engine, tmp_path):
    ok(engine, "Make/D/N=4 y; y=p")
    (tmp_path / "input").mkdir()
    (tmp_path / "output").mkdir()
    source, code, output = tmp_path / "input/input.mdsg", tmp_path / "input/procedure.py", tmp_path / "output/output.mdsg"
    save_graph_document(source, engine.services.document)
    code.write_text('from mds import wave,graph\nwave.create("double",wave("y").values*2)\ngraph.new("Result").append("double")\nprint("done")', encoding="utf-8")
    assert launch(tmp_path) == 0
    assert load_graph_document(output).graph("Result").traces[0].y_wave == "root:double"
    code.write_text('from mds import wave\nwave.create("never",[1,2])\nraise RuntimeError("expected failure")', encoding="utf-8")
    output.unlink()
    (source.parent / "permit").unlink()
    (output.parent / "ready").unlink()
    assert launch(tmp_path) == 1
    assert not output.exists()
    # Direct execution outside an OS container must fail closed.
    assert run(source, code, output) == 1


def test_mdsg_environment_and_old_compatibility(engine, tmp_path):
    ok(engine, 'Variable number=42; String text="preserved"; NewDataFolder root:empty')
    engine.services.environment["python_source"] = 'raise RuntimeError("never execute on load")'
    path = tmp_path / "state.mdsg"
    save_graph_document(path, engine.services.document)
    assert load_graph_document(path).current.environment == engine.services.environment
    with zipfile.ZipFile(path) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(entries["manifest.json"])
    manifest["format_version"] = 3
    entries["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(tmp_path / "old.mdsg", "w") as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    assert load_graph_document(tmp_path / "old.mdsg").current.environment == engine.services.environment


def test_ui_completion_local_undo_and_imported_source(qtbot, engine, tmp_path):
    source = replace(engine.services.document.source, command_history=("Make/D imported",), procedures=("Function Dangerous()\r Execute bad\rEnd",))
    window = GraphEditorWindow()
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    qtbot.addWidget(window, before_close_func=discard_test_window)
    window.bind_document(source)
    command = window.command_workspace
    assert command.tabs.widget(1).isReadOnly()
    assert "Imported Igor history" in command.output.toPlainText()
    assert not window.editor.document.render_source().waves
    command.input.setText("App")
    assert "AppendToGraph" in command.hint.text()
    command.input.clear()
    qtbot.keyClicks(command.input, "Make/D/N=4 y; y=p; Display/N=G y")
    qtbot.keyClick(command.input, Qt.Key.Key_Return)
    assert window.editor.history.count() == 1
    assert window.editor.document.graph("G").traces
    command.execute("Print y")
    assert window.editor.history.count() == 1
    command.execute("Undo")
    assert not window.editor.document.render_source().waves
    command.execute("Redo")
    command.code.setPlainText('print("only on Run")')
    command.flush()
    assert window._save(tmp_path / "ui.mdsg")
    assert load_graph_document(tmp_path / "ui.mdsg").current.environment["python_source"] == 'print("only on Run")'
    window._discard_approved = True
    window.close()


def test_ui_python_worker_commit_and_stop(qtbot, engine, monkeypatch):
    monkeypatch.setenv("PYTHONUNBUFFERED", "1")
    window = GraphEditorWindow()
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    qtbot.addWidget(window, before_close_func=discard_test_window)
    window.bind_graph_document(engine.services.document)
    command = window.command_workspace
    command.trusted = True
    command.start('from mds import wave\nwave.create("created",[1,2,3])\nprint("success")')
    qtbot.waitUntil(lambda: command.process is None, timeout=30000)
    assert window.editor.document.render_source().wave("created") is not None, command.output.toPlainText()
    assert window.editor.history.count() == 1
    window.editor.history.undo()
    assert window.editor.document.render_source().wave("created") is None
    command.start('print("waiting",flush=True)\nwhile True:\n    pass')
    qtbot.waitUntil(lambda: "waiting" in command.output.toPlainText(), timeout=30000)
    command.stop()
    qtbot.waitUntil(lambda: command.process is None, timeout=10000)
    assert "Stopped" in command.output.toPlainText()
    window._discard_approved = True
    window.close()


@pytest.mark.parametrize("failure", ["exception", "stale"])
def test_worker_exception_and_concurrent_edits_are_not_committed(qtbot, engine, failure):
    window = GraphEditorWindow()
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    qtbot.addWidget(window, before_close_func=discard_test_window)
    window.bind_graph_document(engine.services.document)
    command = window.command_workspace
    command.trusted = True
    if failure == "exception":
        command.start('from mds import wave\nwave.create("discarded",[1.,2.])\nraise RuntimeError("intentional worker failure")')
    else:
        command.start('from mds import wave\nprint("worker ready",flush=True)\nresult=sum(range(25000000))\nwave.create("discarded",[1.,2.])')
        qtbot.waitUntil(lambda: "worker ready" in command.output.toPlainText(), timeout=30000)
        command.execute("Make/D/N=3 keepGUI")
    qtbot.waitUntil(lambda: command.process is None, timeout=30000)
    assert window.editor.document.render_source().wave("discarded") is None
    assert window.editor.history.count() == (0 if failure == "exception" else 1)
    assert "intentional worker failure" in command.output.toPlainText() if failure == "exception" else "stale worker results discarded" in command.output.toPlainText()
    window._discard_approved = True
    window.close()


def test_pxp_history_procedure_records_are_not_executed(monkeypatch, tmp_path):
    from igor2 import packed
    from igor2.record import GetHistoryRecord, HistoryRecord, ProcedureRecord, RecreationRecord

    from materials_data_studio.tools.pxp_backend import Igor2Backend
    records = [HistoryRecord({}, b'Make/N=5 dangerous\rPrint 7'), GetHistoryRecord({}, b'Print 8'), ProcedureRecord({}, b'Function Dangerous()\rExecute bad\rEnd'), RecreationRecord({}, b'Window G() : Graph\rDisplay\rEndMacro')]
    monkeypatch.setattr(packed, "load", lambda _: (records, {}))
    path = tmp_path / "synthetic.pxp"
    path.write_bytes(b"Generated record input")
    source = Igor2Backend().open(path)
    assert source.command_history == ("Make/N=5 dangerous", "Print 7", "Print 8")
    assert "Function Dangerous()" in source.procedures[0]
    assert not source.waves
    saved = tmp_path / "imported.mdsg"
    save_graph_document(saved, EditableGraphDocument.from_pxp(source))
    restored = load_graph_document(saved)
    assert restored.source.command_history == source.command_history
    assert restored.source.procedures == source.procedures
