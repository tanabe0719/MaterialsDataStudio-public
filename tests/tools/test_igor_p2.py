"""Independent native P2 references and cross-layer editing contracts."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from materials_data_studio.tools.command_engine import CommandEngine
from materials_data_studio.tools.command_services import DocumentServices
from materials_data_studio.tools.graph_document import load_graph_document, save_graph_document
from materials_data_studio.tools.graph_h5xp import load_h5xp, save_h5xp
from materials_data_studio.tools.graph_waves import empty_document
from materials_data_studio.tools.igor_violin import kde
from materials_data_studio.tools.pxp_render import render_graph

REF = Path(__file__).parents[1] / "reference_igor/v10_03_p2"


@pytest.fixture
def engine():
    return CommandEngine(DocumentServices(empty_document()))


def ok(engine, code):
    result = engine.execute(code)
    assert result.success, result.message
    return result


def test_native_threshold_roi_nan_invert_and_atomic(engine):
    s = engine.services
    ok(engine, "Make/D/N=(4,4) im;im=p+4*q;ImageThreshold/T=7 im")
    mask = s.wave("M_ImageThresh")
    assert mask.data.dtype == np.uint8 and mask.data[3, 1] == 0 and mask.data[0, 2] == 255
    assert s.wave("im").data[3, 3] == 15
    ok(engine, "ImageThreshold/T=7/I/DEST=inverted im")
    np.testing.assert_array_equal(s.wave("inverted").data, 255-mask.data)
    ok(engine, "Make/B/U/N=(4,4) roi;roi=q>1;ImageThreshold/T=7/R={roi,2}/DEST=crop im")
    assert np.all(s.wave("crop").data[:, 2:] == 64)
    before = s.document.current
    assert not engine.execute("ImageThreshold/T=7/DEST=im im").success
    assert s.document.current is before


def test_native_rotate_scaling_and_unwrap_nan(engine):
    s = engine.services
    ok(engine, "Make/D/N=5 a;a=p;SetScale/P x,2,.5,a;Rotate 7,a")
    np.testing.assert_array_equal(s.wave("a").data, np.loadtxt(REF / "rotate.tsv"))
    assert s.wave("a").dimension_scaling == ((-1.5, .5),)
    # Native reference input: 0,8,6,NaN,4,2,0. Keep accumulated offset across NaNs.
    s.create_wave("gap", [0., 8, 6, np.nan, 4, 2, 0])
    ok(engine, "Unwrap 10,gap")
    native = [float(v) if v.strip() else np.nan for v in (REF / "unwrap_nan.tsv").read_text().splitlines()]
    np.testing.assert_allclose(s.wave("gap").data, native, equal_nan=True)


@pytest.mark.parametrize(("command", "fixture"), [
    ("StatsTTest/MEAN=2/CI/Q a", "ttest_one"),
    ("StatsTTest/Q a,b", "ttest_two"),
    ("StatsTTest/PAIR/Q a,b", "ttest_pair"),
])
def test_native_ttest_arrays_labels_and_provenance(engine, command, fixture):
    ok(engine, "Make/D/N=5 a,b;a=p+1;b=2*p+1;" + command)
    wave = engine.services.wave("W_StatsTTest")
    np.testing.assert_allclose(wave.data, np.loadtxt(REF / (fixture + ".tsv")), rtol=2e-12, atol=1e-13)
    if fixture != "ttest_one":
        assert wave.dimension_labels[0] == tuple((REF / (fixture + "_labels.tsv")).read_text().splitlines())
    assert "inputs" in wave.wave_note


def test_native_kde_and_violin_persistence_reference_protection(engine, tmp_path):
    np.testing.assert_allclose(kde(np.arange(1., 6), np.arange(0, 6, .5), 1), np.loadtxt(REF / "kde_gaussian_h1.tsv"), atol=2e-15)
    ok(engine, "Make/D/N=5 a,b;a=p+1;b=2*p+1;Display/N=V;AppendViolinPlot a,b;ModifyViolinPlot bandwidth=1,showMean=1,showMedian=1,plotSide=1")
    s = engine.services
    fig = Figure()
    FigureCanvasAgg(fig)
    render_graph(fig, s.document.render_source(), s.graph())
    fig.canvas.draw()
    assert fig.axes[0].collections
    before = s.document.current
    assert not engine.execute("KillWaves b").success and s.document.current is before
    for suffix in (".mdsg", ".h5xp"):
        path = tmp_path / ("violin" + suffix)
        (save_graph_document if suffix == ".mdsg" else save_h5xp)(path, s.document)
        loaded = (load_graph_document if suffix == ".mdsg" else load_h5xp)(path)
        assert loaded.current.graphs == s.document.current.graphs


def test_trace_range_xy_errors_tag_cursor_table_and_roundtrip(engine, tmp_path):
    s = engine.services
    ok(engine, 'Make/D/N=6 a,e;a=p*p;e=.2;Display/N=g a[1,4];ErrorBars/L=2/T=1/Y=3/RGB=(0,0,65535) a XY,wave=(e,e),wave=(e,e);Cursor/P A,a,2;Tag/N=t/L=2/X=5/Y=5 a,2,"value=\\\\OY";Edit/N=tab a;ModifyTable/W=tab format(a)=3,digits(a)=2,width(a)=90')
    g = s.graph('g')
    assert g.traces[0].point_range == (1, 4)
    assert g.traces[0].x_error_low == 'root:e'
    assert g.cursors[0].point == 2
    assert s.expression_functions['vcsr']('A', 'g') == 4
    figure = Figure()
    FigureCanvasAgg(figure)
    render_graph(figure, s.document.render_source(), g)
    figure.canvas.draw()
    line = next(line for line in figure.axes[0].lines if line.get_gid() == 'trace:a')
    np.testing.assert_array_equal(line.get_xdata(), [1, 2, 3, 4])
    assert any(a.get_gid() == 'tag-arrow:t' for a in figure.axes[0].texts)
    from materials_data_studio.tools.igor_graph_extensions import format_cell, table_styles
    table = s.document.render_source().table_definitions[0]
    assert format_cell(1.2, table_styles(table, 'root:a')) == '1.20'
    native = tmp_path / 'extensions.h5xp'
    save_h5xp(native, s.document)
    loaded = load_h5xp(native)
    restored = loaded.current.graphs[0]
    assert restored.traces[0].point_range == (1, 4)
    assert restored.traces[0].x_error_low
    assert restored.cursors[0].point == 2
    assert restored.annotations[0].arrow == 2
    assert loaded.render_source().table_definitions[0].column_styles


def test_irregular_rgb_category_and_date_axes(engine, tmp_path):
    s = engine.services
    ok(engine, 'Make/D/N=(2,2,3) rgb;rgb=65535*(r==0);Make/D/N=3 edges;edges=p*p;Display/N=im;AppendImage rgb vs {edges,*}')
    figure = Figure()
    FigureCanvasAgg(figure)
    render_graph(figure, s.document.render_source(), s.graph())
    figure.canvas.draw()
    mesh = figure.axes[0].collections[0]
    np.testing.assert_allclose(mesh.get_coordinates()[0,:,0], [0,1,4])
    assert np.allclose(mesh.get_facecolors()[0], [1,0,0,1])
    s.create_wave('labels', np.array(['first','second']))
    ok(engine, 'Make/D/N=2 y;y=p;Display/N=category y vs labels')
    render_graph(figure, s.document.render_source(), s.graph())
    figure.canvas.draw()
    assert [t.get_text() for t in figure.axes[0].get_xticklabels()] == ['first','second']
    ok(engine, 'Display/N=date y;ModifyGraph dateInfo(bottom)={2,1,0}')
    render_graph(figure, s.document.render_source(), s.graph())
    assert figure.axes[0].xaxis.get_major_formatter()(0) == '1904-01-01'
    before = s.document.current
    assert not engine.execute('AppendImage rgb vs {y,*}').success
    assert s.document.current is before


def test_contour_and_layout_semantics(engine, tmp_path):
    ok(engine, 'Make/D/N=(5,5) matrix;matrix=p*p+q*q;Display/N=contour;AppendMatrixContour matrix;ModifyContour matrix,autoLevels={0,0,0},moreLevels={2,5,10},fill=1;NewLayout/N=page;AppendLayoutObject/R=(36,36,576,396) graph contour')
    s = engine.services
    figure = Figure(figsize=(8.5,11))
    FigureCanvasAgg(figure)
    render_graph(figure, s.document.render_source(), s.graph('page'))
    figure.canvas.draw()
    assert len(figure.subfigs) == 1
    np.testing.assert_allclose(figure.subfigs[0].bbox_relative.bounds, [36/612,1-396/792,540/612,360/792])
    assert any(a.get_gid() == 'contour:root:matrix' for a in figure.axes[0].collections)
    path = tmp_path/'page.mdsg'
    save_graph_document(path, s.document)
    restored = load_graph_document(path)
    assert restored.current.graphs[-1].layout_items[0].graph == 'contour'
    assert restored.current.graphs[0].contours[0].levels == (2,5,10)
    before = s.document.current
    assert not engine.execute('AppendLayoutObject graph page').success
    assert before is s.document.current


def test_filled_contour_single_level_rejected_before_render(engine):
    ok(engine, 'Make/D/N=(3,3) z;z=p+q;Display/N=C;AppendMatrixContour z')
    before = engine.services.document.current
    result = engine.execute('ModifyContour z,manLevels={1,1,1},fill=1')
    assert not result.success
    assert engine.services.document.current is before
    ok(engine, 'ModifyContour z,manLevels={1,1,1},fill=0')
    figure = Figure()
    FigureCanvasAgg(figure)
    render_graph(figure, engine.services.document.render_source(), engine.services.graph())
    figure.canvas.draw()


def test_gui_forms_roi_subwindow_and_undo(engine, qtbot):
    from materials_data_studio.tools.graph_extension_dialogs import GraphExtensionDialog
    from materials_data_studio.tools.graph_history import GraphEditorController
    ok(engine, 'Make/D/N=(4,4) im;Make/D/N=5 y;y=p;Display/N=host y')
    s = engine.services
    dialog = GraphExtensionDialog(None, s.document, 'Rectangular ROI', 'host')
    qtbot.addWidget(dialog)
    for field, value in zip(dialog.fields, ['im','roi','1,2','1,3'], strict=True):
        field.setText(value)
    ok(engine, dialog.build_command())
    assert s.wave('roi').data.dtype == np.uint8
    assert np.count_nonzero(s.wave('roi').data == 0) == 6
    before = s.document.current
    ok(engine, 'Display/HOST=host/N=inset/W=(.5,.1,.9,.5) y')
    after = s.document.current
    s.document.current = before
    controller = GraphEditorController(s.document)
    controller.commit(before, after, 'Add subwindow')
    figure = Figure()
    FigureCanvasAgg(figure)
    render_graph(figure, s.document.render_source(), s.graph('host'))
    figure.canvas.draw()
    assert len(figure.subfigs) == 1
    controller.history.undo()
    assert len(s.document.current.graphs) == 1
    controller.history.redo()
    assert s.document.current.graphs[-1].host == 'host'


def test_p2_operations_in_appcontainer(engine, tmp_path):
    from materials_data_studio.tools.procedure_sandbox import launch
    (tmp_path/'input').mkdir()
    (tmp_path/'output').mkdir()
    save_graph_document(tmp_path/'input/input.mdsg', engine.services.document)
    code = ('from mds import command\n'
            'command("Make/D/N=5 a,b;a=p+1;b=2*p+1;StatsTTest/PAIR/T/Q a,b")\n'
            'command("Rotate 2,a;Unwrap 10,b;Display/N=V;AppendViolinPlot a,b;ModifyViolinPlot bandwidth=1")\n'
            'command("Display/N=G a;Cursor/P A,a,2;ModifyGraph dateInfo(bottom)={2,1,0}")\n')
    (tmp_path/'input/procedure.py').write_text(code, encoding='utf-8')
    assert launch(tmp_path) == 0, (tmp_path/'output/procedure.log').read_text(encoding='utf-8')
    result = load_graph_document(tmp_path/'output/output.mdsg')
    assert result.render_source().wave('root:W_StatsTTest').shape == (7,)
    assert result.graph('V').traces[0].violin.bandwidth == 1
    assert result.graph('G').cursors[0].point == 2


def test_native_roi_float_nan_and_automatic_threshold(engine):
    s = engine.services
    image = np.arange(16., dtype=float).reshape((4,4),order='F')
    image[0,0] = np.nan
    s.create_wave('im', image)
    ok(engine, 'Make/B/U/N=(4,4) roi;roi=q>1;ImageThreshold/T=7/R={roi,1} im')
    native = np.array([[float(c) if c else np.nan for c in line.split('\t')] for line in (REF/'threshold_roi.tsv').read_text().splitlines()])
    np.testing.assert_allclose(s.wave('M_ImageThresh').data, native, equal_nan=True)
    ok(engine, 'ImageThreshold/M=1/DEST=iterative im')
    assert s.resolve('V_threshold') == 7.75
    ok(engine, 'ImageThreshold/M=7/DEST=otsu im')
    assert s.resolve('V_threshold') == pytest.approx(7.972549019607843)


def test_colorscale_options_export_and_replay(engine, tmp_path):
    from dataclasses import replace

    from materials_data_studio.tools.command_recording import semantic_events, verified_replay
    s = engine.services
    ok(engine, 'Make/D/N=(4,4) im;im=p+q+1;Display/N=G;AppendImage im;ColorScale image=im,width=10,nticks=3,minor=1,tickLen=-8,fsize=12,log=1,axisLabel="Intensity"')
    figure = Figure()
    FigureCanvasAgg(figure)
    render_graph(figure, s.document.render_source(), s.graph())
    figure.canvas.draw()
    bar = figure.axes[1]
    assert bar.get_yscale() == 'log'
    assert s.graph().images[0].colorbar_axis.major_length == 8
    assert s.graph().images[0].colorbar_label_raw == 'Intensity'
    assert not engine.execute('ColorScale image=im,nonexistentProperty=1').success
    ok(engine, 'Make/D/N=5 y;y=p;Display/N=T y')
    before = s.document.current
    after = before.clone()
    after.graphs = tuple(replace(g, traces=(replace(g.traces[0], line_width=2),)) if g.name == 'T' else g for g in after.graphs)
    events = semantic_events(s.document,before,after,'Line width')
    script = verified_replay(s.document,before,after,events)
    assert script and 'lsize' in script
    ok(engine, script)
    assert s.graph('T').traces[0].line_width == 2


def test_table_scaled_index_label_columns(engine, qtbot):
    from PySide6.QtCore import Qt

    from materials_data_studio.tools.graph_tables import TableModel
    s = engine.services
    ok(engine, 'Make/D/N=3 a;a=p;SetScale/P x,2,.5,"s",a;SetDimLabel 0,0,start,a;Edit/N=t a,a.i,a.l;ModifyTable/W=t format(a)=3,digits(a)=2')
    model = TableModel(s.document,s.document.render_source().table_definitions[0],editable=True)
    assert model.columnCount() == 3
    assert model.data(model.index(0,0)) == '0.00'
    assert model.data(model.index(1,1)) == '2.5'
    assert model.data(model.index(0,2)) == 'start'
    assert not model.flags(model.index(0,1)) & Qt.ItemFlag.ItemIsEditable
    ok(engine, 'Rename a,renamed')
    assert s.document.render_source().table_definitions[0].columns == ('root:renamed','root:renamed.i','root:renamed.l')


def test_native_recreation_calls_precede_definitions_and_canvas_import(engine, tmp_path):
    import h5py
    ok(engine, 'Make/D/N=(5,5) z;z=p*p+q*q;Display/N=C;AppendMatrixContour z;ModifyContour z,manLevels={1,2,3};NewLayout/N=page;AppendLayoutObject/R=(36,36,576,396) graph C')
    path = tmp_path/'canvas.h5xp'
    save_h5xp(path,engine.services.document)
    with h5py.File(path,'a') as h:
        text = h['Recreation/Recreation Procedures'].asstr()[()].replace('\r','\n')
        assert text.startswith('C()\npage()\nWindow C()')
        del h['MDS Graph Document']
    loaded = load_h5xp(path)
    assert loaded.graph('page').window_kind == 'Layout'
    assert loaded.graph('page').layout_items[0].graph == 'C'
    assert loaded.graph('C').contours[0].levels == (1,3,5)


@pytest.mark.parametrize('operation,values', [
    ('Cursor',['B','a','1']),
    ('Tag',['a','1','point=\\OP','2','5','5']),
    ('XY Error Bars',['a','err','err','err','err','1','2']),
    ('Image Coordinates',['im','edges','*']),
    ('Date Axis',['bottom','2','1','0']),
    ('Table Format',['t','a','3','2','100','1']),
    ('Contour',['im','1,2,3','1']),
    ('Layout',['Page','G','36,36,576,396']),
    ('Subwindow',['Inset','a','.5,.1,.9,.5']),
])
def test_extension_gui_commands_and_persistence(engine, qtbot, tmp_path, operation, values):
    from materials_data_studio.tools.graph_extension_dialogs import GraphExtensionDialog
    s = engine.services
    ok(engine, 'Make/D/N=5 a,err;a=p;err=.1;Make/D/N=4 edges;edges=p*p;Make/D/N=(3,3) im;im=p+q;Edit/N=t a;Display/N=G a;AppendImage im')
    dialog = GraphExtensionDialog(None,s.document,operation,'G')
    qtbot.addWidget(dialog)
    for field,value in zip(dialog.fields,values,strict=True):
        field.setText(value)
    command = dialog.build_command()
    ok(engine,command)
    path = tmp_path/'form.mdsg'
    save_graph_document(path,s.document)
    restored = load_graph_document(path)
    assert restored.current.graphs == s.document.current.graphs
    figure = Figure()
    FigureCanvasAgg(figure)
    render_graph(figure,restored.render_source(),restored.current.graphs[-1])
    figure.canvas.draw()


def test_native_reopen_readback_and_hashes(engine):
    import hashlib
    import json

    from materials_data_studio.tools.pxp_recreation import parse_recreation
    manifest = json.loads((REF/'manifest.json').read_text(encoding='utf-8'))
    for name,digest in manifest['files'].items():
        assert hashlib.sha256((REF/name).read_bytes()).hexdigest() == digest
    graphs,_ = parse_recreation((REF/'mds_native_reopen.txt').read_text(encoding='utf-8'))
    assert len(graphs) == 3
    assert graphs[0].traces[0].x_error_low == 'root:MDS:err'
    assert graphs[0].traces[0].error_low == 'root:MDS:err'
    assert graphs[0].annotations[0].trace == 'a'
    assert graphs[1].traces[0].violin.bandwidth == 1
    assert graphs[2].images[0].colorbar_axis.tick_count == 3
