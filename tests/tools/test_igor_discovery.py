"""Native discovery regressions: values, references, rendering and persistence."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import h5py
import numpy as np
import pytest
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from materials_data_studio.tools.command_engine import CommandEngine
from materials_data_studio.tools.command_services import DocumentServices
from materials_data_studio.tools.graph_document import load_graph_document, save_graph_document
from materials_data_studio.tools.graph_h5xp import load_h5xp, save_h5xp
from materials_data_studio.tools.graph_waves import empty_document, make_wave, rename_wave
from materials_data_studio.tools.igor_box import box_statistics
from materials_data_studio.tools.igor_trace_channels import point_colors
from materials_data_studio.tools.igor_wave_ops import reconstruction_filter, resample_wave
from materials_data_studio.tools.procedure_api import install_api
from materials_data_studio.tools.pxp_model import PxpBoxPlot
from materials_data_studio.tools.pxp_recreation import parse_recreation
from materials_data_studio.tools.pxp_render import render_graph

REF = Path(__file__).parents[1] / "reference_igor/v10_03_discovery"


def save(path, document):
    return save_h5xp(path, document) if path.suffix == ".h5xp" else save_graph_document(path, document)


def load(path):
    return load_h5xp(path) if path.suffix == ".h5xp" else load_graph_document(path)


@pytest.fixture
def engine():
    return CommandEngine(DocumentServices(empty_document()))


def ok(engine, code):
    r = engine.execute(code)
    assert r.success, r.message
    return r


def test_concatenate_promotion_append_explicit_dimensions_and_labels(engine):
    s = engine.services
    ok(engine, 'Make/D/N=4 a,b;a=p;b=p+4;Concatenate/DL {a,b},joined')
    np.testing.assert_array_equal(s.wave("joined").data, [[0, 4], [1, 5], [2, 6], [3, 7]])
    assert s.wave("joined").dimension_labels[1] == ("a", "b")
    ok(engine, "Concatenate {a,b},joined")
    assert s.wave("joined").shape == (4, 4)
    ok(engine, "Concatenate/NP {a,b},flat")
    np.testing.assert_array_equal(s.wave("flat").data, np.arange(8))
    ok(engine, "Concatenate/NP=0 {joined,joined},rows")
    assert s.wave("rows").shape == (8, 4)
    ok(engine, "Concatenate/NP=2 {joined,joined},layers")
    assert s.wave("layers").shape == (4, 4, 2)
    ok(engine, "Make/D/N=3 short;Concatenate {a,short},mixed")
    assert s.wave("mixed").shape == (7,)
    before = s.document.current
    assert not engine.execute("Concatenate/O {a},a").success
    assert s.document.current is before


def test_split_order_metadata_naming_and_atomic_failure(engine):
    s = engine.services
    ok(engine, 'Make/D/N=(4,2,3) source;source=p+10*q+100*r;SetScale/P x,2,.5,"s",source;Note source,"synthetic";SetDimLabel 0,1,pointB,source')
    ok(engine, 'SplitWave/SDIM=1/N=col/SCL=1/NOTE source')
    np.testing.assert_array_equal(s.wave("col3").data, np.arange(4) + 110)
    assert s.wave("col3").dimension_scaling == ((2., .5),)
    assert s.wave("col3").wave_note == "synthetic"
    assert s.wave("col3").dimension_labels[0][1] == "pointB"
    assert s.resolve("S_waveNames") == "col0;col1;col2;col3;col4;col5;"
    assert len({w.id for w in s.document.render_source().waves}) == 7
    ok(engine, 'SplitWave/SDIM=2/NAME="l0;l1;l2;"/DDF=layers source')
    assert s.wave("root:layers:l2").shape == (4, 2)
    assert s.wave("root:layers:l2").wave_note == ""
    before = s.document.current
    assert not engine.execute('SplitWave/NAME="new;col1;new2;new3;new4;new5;"/SDIM=1 source').success
    assert s.document.current is before and s.document.render_source().wave("new") is None


@pytest.mark.parametrize(("up", "down", "n", "name"), [(2, 1, 3, "coeff3"), (2, 1, 5, "coeff5"), (2, 1, 21, "coeff21"), (1, 3, 21, "coeff_down"), (3, 2, 5, "coeff_mix")])
def test_native_filter_coefficients(up, down, n, name):
    np.testing.assert_allclose(reconstruction_filter(up, down, n, "None" if n == 3 else "Hanning"), np.loadtxt(REF / (name + ".tsv")), atol=2e-15, rtol=0)


@pytest.mark.parametrize(("name", "up", "down", "n", "end"), [
    ("up", 2, 1, 21, 3), ("down", 1, 3, 21, 3), ("mix", 3, 2, 5, 3),
    *[(f"edge{i}", 2, 1, 5, i) for i in range(4)],
])
def test_native_resample_arrays_and_scaling(name, up, down, n, end):
    p = np.arange(128)
    source = make_wave("signal", np.sin(p * .31) + .2 * np.cos(p * 1.7))
    source = replace(source, dimension_scaling=((5., .25),))
    result = resample_wave(source, up=up, down=down, samples=n, end=end)
    np.testing.assert_allclose(result.data, np.loadtxt(REF / (name + ".tsv")), rtol=0, atol=2e-14)
    assert result.dimension_scaling == ((5., .25 * down / up),)
    assert source.shape == (128,) and not source.data.flags.writeable


def test_resample_linear_multidimensional_rate_and_transaction(engine):
    s = engine.services
    ok(engine, "Make/D/N=(16,2) a;a=p+100*q;Resample/UP=2/N=3/WINF=None a")
    assert s.wave("a").shape == (31, 2)
    np.testing.assert_allclose(s.wave("a").data[:, 1], np.arange(31) / 2 + 100)
    before = s.document.current
    assert not engine.execute("Make/D/N=10 short;Resample short").success
    assert s.document.current is before
    ok(engine, "Resample/RATE=4/N=3/WINF=None a")
    assert s.wave("a").dimension_scaling[0][1] == .25


def test_metadata_public_api_clear_unicode_roundtrip(engine, tmp_path):
    s = engine.services
    ok(engine, 'Make/D/N=(4,2) a;Note a,"one";Note a," two";SetDimLabel 1,0,channelA,a;SetDimLabel 0,-1,time,a;SetDimLabel 1,1,$"温度",a')
    api = install_api(s)
    handle = api.wave("a")
    assert handle.note == "one\r two" and handle.get_label(1, 1) == "温度"
    handle.note = "研究用 metadata"
    handle.set_label(1, 0, "channelB")
    ok(engine, 'SetDimLabel 1,1,$"",a')
    assert handle.get_label(1, 1) == ""
    for suffix in (".mdsg", ".h5xp"):
        path = tmp_path / ("metadata" + suffix)
        save(path, s.document)
        loaded = load(path).render_source().wave("root:a")
        assert loaded is not None and loaded.wave_note == handle.note
        assert loaded.dimension_labels == s.wave("a").dimension_labels
        assert loaded.dimension_names == s.wave("a").dimension_names


def test_zcolor_renderer_rename_guards_and_persistence(engine, tmp_path):
    s = engine.services
    ok(engine, "Make/D/N=5 y,z;y=p;z=p+1;Display/N=G y;ModifyGraph mode(y)=3,zColor(y)={z,*,*,Grays},zmrkSize(y)={z,*,*,1,5};ColorScale trace=y")
    trace = s.graph().traces[0]
    colors = point_colors(s.document.render_source(), trace)
    np.testing.assert_allclose(colors[[0, -1]], [[0, 0, 0, 1], [1, 1, 1, 1]])
    fig = Figure()
    FigureCanvasAgg(fig)
    render_graph(fig, s.document.render_source(), s.graph())
    fig.canvas.draw()
    assert len(fig.axes) == 2 and len(fig.axes[0].collections) > 0
    sizes = fig.axes[0].collections[0].get_sizes()
    assert sizes[0] < sizes[-1]
    ok(engine, r'Legend/N=l "\s(y) colored data"')
    render_graph(fig, s.document.render_source(), s.graph())
    fig.canvas.draw()  # Inline legend symbols must accept channel Artists.
    before = s.document.current
    assert not engine.execute("KillWaves z").success and s.document.current is before
    assert not engine.execute("Redimension/N=2 z").success and s.document.current is before
    s.document.current = rename_wave(s.document, "z", "renamed")
    assert s.graph().traces[0].z_color[0] == "root:renamed"
    for suffix in (".mdsg", ".h5xp"):
        path = tmp_path / ("trace" + suffix)
        save(path, s.document)
        assert load(path).current.graphs == s.document.current.graphs
        if suffix == ".h5xp":
            with h5py.File(path, "r+") as f:
                del f["MDS Graph Document"]
            native_style = load(path)
            channel = native_style.current.graphs[0].traces[0].z_color
            assert channel is not None
            np.testing.assert_array_equal(native_style.render_source().wave(channel[0]).data, s.wave("renamed").data)


def test_box_hinges_whiskers_small_nan_and_native_import(engine, tmp_path):
    stat = box_statistics([1, 2, 3, 4, 5], PxpBoxPlot())
    assert (stat["q1"], stat["med"], stat["q3"], stat["whislo"], stat["whishi"]) == (2, 3, 4, 1, 5)
    stat = box_statistics([1, 2, 3, 4, 100, np.nan], PxpBoxPlot(whisker_method=(2,)))
    assert stat["whishi"] == 4
    assert np.isnan(box_statistics([np.nan], PxpBoxPlot())["med"])
    s = engine.services
    ok(engine, "Make/D/N=16 exY,exY2;exY=1+p/2;exY2=sin(p/4);Display/N=G;AppendBoxPlot exY,exY2;ModifyBoxPlot trace=exY,notched,whiskerMethod=2,showData=Outliers")
    assert len(s.graph().traces) == 1 and s.graph().traces[0].box.waves == ("root:exY", "root:exY2")
    fig = Figure()
    FigureCanvasAgg(fig)
    render_graph(fig, s.document.render_source(), s.graph())
    assert fig.axes[0].patches
    assert fig.axes[0].get_xlim()[0] < -.25 and fig.axes[0].get_xlim()[1] > 1.25
    ok(engine, r'Legend/N=l "\s(exY) distributions"')
    render_graph(fig, s.document.render_source(), s.graph())
    fig.canvas.draw()
    native = Path(__file__).parents[2] / "docs/igor_audit/exploration/evidence/native/boxes_after_gui.txt"
    graphs, _ = parse_recreation(native.read_text())
    assert len(graphs[0].traces) == 1 and graphs[0].traces[0].box.notched
    for suffix in (".mdsg", ".h5xp"):
        path = tmp_path / ("box" + suffix)
        save(path, s.document)
        assert load(path).current.graphs == s.document.current.graphs
        if suffix == ".h5xp":
            with h5py.File(path, "r+") as f:
                del f["MDS Graph Document"]
            native_style = load(path)
            assert native_style.current.graphs[0].traces[0].box.notched


def test_native_h5xp_label_attribute_and_external_edits(engine, tmp_path):
    native = load_h5xp(REF / "labels.h5xp").render_source().waves[0]
    assert native.dimension_names == ("rowName", "")
    assert native.dimension_labels == ((), ("channelA", "", "channelC"))
    assert native.wave_note == "native synthetic"
    ok(engine, 'Make/D/N=4 a;Note a,"old";SetDimLabel 0,0,first,a')
    path = tmp_path / "attributes.h5xp"
    save_h5xp(path, engine.services.document)
    with h5py.File(path, "r+") as f:
        f["Packed Data/MDS/a"].attrs["IGORWaveNote"] = "externally edited"
    # The hash-bound MDS cache must not resurrect the old note.
    assert load_h5xp(path).render_source().waves[0].wave_note == "externally edited"


@pytest.mark.parametrize("method", range(4))
def test_native_quartile_modes(method):
    s = box_statistics([1, 2, 3, 4, 5], PxpBoxPlot(quartile_method=method))
    np.testing.assert_allclose([s["q1"], s["med"], s["q3"]], np.loadtxt(REF / f"quartile{method}.tsv"))


def test_native_note_append_separator(engine):
    ok(engine, 'Make/N=1 a;Note a,"first";Note a,"second"')
    assert engine.services.wave("a").wave_note.encode() == (REF / "note.txt").read_bytes()


def test_gui_forms_preview_trace_box_edit_undo_and_codec(qtbot, engine, tmp_path):
    from PySide6.QtCore import Qt

    from materials_data_studio.tools.graph_scientific_dialogs import WaveOperationDialog
    from materials_data_studio.tools.graph_window import GraphEditorWindow
    ok(engine, "Make/D/N=8 a,b;a=p;b=p+1;Display/N=G a;ModifyGraph mode(a)=3")
    document = engine.services.document
    dialog = WaveOperationDialog(None, document, "Concatenate")
    qtbot.addWidget(dialog)
    for i in range(dialog.waves.count()):
        dialog.waves.item(i).setSelected(True)
    before = document.current
    dialog.preview_result()
    assert "(8, 2)" in dialog.preview.toPlainText() and document.current is before
    notes = WaveOperationDialog(None, document, "Note")
    qtbot.addWidget(notes)
    notes.waves.item(0).setSelected(True)
    notes.note.setPlainText("first")
    ok(engine, notes.build_command())
    notes.append_note.setChecked(True)
    assert notes.note.toPlainText() == ""
    notes.note.setPlainText("second")
    ok(engine, notes.build_command())
    assert document.render_source().wave("root:a").wave_note == "first\rsecond"
    window = GraphEditorWindow()
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    qtbot.addWidget(window)
    window.bind_graph_document(document)
    window.apply_properties("trace", "a", {"z_color_config": "b,*,*,Grays", "color_scale": True})
    assert document.graph("G").traces[0].z_color[0] == "root:b"
    window.editor.history.undo()
    assert document.graph("G").traces[0].z_color is None
    window.editor.history.redo()
    before = document.current
    with pytest.raises(ValueError, match=r"not found|Unknown|No wave"):
        window.apply_properties("trace", "a", {"z_color_config": "missing,*,*,Grays"})
    assert document.current is before
    path = tmp_path / "gui.mdsg"
    window.editor.save(path)
    assert not window.editor.dirty and load_graph_document(path).current.graphs == document.current.graphs
    # Discard transient GUI changes without opening a save confirmation.
    window.editor.history.setClean()
    window.editor.saved = True


def test_new_operations_through_reviewed_procedure_api(engine):
    api = install_api(engine.services)
    api.command('Make/D/N=(4,2) source;source=p+q;SplitWave/N=col/SCL=1 source;Concatenate/NP {col0,col1},flat')
    api.command('Note col0,"created";SetDimLabel 0,0,origin,col0')
    assert api.wave("col0").note == "created"
    assert api.wave("col0").get_label(0, 0) == "origin"
    api.command("Display/N=G;AppendBoxPlot col0,col1;ModifyBoxPlot trace=col0,notched")
    assert engine.services.graph().traces[0].box.notched


def test_discovery_operations_in_appcontainer(engine, tmp_path):
    from materials_data_studio.tools.procedure_sandbox import launch
    (tmp_path / "input").mkdir()
    (tmp_path / "output").mkdir()
    save_graph_document(tmp_path / "input/input.mdsg", engine.services.document)
    code = ('from mds import command\n'
            'command(\'Make/D/N=4 a,b;a=p;b=p+1;Concatenate {a,b},joined;SplitWave/N=col joined\')\n'
            'command(\'Note col0,"synthetic";SetDimLabel 0,0,origin,col0\')\n'
            'command(\'Display/N=G a;ModifyGraph mode(a)=3,zColor(a)={b,*,*,Grays}\')\n'
            'command(\'Display/N=B;AppendBoxPlot a,b;ModifyBoxPlot trace=a,notched\')\n'
            'command(\'Make/D/N=64 signal;signal=sin(p*.1);Resample/UP=2 signal\')\n')
    (tmp_path / "input/procedure.py").write_text(code, encoding="utf-8")
    assert launch(tmp_path) == 0
    result = load_graph_document(tmp_path / "output/output.mdsg")
    assert result.render_source().wave("root:signal").shape == (127,)
    assert result.render_source().wave("root:col0").wave_note == "synthetic"
    assert result.graph("B").traces[0].box.notched
    channel = result.graph("G").traces[0].z_color
    assert channel is not None and channel[0] == "root:b"
