"""Selected remaining audit contracts: model, numerical/Artist, save and Undo."""
from __future__ import annotations

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
from materials_data_studio.tools.graph_render import IgorMatplotlibRenderer
from materials_data_studio.tools.graph_typography import annotation_font, graph_font
from materials_data_studio.tools.graph_waves import empty_document
from materials_data_studio.tools.procedure_api import install_api
from materials_data_studio.tools.pxp_model import PxpAnnotationStyle
from materials_data_studio.tools.pxp_snapshot import decode_document, encode_document
from materials_data_studio.tools.pxp_style import image_colormap, native_rainbow
from materials_data_studio.tools.pxp_text import text_runs

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = json.loads((ROOT / "tests/reference_igor/v10_03_extended.json").read_text())


def test_native_reference_manifest_integrity():
    import hashlib
    evidence = dict(REFERENCE)
    integrity = evidence.pop("integrity")
    payload = json.dumps(evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    assert hashlib.sha256(payload).hexdigest() == integrity["evidence_sha256"]
    values = np.array([*evidence["fft_real"]["input"], *evidence["histogram"]["input"],
                       *evidence["weighted_line"]["x"], evidence["weighted_line"]["sigma"],
                       evidence["float32_expression"]["source"]], dtype="<f8")
    assert hashlib.sha256(values.tobytes()).hexdigest() == integrity["numeric_input_sha256"]


def ok(engine, code):
    result = engine.execute(code)
    assert result.success, result.message
    return result


@pytest.fixture
def engine():
    return CommandEngine(DocumentServices(empty_document()))


def render(engine, dpi=96):
    figure = Figure(figsize=(420/72, 300/72), dpi=dpi)
    FigureCanvasAgg(figure)
    IgorMatplotlibRenderer().render(figure, engine.services.document, "G")
    figure.canvas.draw()
    return figure


@pytest.mark.parametrize("dtype", ["float32", "float64", "complex64", "complex128", "int32"])
@pytest.mark.parametrize("rotate", [True, False])
def test_fft_native_sign_rotation_precision(engine, dtype, rotate):
    data = np.arange(8).astype(dtype)
    if data.dtype.kind == "c":
        data += 1j * (7-np.arange(8))
    engine.services.create_wave("src", data)
    ok(engine, "SetScale/P x,2,0.25,\"s\",src")
    original = engine.services.wave("src")
    ok(engine, "FFT/DEST=out" + ("/Z" if not rotate else "") + " src")
    result = engine.services.wave("out")
    expected = np.fft.ifft(data) * len(data) if data.dtype.kind == "c" else np.conj(np.fft.rfft(data))
    if data.dtype.kind == "c" and rotate:
        expected = np.fft.fftshift(expected)
    np.testing.assert_allclose(result.data, expected, atol=2e-6)
    assert result.data.dtype == np.dtype("complex128" if dtype in {"float64", "complex128"} else "complex64")
    assert result.dimension_scaling == (((-2 if data.dtype.kind == "c" and rotate else 0), .5),)
    assert result.dimension_units == ("Hz",)
    assert engine.services.wave("src") is original
    if rotate and dtype in {"float64", "complex128"}:
        golden = REFERENCE["fft_complex" if data.dtype.kind == "c" else "fft_real"]
        np.testing.assert_allclose(result.data.real, golden["real"], atol=golden["print_absolute_tolerance"], rtol=0)
        np.testing.assert_allclose(result.data.imag, golden["imag"], atol=golden["print_absolute_tolerance"], rtol=0)


def test_fft_replace_flags_and_atomic_reject(engine):
    ok(engine, "Make/D/N=8 src;src=p")
    original = engine.services.wave("src")
    ok(engine, "FFT src")
    assert engine.services.wave("src").id == original.id
    assert engine.services.wave("src").shape == (5,)
    before = engine.services.document.current
    for code in ("FFT/DEST=src src", "FFT/OUT=7 src", "FFT/WINF=Hann src", "Make/D/N=3 short;FFT short"):
        assert not engine.execute(code).success
        assert engine.services.document.current is before


@pytest.mark.parametrize("option", ["", "/C", "/P", "/CUM", "/P/CUM", "/DP"])
def test_histogram_native_edges_scale(engine, option, tmp_path):
    engine.services.create_wave("src", REFERENCE["histogram"]["input"])
    ok(engine, f"Histogram/B={{0,1,4}}/DEST=hist{option} src")
    wave = engine.services.wave("hist")
    expected = np.arange(1, 5) if "CUM" in option else np.ones(4)
    if "/P" in option:
        expected = expected / 6
    np.testing.assert_allclose(wave.data, expected, rtol=1e-7)
    assert wave.dimension_scaling == (((.5 if "/P" in option or option == "/C" else 0), 1),)
    path = tmp_path / "hist.mdsg"
    save_graph_document(path, engine.services.document)
    np.testing.assert_allclose(load_graph_document(path).render_source().wave("hist").data, expected, rtol=1e-7)


def test_curvefit_coefficients_covariance_weights_rollback(engine):
    x = np.arange(6, dtype=float)
    y = 2 + 3*x + np.array([.1,-.2,.15,-.05,.1,-.1])
    engine.services.create_wave("x", x)
    engine.services.create_wave("y", y)
    engine.services.create_wave("sigma", np.full(6, .5))
    ok(engine, "CurveFit/Q/X=x/W=sigma/I=1/D=fit/R=residual/M=2 line,y")
    design = np.column_stack((np.ones(6), x))
    coef = np.linalg.lstsq(design, y, rcond=None)[0]
    covariance = np.linalg.inv(design.T @ design) * .25
    for name, expected in (("W_coef", coef), ("MDS_Covar", covariance), ("W_sigma", np.sqrt(np.diag(covariance))), ("fit", design@coef), ("residual", y-design@coef)):
        np.testing.assert_allclose(engine.services.wave(name).data, expected, atol=1e-12)
    assert engine.services.resolve("V_FitError") == 0
    state = engine.services.document.current
    assert not engine.execute("x=0;CurveFit line,y,x").success
    assert engine.services.document.current is state


@pytest.mark.parametrize("shape", [(3,4), (2,3,4), (2,3,2,4)])
@pytest.mark.parametrize("flags,dtype", [("/D","float64"), ("/I","int32"), ("/W/U","uint16"), ("/D/C","complex128")])
def test_multidimensional_context_type_and_roundtrip(engine, shape, flags, dtype, tmp_path):
    ok(engine, f"Make{flags}/N=({','.join(map(str,shape))}) w")
    formula = "+".join("pqrs"[:len(shape)])
    if "/C" in flags:
        formula = "cmplx(" + formula + ",2)"
    ok(engine, "w=" + formula)
    expected = np.indices(shape).sum(axis=0).astype(dtype) + (2j if "/C" in flags else 0)
    np.testing.assert_array_equal(engine.services.wave("w").data, expected)
    assert engine.services.wave("w").data.dtype == np.dtype(dtype)
    ok(engine, 'SetScale/P y,10,2,"K",w')
    ok(engine, "w=x+y")
    coords = np.indices(shape)
    np.testing.assert_array_equal(engine.services.wave("w").data, coords[0]+10+2*coords[1])
    path = tmp_path/"multi.mdsg"
    save_graph_document(path, engine.services.document)
    wave = load_graph_document(path).render_source().wave("w")
    assert wave.shape == shape and wave.dimension_units[1] == "K"
    np.testing.assert_array_equal(wave.data, engine.services.wave("w").data)


def test_folders_references_readback_and_undo_state(engine, tmp_path):
    ok(engine, "NewDataFolder root:a;NewDataFolder root:dest;Make/D/N=8 root:a:y;Display/N=G root:a:y;Edit/N=T root:a:y")
    prior = engine.services.document.current.clone()
    ok(engine, "MoveDataFolder root:a,root:dest")
    assert engine.services.graph().traces[0].y_wave == "root:dest:a:y"
    assert engine.services.document.render_source().table_definitions[0].columns == ("root:dest:a:y",)
    ok(engine, "RenameDataFolder root:dest:a,b")
    assert engine.services.graph().traces[0].y_wave == "root:dest:b:y"
    model = install_api(engine.services).graph("G").trace("y").info()
    assert model["y_wave"] == "root:dest:b:y"
    ok(engine, 'String info=AxisInfo("G","left")')
    assert json.loads(engine.services.resolve("info"))["name"] == "left"
    state = engine.services.document.current
    assert not engine.execute("MoveDataFolder root:dest,root:dest:b").success
    assert engine.services.document.current is state
    engine.services.document.current = prior
    assert engine.services.document.render_source().wave("root:a:y") is not None


def test_rainbow_exact_native_reference_and_freeze(engine, tmp_path):
    reference = np.loadtxt(ROOT/"docs/igor_audit/evidence/igor/rainbow.tsv", dtype=np.uint16)
    np.testing.assert_array_equal(native_rainbow(), reference)
    ok(engine, "Make/D/N=(3,4) image;image=p+q;Display/N=G;AppendImage image;ModifyImage image ctab={0,5,Rainbow,1},nanRGB=NaN,minRGB=(65535,0,0),maxRGB=(0,0,65535)")
    image = engine.services.graph().images[0]
    assert len(image.color_lut) == 100 and image.lut_provenance
    colors = image_colormap(image)
    np.testing.assert_array_equal(np.rint(np.asarray(colors.colors)*65535), reference[::-1])
    path = tmp_path/"palette.mdsg"
    save_graph_document(path, engine.services.document)
    assert load_graph_document(path).graph("G").images[0].color_lut == image.color_lut
    fig = render(engine)
    assert len(fig.axes[0].images) == 1
    assert fig.axes[0].images[0].cmap.get_bad()[3] == 0


@pytest.mark.parametrize("dpi", [72,96,144])
@pytest.mark.parametrize("swap", [False,True])
def test_label_point_offset_and_roundtrip(engine, dpi, swap):
    ok(engine, "Make/D/N=8 y;y=p;Display/N=G y;Label left \"Value\";Label bottom \"Time\";ModifyGraph margin(left)=70,margin(bottom)=50,margin(right)=30,margin(top)=30,lblPosMode(left)=3,lblPos(left)=30,lblLatPos(left)=0")
    if swap:
        ok(engine,"ModifyGraph swapXY=1")
    fig = render(engine,dpi)
    label = fig.axes[0].xaxis.label if swap else fig.axes[0].yaxis.label
    before = label.get_transform().transform(label.get_position())
    ok(engine, "ModifyGraph lblLatPos(left)=27")
    fig = render(engine,dpi)
    label = fig.axes[0].xaxis.label if swap else fig.axes[0].yaxis.label
    after = label.get_transform().transform(label.get_position())
    expected = np.array([27,0]) if swap else np.array([0,-27])
    np.testing.assert_allclose((after-before)*72/dpi, expected, atol=1e-8)
    meta, payload = encode_document(engine.services.document.render_source())
    assert decode_document(meta,payload).graphs[0].axes == engine.services.graph().axes


def test_fonts_independent_inheritance_relative_and_fallback(engine):
    ok(engine,'Make/N=8 y;Display/N=G y;ModifyGraph gFont="Arial",fSize=18;Legend/N=l "\\s(y) yData"')
    graph = engine.services.graph()
    assert annotation_font(graph, PxpAnnotationStyle(inherit_graph_font=True)).size == 10
    assert graph_font(graph,axis_size=18).size == 18
    graph = replace(graph,style=replace(graph.style,window_size=(420,300),relative_font_size=5))
    assert graph_font(graph).size == REFERENCE["typography"]["relative_420x300"]
    assert graph_font(replace(graph,style=replace(graph.style,window_size=(840,600)))).size == 30
    assert graph_font(graph, family="MDS nonexistent font 12345").fallback
    runs = text_runs(r"A\Zr200B\]0C", "Arial", 10)
    assert [r.size for r in runs] == [10,20,10]


def test_ticks_manual_zap_log_subminor_and_visibility(engine):
    ok(engine, 'Make/D/N=8 y;y=p;Display/N=G y;SetAxis bottom 0,4;ModifyGraph manTick(bottom)={0,1,0,0},manMinor(bottom)={3,2},minor(bottom)=1,tickZap(bottom)={2},tickEnab(bottom)={1,3},grid(bottom)=1,gridStyle(bottom)=3')
    fig = render(engine)
    axis = fig.axes[0].xaxis
    np.testing.assert_array_equal(axis.get_majorticklocs(), [1,2,3])
    assert [t.get_text() for t in axis.get_ticklabels()] == ["1","","3"]
    assert 1.25 in axis.get_minorticklocs()
    ok(engine,'SetAxis bottom 1,3;ModifyGraph log(bottom)=1,ttLen(bottom)=1,ttThick(bottom)=0.25,tickEnab(bottom)={-inf,inf}')
    fig = render(engine)
    assert len(fig.axes[0].xaxis.get_minorticklocs()) > 1


def test_resource_limits_atomic_and_old_snapshot(engine):
    before = engine.services.document.current
    for code in ("Make/N=(1000,1000,1000) huge", "Make/I/C bad", "Make/I/N=4 n;n=Inf", "Make/N=(2,2,2,2,2) five"):
        assert not engine.execute(code).success
        assert engine.services.document.current is before
    ok(engine,"Make/N=4 a")
    metadata,payload = encode_document(engine.services.document.render_source())
    metadata["pxp_viewer_schema_version"] = 12
    assert decode_document(metadata,payload).waves[0].shape == (4,)


def test_native_float_expression_and_integer64_redimension(engine):
    engine.services.create_wave("single", np.array([16777216], dtype=np.float32))
    ok(engine, "single=(single+1)-single")
    assert engine.services.wave("single").data[0] == REFERENCE["float32_expression"]["expected"]
    ok(engine, 'Make/L/N=(2,3) w;w=p+10*q;SetScale/P x,2,0.5,"s",w;Redimension/N=6 w')
    wave = engine.services.wave("w")
    assert wave.data.dtype == np.dtype("int64")
    np.testing.assert_array_equal(wave.data, [0,1,10,11,20,21])
    assert wave.dimension_scaling == ((2,.5),) and wave.dimension_units == ("s",)


def test_native_fit_reference_trailing_flags_and_mask(engine):
    golden = REFERENCE["weighted_line"]
    x = np.array(golden["x"], float)
    engine.services.create_wave("x", x)
    engine.services.create_wave("y", 2+3*x+.1*np.sin(x))
    engine.services.create_wave("sd", np.full(len(x), golden["sigma"]))
    ok(engine, "CurveFit/Q line,y /X=x /W=sd /I=1 /D /R")
    np.testing.assert_allclose(engine.services.wave("W_coef").data, golden["coefficients"], atol=golden["print_tolerance"], rtol=0)
    np.testing.assert_allclose(engine.services.wave("W_sigma").data, golden["sigma_coefficients"], atol=golden["print_tolerance"], rtol=0)
    assert engine.services.wave("fit_y").shape == (2,)
    engine.services.create_wave("mask", [1,1,1,1,0,0])
    ok(engine, "CurveFit/Q/M=2 poly 3,y /X=x /M=mask /R=masked")
    assert engine.services.wave("M_Covar").shape == (3,3)
    assert np.isnan(engine.services.wave("masked").data[-2:]).all()


def test_multidimensional_plane_editing(qtbot, engine):
    from materials_data_studio.tools.graph_wave_dialogs import WaveEditorDialog
    ok(engine, "Make/D/N=(2,3,2,2) w;w=p+q+10*r+100*s")
    original = engine.services.wave("w")
    dialog = WaveEditorDialog(None, original)
    qtbot.addWidget(dialog)
    model = dialog.model
    model.set_plane(0,1)
    model.set_plane(1,1)
    assert model.value(1,2) == 113
    assert model.setData(model.index(1,2), "123.5")
    model.set_plane(0,0)
    assert model.value(1,2) == 103
    result = model.result()
    assert result.data[1,2,1,1] == 123.5 and original.data[1,2,1,1] == 113


def test_shared_python_and_converted_scope_context(engine):
    from materials_data_studio.tools.igor_procedure_runtime import IgorAPI
    api = install_api(engine.services)
    api.command('Make/D/C/N=(2,3,2,2) w;w=cmplx(p+q+r+s,2);SetScale/P z,5,2,"K",w')
    assert engine.services.wave("w").data[1,2,1,1] == 5+2j
    scope = IgorAPI(engine.services).scope()
    assert scope.evaluate("DimDelta(w,2)") == 2
    assert scope.evaluate("DimOffset(w,2)") == 5
    scope.assign("w", "cmplx(p+q+r+s,3)")
    assert engine.services.wave("w").data[1,2,1,1] == 5+3j


@pytest.mark.parametrize("dpi", [72,96,144])
def test_measured_native_legend_profile_and_anchor(engine, dpi):
    from materials_data_studio.tools.graph_typography import font_family
    if font_family("Arial")[1]:
        pytest.skip("Native Arial metric profile requires Arial")
    ok(engine, r'Make/N=8 y;y=p;Display/N=G y;ModifyGraph gFont="Arial",gfSize=10,fSize=18,mode=3,marker=8;Legend/N=l/F=2/A=RT "\s(y) yData"')
    figure = render(engine,dpi)
    frame = next(a for a in figure.findobj() if a.get_gid() == "annotation:l")
    box = frame.get_window_extent(figure.canvas.get_renderer())
    native = REFERENCE["typography"]["legend_RECT_at_10"]
    assert box.width*72/dpi == pytest.approx(native[2]-native[0], abs=2)
    assert box.height*72/dpi == pytest.approx(native[3]-native[1], abs=2)
    anchor = frame.text.get_transform().transform(frame.text.get_position())
    assert box.x1 == pytest.approx(anchor[0], abs=1)
    assert box.y1 == pytest.approx(anchor[1], abs=1)


def test_native_custom_palette_wave_snapshot(engine):
    from materials_data_studio.tools.pxp_recreation import parse_recreation
    from materials_data_studio.tools.pxp_style import bind_palette_waves
    engine.services.create_wave("image", np.arange(6.).reshape(2,3))
    palette = engine.services.create_wave("custom", np.array([[65535,0,0],[0,0,65535]],dtype=np.uint16))
    graphs,_ = parse_recreation("Window G() : Graph\nDisplay\nAppendImage image\nModifyImage image ctab={0,5,custom,0}\nEndMacro")
    graph = bind_palette_waves(engine.services.document.render_source().waves,graphs)[0]
    np.testing.assert_array_equal(graph.images[0].color_lut,palette.data)
    assert not any(i.code == "igor.image.palette_fallback" for i in graph.issues)


def test_free_cross_axis_and_standoff_geometry(engine):
    ok(engine,'Make/D/N=8 y,x;y=p;x=p;Display/N=G y vs x;AppendToGraph/T y vs x;SetAxis bottom 0,10;SetAxis top 0,20;NewFreeAxis/R=aux aux;AppendToGraph/R=aux y vs x;ModifyGraph freePos(aux)={10,top},standoff(left)=1,fSize(left)=18')
    figure=render(engine)
    auxiliary = next(a for a in figure.axes if a._mds_axis_names == ("bottom","aux"))
    assert auxiliary.spines["right"].get_position() == ("axes", .5)
    left = figure.axes[0].spines["left"]
    assert left.get_position() == ("outward",6)


def test_legacy_label_layout_migration_without_renderer(engine, tmp_path):
    ok(engine, "Make/N=8 y;Display/N=G y")
    doc = engine.services.document
    doc.current.layout["G"] = {"labels":{"left":{"coordinate_system":"axis_label","offset_points":42,"lateral":.7}}}
    path=tmp_path/"legacy.mdsg"
    save_graph_document(path,doc)
    restored=load_graph_document(path)
    axis=next(a for a in restored.graph("G").axes if a.name=="left")
    assert axis.style.label_position == 42
    assert axis.style.label_lateral_fraction == .7
    assert "labels" not in restored.current.layout["G"]
    save_graph_document(path,restored)
    assert load_graph_document(path).graph("G") == restored.graph("G")


def test_new_context_in_isolated_worker(engine, tmp_path):
    from materials_data_studio.tools.procedure_sandbox import launch
    (tmp_path/"input").mkdir()
    (tmp_path/"output").mkdir()
    save_graph_document(tmp_path/"input/input.mdsg",engine.services.document)
    (tmp_path/"input/procedure.py").write_text(
        'from mds import command\ncommand("Make/D/C/N=(2,3,2,2) multi;multi=cmplx(p+q+r+s,2)")\n'
        'command("Make/D/N=8 signal;signal=p;FFT/DEST=spectrum signal")\n',
        encoding="utf-8")
    assert launch(tmp_path) == 0
    result=load_graph_document(tmp_path/"output/output.mdsg").render_source()
    assert result.wave("multi").data[1,2,1,1] == 5+2j
    assert result.wave("spectrum").shape == (5,)


def test_explicit_readback_modes_and_result_folder_guard(engine):
    ok(engine, 'Make/N=8 y;Display/N=G y')
    assert not engine.execute('Print TraceInfo("G","y",1)').success
    ok(engine, 'NewDataFolder/S results;WaveStats/Q root:y;SetDataFolder root:')
    assert not engine.execute('KillDataFolder results').success


def test_log_filtered_subminor_styles(engine):
    ok(engine, 'Make/N=8 y;y=1+p/20;Display/N=G y;SetAxis left 1,1.4;ModifyGraph log(left)=1,minor(left)=1,ttLen(left)=0,ttThick(left)=0,tickEnab(left)={1.1,1.3}')
    figure = render(engine)
    ticks = figure.axes[0].yaxis
    assert all(1.1 <= v <= 1.3 for v in ticks.get_minorticklocs())
    assert any(t.tick1line.get_markersize() == 0 for t in ticks.get_minor_ticks())


def test_long_axis_form_scrolls_without_compressed_controls(qtbot):
    from materials_data_studio.tools.graph_window import GraphEditorWindow
    window = GraphEditorWindow()
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    qtbot.addWidget(window)
    window.resize(1100, 700)
    window.bind_document(empty_document().render_source())
    window._discard_approved = True
    window.show()
    window.command_workspace.execute('Make/N=8 y;Display/N=G y')
    window.select_target('axis','left')
    qtbot.wait(50)
    record = window.property_widgets['axis']
    page = record['selector'].parentWidget()
    assert page.height() > window.properties.height()
    first = record['widgets']['minimum'][0]
    second = record['widgets']['maximum'][0]
    assert second.y() >= first.y() + first.height()


def test_annotation_dialog_keeps_graph_font_inheritance(engine, qtbot):
    from materials_data_studio.tools.graph_annotation_dialog import AnnotationDialog
    ok(engine, 'Make/N=8 y;Display/N=G y;ModifyGraph gFont="DejaVu Sans",gfSize=12;TextBox/N=note "Generated annotation"')
    dialog = AnnotationDialog(None, engine.services.document, 'G', 'note')
    qtbot.addWidget(dialog)
    assert dialog.values()['font_size'] == 0
    graph, layout = dialog.updated()
    assert graph.annotations[0].style.inherit_graph_font
    assert 'font' not in layout['annotations']['note']


@pytest.mark.parametrize('flag,value', [('L',2**63), ('L/U',2**64), ('I',2**31)])
def test_integer_exclusive_upper_bound_atomic(engine, flag, value):
    ok(engine, f'Make/{flag}/N=2 w')
    before = engine.services.document.current
    assert not engine.execute(f'w={value}').success
    assert engine.services.document.current is before
    from materials_data_studio.tools.igor_procedure_runtime import IgorAPI
    with pytest.raises(ValueError, match='range'):
        IgorAPI(engine.services).scope().assign('w',str(value))
    with pytest.raises(ValueError, match='range'):
        install_api(engine.services).wave('w').assign(float(value))


def test_converted_python_double_expression_and_dtype(engine):
    from materials_data_studio.tools.command_python import to_python
    install_api(engine.services)
    exec(to_python('Make/N=2 w;w=16777216;w=(w+1)-w',engine.services),{})
    np.testing.assert_array_equal(engine.services.wave('w').data,[1,1])
    assert engine.services.wave('w').data.dtype == np.float32


def test_compound_integer_overflow_is_not_silent(engine):
    ok(engine,'Make/I/N=2 w;w=2147483647')
    before = engine.services.document.current
    assert not engine.execute('w+=1').success
    assert engine.services.document.current is before
    assert not engine.execute('Redimension/B w').success
    assert engine.services.document.current is before
