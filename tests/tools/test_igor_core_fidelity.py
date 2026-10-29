"""Igor 10.03 synthetic golden contracts and cross-layer editor regressions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Qt

from materials_data_studio.tools.command_engine import CommandEngine
from materials_data_studio.tools.command_services import DocumentServices
from materials_data_studio.tools.graph_document import load_graph_document, save_graph_document
from materials_data_studio.tools.graph_fidelity import assess_graph
from materials_data_studio.tools.graph_h5xp import load_h5xp, save_h5xp
from materials_data_studio.tools.graph_history import GraphEditorController
from materials_data_studio.tools.graph_render import IgorMatplotlibRenderer
from materials_data_studio.tools.graph_waves import empty_document
from materials_data_studio.tools.pxp_recreation import parse_recreation
from materials_data_studio.tools.pxp_snapshot import decode_document, encode_document

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / "tests/reference_igor/v10_03_core.json"
GOLDEN = json.loads(REFERENCE.read_text(encoding="utf-8"))


@pytest.fixture
def engine():
    e = CommandEngine(DocumentServices(empty_document()))
    e.services.create_wave("y", np.array([0, 2, 4, 6, 8], dtype=np.float64))
    e.services.create_wave("x", np.linspace(0, 10, 5))
    e.services.create_wave("map", np.arange(1.0, 13.0).reshape(3, 4))
    return e


def ok(engine, code):
    result = engine.execute(code)
    assert result.success, result.message
    return result


def render(document, name):
    fig = Figure(figsize=(420 / 72, 300 / 72), dpi=GOLDEN["dpi"])
    FigureCanvasAgg(fig)
    mapping = IgorMatplotlibRenderer().render(fig, document, name)
    fig.canvas.draw()
    return fig, mapping


@pytest.mark.parametrize("case", ["smooth_binomial_3", "findlevel", "wavestats"])
def test_native_numeric_golden(engine, case):
    reference = GOLDEN["cases"][case]
    original = engine.services.wave("y")
    ok(engine, reference["commands"])
    if case.startswith("smooth"):
        wave = engine.services.wave("y")
        np.testing.assert_allclose(wave.data, reference["expected"], rtol=0, atol=1e-12)
        assert wave.id == original.id and wave.dtype == original.dtype
        np.testing.assert_array_equal(original.data, reference["input"])
        assert len(engine.services.document.render_source().waves) == 3
    else:
        for variable, value in reference["expected"].items():
            assert engine.services.resolve(variable) == pytest.approx(value)
        assert engine.services.wave("y") is original


@pytest.mark.parametrize("swap", [False, True])
def test_native_top_axis_all_layers(engine, tmp_path, swap):
    ok(engine, GOLDEN["cases"]["top_axis"]["commands"])
    if swap:
        ok(engine, "ModifyGraph swapXY=1")
    doc = engine.services.document
    fig, mapping = render(doc, "AuditTop")
    ax = fig.axes[0]
    component = ax.yaxis if swap else ax.xaxis
    assert component.get_label_position() == ("right" if swap else "top")
    assert component.label.get_text() == "Top X"
    box = component.label.get_window_extent(fig.canvas.get_renderer())
    assert box.x0 >= 0 and box.y1 <= fig.bbox.height
    assert mapping[component.label] == ("label", "top")
    assert (ax.get_ylim() if swap else ax.get_xlim()) == (0, 10)
    path = tmp_path / "top.mdsg"
    save_graph_document(path, doc)
    restored = load_graph_document(path)
    assert restored.graph("AuditTop").traces[0].x_axis == "top"
    target = tmp_path / "top.h5xp"
    save_h5xp(target, doc)
    assert load_h5xp(target).graph("AuditTop").traces[0].x_axis == "top"
    # Inspect native recreation too, not only MDS metadata roundtrip.
    import h5py

    with h5py.File(target) as h:
        raw = h["Recreation"]["Recreation Procedures"][()]
    raw = raw.decode() if isinstance(raw, bytes) else str(raw)
    graphs, _ = parse_recreation(raw)
    assert graphs[0].traces[0].x_axis == "top"
    svg = tmp_path / "top.svg"
    fig.savefig(svg)
    assert svg.stat().st_size > 100


def test_independent_top_bottom_ranges_shared_y(engine):
    ok(
        engine,
        'Display/N=G y vs x; AppendToGraph/T y vs x; SetAxis bottom 0,10; SetAxis top 0,100; Label top "Energy"',
    )
    fig, _ = render(engine.services.document, "G")
    axes = {a._mds_axis_names: a for a in fig.axes}
    assert axes[("bottom", "left")].get_xlim() == (0, 10)
    assert axes[("top", "left")].get_xlim() == (0, 100)
    assert axes[("top", "left")].yaxis.get_visible() is False
    assert axes[("top", "left")].get_shared_y_axes().joined(*fig.axes)


@pytest.mark.parametrize("swap", [False, True])
def test_mixed_trace_image_error_annotation_roundtrip(engine, tmp_path, swap):
    ok(engine, GOLDEN["cases"]["mixed"]["commands"])
    ok(engine, 'ErrorBars y Y,wave=(x,x); TextBox/N=note "Mixed layers"')
    if swap:
        ok(engine, "ModifyGraph swapXY=1")
    doc = engine.services.document
    fig, _ = render(doc, "AuditMixed")
    ax = fig.axes[0]
    assert len(ax.images) == 1
    assert len(ax.containers) == 1
    line = next(line for line in ax.lines if line.get_gid() == "trace:y")
    np.testing.assert_array_equal(line.get_xdata(), engine.services.wave("y" if swap else "x").data)
    np.testing.assert_array_equal(line.get_ydata(), engine.services.wave("x" if swap else "y").data)
    assert any(a.get_gid() == "annotation:note" for a in ax.artists)
    np.testing.assert_array_equal(
        ax.images[0].get_array(),
        engine.services.wave("map").data if swap else engine.services.wave("map").data.T,
    )
    path = tmp_path / "mixed.mdsg"
    save_graph_document(path, doc)
    restored, _ = render(load_graph_document(path), "AuditMixed")
    assert len(restored.axes[0].images) == 1
    assert any(line.get_gid() == "trace:y" for line in restored.axes[0].lines)
    fig.savefig(tmp_path / "mixed.svg")
    fig.savefig(tmp_path / "mixed.png")


def test_parser_top_free_x_and_legacy_migration(engine):
    graphs, _ = parse_recreation(
        'Window G() : Graph\nDisplay/T=energy y vs x\nLabel energy "E"\nEndMacro'
    )
    graph = graphs[0]
    assert graph.traces[0].x_axis == "energy"
    assert next(a.side for a in graph.axes if a.name == "energy") == "top"
    source = replace(engine.services.document.render_source(), graphs=graphs)
    metadata, payload = encode_document(source)
    metadata["pxp_viewer_schema_version"] = 11
    for g in metadata["graphs"]:
        for t in g["traces"]:
            t.pop("x_axis")
    restored = decode_document(metadata, payload)
    assert restored.graphs[0].traces[0].x_axis == "bottom"


def test_original_native_top_recreation_with_native_synthetic_waves(engine):
    waves = json.loads(
        (ROOT / "docs/igor_audit/evidence/mds/native_wave_reference.json").read_text(
            encoding="utf-8"
        )
    )
    for item in waves:
        if item["name"] in {"xData", "yData"}:
            engine.services.create_wave(item["name"], np.asarray(item["data"], dtype=item["dtype"]))
    raw = (ROOT / GOLDEN["cases"]["top_axis"]["native_evidence"]).read_text(encoding="utf-8")
    graphs, _ = parse_recreation(raw)
    engine.services.document.current.graphs = graphs
    fig, mapping = render(engine.services.document, "AuditTop")
    ax = fig.axes[0]
    assert ax.xaxis.get_label_position() == "top" and ax.get_xlim() == (0, 10)
    assert mapping[ax.xaxis.label] == ("label", "top")
    np.testing.assert_array_equal(ax.lines[0].get_xdata(), engine.services.wave("xData").data)


@pytest.mark.parametrize("side", ["T", "B"])
def test_command_free_x_axis(engine, side):
    ok(
        engine,
        f"Display/N=G y vs x; NewFreeAxis/{side} energy; AppendToGraph/{side}=energy y vs x; SetAxis energy 0,20; ModifyGraph freePos(energy)={{0.3,kwFraction}}",
    )
    fig, mapping = render(engine.services.document, "G")
    ax = next(a for a in fig.axes if a._mds_axis_names[0] == "energy")
    assert ax.get_xlim() == (0, 20)
    assert mapping[ax.xaxis.label] == ("label", "energy")
    assert ax.spines["top" if side == "T" else "bottom"].get_position()[0] == "axes"


def test_structured_diagnostics_preserve_unknown_property(engine, tmp_path):
    raw = "Window G() : Graph\nDisplay y vs x\nModifyGraph useComma(bottom)=1\nEndMacro"
    graphs, _ = parse_recreation(raw)
    report = assess_graph(graphs[0], engine.services.document.render_source())
    note = next(d for d in report.diagnostics if d.property == "useComma")
    assert note.layer == "parser" and note.code and "useComma" in note.raw
    assert report.rendered == "partial" and "Fully supported" not in report.summary
    doc = engine.services.document
    doc.current.graphs = graphs
    path = tmp_path / "notes.mdsg"
    save_graph_document(path, doc)
    restored = load_graph_document(path)
    assert "useComma" in restored.graph("G").recreation_raw
    assert assess_graph(restored.graph("G")).diagnostics[0].raw == note.raw


def test_normal_graph_has_no_spurious_diagnostic(engine):
    ok(engine, "Display/T/N=G y vs x")
    report = assess_graph(engine.services.graph(), engine.services.document.render_source())
    assert not report.diagnostics
    assert report.rendered == "supported subset"


def test_unrenderable_state_is_diagnosed_and_not_silently_exported(engine, tmp_path):
    from materials_data_studio.tools.graph_export import GraphicsSettings, save_graphics
    from materials_data_studio.tools.pxp_model import PxpGraph, PxpTrace

    graph = PxpGraph("Broken", traces=(PxpTrace("missing", "missing"),))
    engine.services.document.current.graphs = (graph,)
    report = engine.services.document.compatibility("Broken")
    assert report.rendered == report.exportable == "blocked"
    fig, mapping = render(engine.services.document, "Broken")
    assert not fig.axes and not mapping
    assert "cannot be rendered" in fig.texts[0].get_text()
    with pytest.raises(ValueError, match=r"igor\.trace\.unrenderable"):
        save_graphics(
            tmp_path / "broken.svg", engine.services.document, "Broken", GraphicsSettings()
        )
    assert not (tmp_path / "broken.svg").exists()
    # Rich source text is literal in GUI tooltips, never HTML/resources.
    graph = replace(graph, recreation_raw='<img src="file:///private">')
    assert "<img" not in assess_graph(graph).tooltip


def test_partial_graph_keeps_readable_trace_with_explicit_diagnostic(engine):
    from materials_data_studio.tools.pxp_model import PxpTrace
    ok(engine,'Display/N=Partial y vs x')
    graph = engine.services.graph()
    graph = replace(graph,traces=(*graph.traces,PxpTrace('missing','no_data')))
    engine.services.document.current.graphs = (graph,)
    report = engine.services.document.compatibility('Partial')
    assert report.rendered=='partial' and report.exportable=='blocked'
    assert any(d.code=='igor.trace.unrenderable' for d in report.diagnostics)
    fig,_ = render(engine.services.document,'Partial')
    assert len(fig.axes[0].lines)==1


def test_output_monitor_ignores_disappearing_locks_but_not_access_errors(tmp_path,monkeypatch):
    from materials_data_studio.tools.procedure_sandbox import output_bytes
    (tmp_path/'result').write_bytes(b'12345')
    (tmp_path/'font.lock').write_bytes(b'temporary')
    original = Path.stat
    def disappearing(path,*args,**kwargs):
        if path.name=='font.lock':
            raise FileNotFoundError(path)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'stat',disappearing)
    assert output_bytes(tmp_path)==5
    def inaccessible(path,*args,**kwargs):
        if path.name=='font.lock':
            raise PermissionError(path)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'stat',inaccessible)
    with pytest.raises(PermissionError):
        output_bytes(tmp_path)


def test_reference_evidence_manifest():
    assert (
        hashlib.sha256(
            np.array(GOLDEN["cases"]["findlevel"]["input"], dtype="<f8").tobytes()
        ).hexdigest()
        == GOLDEN["numeric_input_sha256"]
    )
    for case in GOLDEN["cases"].values():
        path = ROOT / case["native_evidence"]
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == case["evidence_sha256"]


@pytest.mark.parametrize(
    "end,expected",
    [
        (0, [1, 2, 4, 6, 7]),
        (1, [2.5, 2, 4, 6, 5.5]),
        (2, [0.5, 2, 4, 6, 5.5]),
        (3, [0.5, 2, 4, 6, 7.5]),
    ],
)
def test_smooth_end_effects(engine, end, expected):
    ok(engine, f"Smooth/E={end} 1,y")
    np.testing.assert_allclose(engine.services.wave("y").data, expected)


def test_smooth_metadata_dtype_source_and_undo(engine, qtbot, tmp_path):
    from materials_data_studio.tools.graph_waves import make_wave

    e = engine
    original = e.services.wave("y")
    original = replace(
        original, dimension_scaling=((10.0, -0.5),), dimension_units=("s",), data_units="emu"
    )
    e.services.write([original], overwrite=True)
    original = e.services.wave("y")
    before = e.services.document.current.clone()
    controller = GraphEditorController(e.services.document)
    ok(e, "Smooth 3,y; WaveStats/Q y; Print V_avg")
    after = e.services.document.current
    controller.commit(before, after, "Command: Smooth and statistics")
    changed = e.services.wave("y")
    assert changed.dimension_scaling == original.dimension_scaling
    assert changed.data_units == "emu" and changed.dimension_units == ("s",)
    assert changed.id == original.id
    controller.history.undo()
    assert e.services.wave("y") is original
    with pytest.raises(ValueError):
        e.services.resolve("V_avg")
    controller.history.redo()
    assert e.services.resolve("V_avg") == 4
    path = tmp_path / "analysis.mdsg"
    controller.save(path)
    restored = CommandEngine(DocumentServices(load_graph_document(path)))
    assert restored.services.resolve("v_AVG") == 4
    assert not controller.dirty
    # A working float32 wave remains float32, not a silent float64 replacement.
    e.services.write(
        [make_wave("root:y", original.data.astype(np.float32), original)], overwrite=True
    )
    ok(e, "Smooth 3,y")
    assert e.services.wave("y").data.dtype == np.float32


@pytest.mark.parametrize(
    "code",
    [
        "Smooth/B/S=2 5,y",
        "Smooth/S=3 5,y",
        "Smooth/S=2 4,y",
        "Smooth/E=4 3,y",
        "Smooth/D=dest 3,y",
        "Smooth/S 3,y",
        "FindLevel/T=1 y,3",
        "FindLevel/EDGE=3 y,3",
        "WaveStats/W=1 y",
        "WaveStats/Q=2 y",
    ],
)
def test_unsupported_analysis_atomic(engine, code):
    before = engine.services.document.current
    result = engine.execute("Variable scratch=1; " + code)
    assert not result.success
    assert engine.services.document.current is before


def test_findlevel_scaled_reverse_direction_range_nan(engine, tmp_path):
    e = engine
    ok(e, 'SetScale/P x,10,-2,"s",y; FindLevel/Q y,3')
    assert e.services.resolve("V_LevelX") == 7
    ok(e, "FindLevel/Q/P/R=[4,0]/EDGE=1 y,3")
    assert e.services.resolve("V_LevelX") == 1.5
    assert e.services.resolve("V_rising") == 1
    e.services.write(
        [replace(e.services.wave("y"), data=np.array([0.0, 1, np.nan, np.nan, 4.0]))],
        overwrite=True,
    )
    ok(e, "FindLevel/Q/P y,2.5")
    assert e.services.resolve("V_LevelX") == pytest.approx(2.5)
    ok(e, "FindLevel/Q/EDGE=2 y,2.5")
    assert e.services.resolve("V_flag") == 1
    assert np.isnan(e.services.resolve("V_LevelX"))
    path = tmp_path / "notfound.mdsg"
    save_graph_document(path, e.services.document)
    restored = DocumentServices(load_graph_document(path))
    assert restored.resolve("V_flag") == 1 and np.isnan(restored.resolve("V_LevelX"))
    before = e.services.document.current
    assert not e.execute("FindLevel y,1000").success
    assert e.services.document.current is before


def test_stats_nan_inf_scaled_locations_and_followon_expression(engine):
    from materials_data_studio.tools.graph_waves import make_wave

    e = engine
    e.services.write(
        [make_wave("root:y", [np.nan, 5.0, np.inf, 1.0, -np.inf], e.services.wave("y"))],
        overwrite=True,
    )
    ok(e, 'SetScale/P x,10,2,"K",y; WaveStats/Q y; Variable average=V_avg; Print V_minloc')
    assert e.services.resolve("average") == 3
    assert e.services.resolve("V_minloc") == 16
    assert e.services.resolve("V_npnts") == 2
    assert e.services.resolve("V_numNaNs") == 1
    assert e.services.resolve("V_numINFs") == 2
    assert e.services.resolve("V_sdev") == pytest.approx(np.sqrt(8))
    ok(e, "WaveStats/Q/R=[1,3] y; V_avg=9; Print V_avg")
    assert e.services.resolve("V_avg") == 9


def test_empty_coordinate_range_does_not_reverse_or_search_outside_range(engine):
    ok(engine, "FindLevel/Q/R=(0.1,0.2) y,1")
    assert engine.services.resolve("V_flag") == 1
    assert np.isnan(engine.services.resolve("V_LevelX"))


def test_gui_axis_assignment_mixed_history_and_recording(engine, qtbot):
    from materials_data_studio.tools.graph_window import GraphEditorWindow

    window = GraphEditorWindow()
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    qtbot.addWidget(window)
    window.bind_document(engine.services.document.render_source())
    window._discard_approved = True
    window.command_workspace.execute("Display/N=G y vs x; AppendImage map")
    window.apply_properties("trace", "y", {"x_axis": "top"})
    assert window.editor.document.graph("G").traces[0].x_axis == "top"
    assert "top" in window.compatibility.text() or "Supported subset" in window.compatibility.text()
    assert any(a._mds_axis_names == ("top", "left") for a in window.figure.axes)
    window.editor.history.undo()
    assert window.editor.document.graph("G").traces[0].x_axis == "bottom"
    window.editor.history.redo()
    assert window.editor.document.graph("G").traces[0].x_axis == "top"
    from materials_data_studio.tools.command_recording import append_trace

    code = append_trace(window.graph(), window.graph().traces[0])
    assert "/T=top" in code
    top = next(a for a in window.figure.axes if a._mds_axis_names[0] == "top")
    window.region = {
        "ax": top,
        "start": (top.bbox.x0 + top.bbox.width * 0.25, top.bbox.y0),
        "end": (top.bbox.x0 + top.bbox.width * 0.75, top.bbox.y1),
    }
    window.zoom_region("expand", "horizontal")
    graph = window.graph()
    assert next(a for a in graph.axes if a.name == "top").limits is not None
    assert next(a for a in graph.axes if a.name == "bottom").limits is None
    window.close()


def test_python_api_and_command_conversion_preserve_top_axis(engine, monkeypatch):
    import sys

    from materials_data_studio.tools.command_python import to_python
    from materials_data_studio.tools.procedure_api import install_api

    monkeypatch.setitem(sys.modules, "mds", None)
    api = install_api(engine.services)
    graph = api.graph.new("Python")
    graph.append("y", "x", x_axis="top")
    assert graph.model.traces[0].x_axis == "top"
    source = to_python("Display/T/N=Converted y vs x", engine.services)
    exec(source, {})
    assert engine.services.graph("Converted").traces[0].x_axis == "top"


def test_shared_action_command_replay_axis_semantics(engine):
    from materials_data_studio.tools.command_recording import semantic_events
    from materials_data_studio.tools.command_services import assign_trace_axes

    ok(engine, "Display/N=G y vs x")
    doc = engine.services.document
    before = doc.current.clone()
    after = before.clone()
    after.graphs = (assign_trace_axes(doc.graph("G"), "y", x_axis="top"),)
    events = semantic_events(doc, before, after, "Edit trace: y")
    recipe = "\n".join(line for line in events if not line.startswith("//"))
    ok(engine, recipe)
    assert doc.graph("G").traces[0].x_axis == "top"
    fig, _ = render(doc, "G")
    assert fig.axes[0].xaxis.get_label_position() == "top"


def test_invalid_axis_assignment_rejected(engine):
    ok(engine, "Display/N=G y vs x; NewFreeAxis/R aux")
    before = engine.services.document.current
    assert not engine.execute("AppendToGraph/B=aux y vs x").success
    assert engine.services.document.current is before


def test_converted_igor_scope_results(engine):
    from materials_data_studio.tools.procedure_api import install_api

    api = install_api(engine.services)
    scope = api.igor.scope()
    scope.command("WaveStats/Q y; Print V_avg")
    assert scope.evaluate("V_avg") == 4
    scope.command("FindLevel/Q y,3")
    assert scope.evaluate("V_LevelX") == 1.5
