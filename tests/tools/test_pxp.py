"""PXP parsing, recreation, rendering, and portable session regression tests."""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

import numpy as np
import pytest
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import LogNorm
from matplotlib.figure import Figure
from matplotlib.path import Path as MarkerPath
from matplotlib.ticker import AutoMinorLocator, MultipleLocator

from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.core.session import load_session, save_session
from materials_data_studio.tools.pxp_backend import Igor2Backend, _long_name_record
from materials_data_studio.tools.pxp_markers import marker_path
from materials_data_studio.tools.pxp_model import PxpDocument, PxpWave
from materials_data_studio.tools.pxp_recreation import formatted_text, parse_recreation
from materials_data_studio.tools.pxp_render import render_graph, render_wave
from materials_data_studio.tools.pxp_snapshot import decode_document, encode_document
from materials_data_studio.tools.pxp_text import IgorText, text_runs
from materials_data_studio.tools.pxp_window import PxpViewerWindow
from materials_data_studio.ui.main_window import MainWindow


def _long_wave_bytes() -> bytes:
    """Generated TN003 v7 wave; never uses a private research fixture."""
    name = b"synthetic_signal_with_more_than_thirty_one_characters"
    labels = b"axis\0first\0second\0"
    header = bytearray(384)
    struct.pack_into("<HH13i", header, 0, 7, 0, 352, 0, 4, 0,
                     0, 0, 0, 0, len(labels), 0, 0, 0, 0)
    struct.pack_into("<h", header, 56, len(name))
    struct.pack_into("<ih", header, 76, 4, 4)
    struct.pack_into("<4i", header, 132, 4, 0, 0, 0)
    struct.pack_into("<4d", header, 148, 0.5, 1, 1, 1)
    struct.pack_into("<4d", header, 180, 2, 0, 0, 0)
    struct.pack_into("<H", header, 2, (-sum(struct.unpack("<192h", header))) & 0xffff)
    return bytes(header) + np.arange(4, dtype="<f8").tobytes() + b"note" + labels + name


def test_long_name_wave_adapter_preserves_values_scales_and_note(tmp_path: Path) -> None:
    body = _long_wave_bytes()
    record = _long_name_record({}, body)
    wave = record.wave["wave"]
    np.testing.assert_array_equal(wave["wData"], np.arange(4))
    assert wave["wave_header"]["bname"].startswith(b"synthetic_signal_with_more")
    assert wave["note"] == b"note"
    path = tmp_path / "synthetic.pxp"
    path.write_bytes(struct.pack("<Hhi", 3, 0, len(body)) + body)
    document = Igor2Backend().open(path)
    assert len(document.waves) == 1 and document.status == "PARTIAL"
    assert any(issue.scope == "wave" and "v7 variable-length dimension labels" in issue.message
               for issue in document.issues)
    assert document.waves[0].dimension_labels == ((),)
    assert document.waves[0].wave_note == "note"
    np.testing.assert_array_equal(document.waves[0].data, np.arange(4))
    np.testing.assert_array_equal(document.waves[0].coordinate(0), [2, 2.5, 3, 3.5])
    assert not document.waves[0].data.flags.writeable


@pytest.mark.parametrize("corruption", ["short", "checksum", "length", "dimensions", "name"])
def test_long_name_wave_adapter_rejects_corrupt_records(corruption: str) -> None:
    body = bytearray(_long_wave_bytes())
    if corruption == "short":
        body = body[:100]
    elif corruption == "checksum":
        body[10] ^= 1
    else:
        if corruption == "length":
            struct.pack_into("<i", body, 4, 2**30)
        elif corruption == "dimensions":
            struct.pack_into("<i", body, 132, -1)
        elif corruption == "name":
            body[-1] = 0xff
        struct.pack_into("<h", body, 2, 0)
        struct.pack_into("<H", body, 2, (-sum(struct.unpack_from("<192h", body))) & 0xffff)
    with pytest.raises(ValueError):
        _long_name_record({}, bytes(body))


def test_offsets_implicit_x_and_missing_explicit_x_do_not_mutate_data() -> None:
    document = _document()
    graph = parse_recreation('''Window Test() : Graph
Display /W=(0,0,400,300) y
AppendToGraph y vs missing
ModifyGraph muloffset(y)={0,0.0015},offset(y)={-1,0.02}
EndMacro''')[0][0]
    original = document.wave("y")
    assert original is not None
    data = np.asarray(original.data, dtype=float).copy()
    figure = Figure()
    render_graph(figure, document, graph)
    assert len(figure.axes[0].lines) == 1
    line = figure.axes[0].lines[0]
    np.testing.assert_array_equal(line.get_xdata(), original.coordinate(0) - 1)
    np.testing.assert_allclose(line.get_ydata(), data * 0.0015 + 0.02)
    np.testing.assert_array_equal(original.data, data)


def test_display_multiplier_scales_error_bars_without_scaling_raw_waves() -> None:
    graph = parse_recreation('''Window Test() : Graph
Display y vs x
ModifyGraph muloffset={0,-2},offset={0,5}
ErrorBars y Y,wave=(err,err)
EndMacro''')[0][0]
    document = _document()
    figure = Figure()
    render_graph(figure, document, graph)
    segments = figure.axes[0].collections[0].get_segments()
    np.testing.assert_allclose(segments[0][:, 1], [0.6, 1.4])
    wave = document.wave("err")
    assert wave is not None
    np.testing.assert_allclose(wave.data, [0.2, 0.4, 0.3, 0.5])


def test_named_annotations_appendtext_symbols_spacing_and_drawings_roundtrip() -> None:
    from dataclasses import replace

    from materials_data_studio.tools.pxp_render import _AnnotationFrame
    document = _document()
    graph = parse_recreation(r'''Window Test() : Graph
Display y vs x
AppendToGraph err vs x
ModifyGraph width={Aspect,1},height=220,nticks(left)=0,tick=2,mirror=1
ModifyGraph margin(left)=56,margin(right)=85,lblMargin(left)=30
TextBox/C/N=title/F0/B1/A=LT "\Z15\f02Synthetic\f00"
Legend/C/N=first/F0/B1/A=MC/X=58/Y=10 "\Z17\s(err) Error\r\sb+13No symbol"
AppendText "\r\s(y) Signal"
Legend/C/N=second/F0/B1/A=RB "\Z12Second"
SetDrawLayer UserFront
SetDrawEnv xcoord= bottom,ycoord= prel,arrow=1,arrowlen=4,arrowfat=1
DrawLine 2,0.2,2,0.4
SetDrawEnv fsize=16
DrawText 0.1,0.1,"Drawn title"
EndMacro''')[0][0]
    assert graph.status == "Fully supported"
    assert len(graph.annotations) == 3 and len(graph.drawings) == 2
    assert graph.drawings[0].ycoord == "prel"
    assert graph.annotations[1].raw.endswith("\r" + r"\s(y) Signal")
    figure = Figure(figsize=(8, 6))
    FigureCanvasAgg(figure)
    render_graph(figure, document, graph)
    figure.canvas.draw()
    ax = figure.axes[0]
    assert len(ax.get_yticks()) == 0 and len(ax.yaxis.get_minorticklocs()) == 0
    frames = [a for a in ax.artists if isinstance(a, _AnnotationFrame)]
    assert len(frames) == 3 and frames[1].style.frame == 0
    assert frames[1].text.symbol_handles["err"] is ax.lines[1]
    box = frames[1].text.get_window_extent(figure.canvas.get_renderer())
    assert box.height > 40 and (box.x0 + box.x1) / 2 > ax.get_window_extent().x1
    from materials_data_studio.tools.graph_drawing import DrawingArtist
    assert len([a for a in ax.artists if isinstance(a, DrawingArtist)]) == 2
    drawing = graph.drawings[0]
    arrow = next(a for a in ax.artists if isinstance(a, DrawingArtist))
    points = arrow.base_transform.transform([[2, 0.2], [2, 0.4]])
    assert points[0, 1] > points[1, 1]  # Igor plot-relative Y increases DOWN.
    assert drawing.arrow == 1
    metadata, payload = encode_document(replace(document, graphs=(graph,)))
    restored = decode_document(json.loads(json.dumps(metadata)), payload)
    assert restored.graphs[0] == graph
    metadata["pxp_viewer_schema_version"] = 3
    assert decode_document(metadata, payload).graphs[0].annotations == graph.annotations


def test_igor_spacing_and_unit_escape_are_not_visible_text() -> None:
    assert formatted_text(r"\Z17\sb+1316 K\r\n4.2 K") == "16 K\n4.2 K"
    runs = text_runs(r"\Z17\sb+1316 K\r\n\f02M\f00 (\uμ\BB\M/Ir)", "Arial", 12)
    assert runs[0].text == "16 K\n" and runs[0].space_below == 6.5
    assert "\\u" not in "".join(r.text for r in runs)


def test_fixed_margins_and_window_edge_label_margin() -> None:
    document = _document()
    graph = parse_recreation('''Window Test() : Graph
Display y vs x
ModifyGraph width=200,height=300,margin(left)=56,margin(bottom)=48,margin(top)=14,margin(right)=14
ModifyGraph lblMargin(left)=30,lblMargin(bottom)=8
EndMacro''')[0][0]
    figure = Figure(figsize=(6, 6))
    FigureCanvasAgg(figure)
    render_graph(figure, document, graph)
    figure.canvas.draw()
    ax = figure.axes[0]
    assert ax.get_window_extent().height / ax.get_window_extent().width == pytest.approx(1.5)
    assert not ax.xaxis._autolabelpos and not ax.yaxis._autolabelpos
    assert ax.yaxis.label.get_position()[0] == pytest.approx((30 - 56) / 200)
    assert ax.xaxis.label.get_position()[1] == pytest.approx((8 - 48) / 300)
    assert ax.xaxis.label.get_verticalalignment() == "bottom"
    assert ax.yaxis.label.get_verticalalignment() == "bottom"


def test_custom_dash_patterns_and_distinct_default_dash_presets() -> None:
    from materials_data_studio.tools.pxp_render import _DASH_PATTERNS
    assert len(set(_DASH_PATTERNS.values())) == 17
    graph = parse_recreation('''SetDashPattern 17,{20,3,15,8}
Window Test() : Graph
Display y vs x
ModifyGraph lStyle=17,lOptions=1,lineJoin={1,10}
EndMacro''')[0][0]
    assert graph.status == "Fully supported"
    assert graph.style.dash_patterns == ((17, (20, 3, 15, 8)),)
    figure = Figure()
    render_graph(figure, _document(), graph)
    line = figure.axes[0].lines[0]
    assert line.is_dashed() and line.get_dash_capstyle() == "round"
    assert line.get_solid_joinstyle() == "round"


def _document() -> PxpDocument:
    source = b"synthetic source only"
    x = np.linspace(1, 4, 4)
    y = np.array([2.0, 4.0, 3.0, 5.0])
    image = np.arange(12.0).reshape(4, 3) + 1
    lines = """Window XY() : Graph
Display y vs x
ModifyGraph mode=3,marker=8,rgb=(65535,0,0),msize=4
Label left "Intensity"
SetAxis bottom 0,5
ErrorBars y Y,wave=(err,err)
EndMacro
Window Map() : Graph
Display
AppendImage map
ModifyImage map ctab= {1,12,VioletOrangeYellow,0}
ModifyImage map log= 1
ColorScale/C/N=text0 image=map
AppendText "Normalized Intensity"
EndMacro
"""
    graphs, tables = parse_recreation(lines)
    waves = tuple(PxpWave(name, f"root:{name}", name, data.dtype.str, data.shape,
                          data, ((5.0, 0.5), (10.0, 2.0))[:data.ndim],
                          ("degree", "GPa")[:data.ndim], "", "")
                  for name, data in (("x", x), ("y", y), ("err", y * 0.1), ("map", image)))
    for wave in waves:
        wave.data.setflags(write=False)
    return PxpDocument("11111111-1111-1111-1111-111111111111", "synthetic.pxp",
                       hashlib.sha256(source).hexdigest(), len(source), "10.0", "igor2",
                       "0.5.13", waves, ("root",), graphs, tables, (), (), "FULL", source)


def test_bad_and_truncated_pxp_do_not_raise(tmp_path: Path) -> None:
    for name, content in (("bad.pxp", b"bad"), ("truncated.pxp", b"\x03\0\0\0\xff\xff\xff\x7f")):
        path = tmp_path / name
        path.write_bytes(content)
        document = Igor2Backend().open(path)
        assert document.status == "OPEN_FAILED"
        assert document.issues


def test_recreation_whitelist_and_igor_formatting() -> None:
    text = """Window Plot() : Graph
Display a vs b
AppendToGraph/L=left2 c vs b
ModifyGraph marker(a)=8,rgb=(65535,32768,0),swapXY=1,axisEnab(left2)={0,0.5}
SetAxis left 0,2
Label bottom "\\\\Z16Field"
Legend/C/N=text0 "\\\\s(a) First\\r\\\\s(c) Second"
UnknownCommand suspicious
EndMacro
"""
    graphs, _tables = parse_recreation(text)
    graph = graphs[0]
    assert len(graph.traces) == 2
    assert graph.traces[1].axis == "left2"
    assert graph.traces[0].color_igor == (65535, 32768, 0)
    assert graph.swap_xy
    assert graph.status == "Partially supported"
    assert graph.issues[0].scope == "UnknownCommand"
    assert formatted_text(graph.axes[-1].label_raw) == "Field"
    assert formatted_text(r"\Z162\f02θ\f00") == "2θ"
    assert formatted_text(r"a/a\B0") == "a/a₀"


def test_render_xy_errorbar_and_scaled_log_image() -> None:
    document = _document()
    figure = Figure()
    render_graph(figure, document, document.graphs[0])
    assert len(figure.axes[0].lines) >= 1
    assert len(figure.axes[0].collections) >= 1  # scientific Y error bars
    assert figure.axes[0].get_xlabel() == ""
    render_graph(figure, document, document.graphs[1])
    assert len(figure.axes) == 2  # image plus colorbar
    assert tuple(figure.axes[0].images[0].get_extent()) == (4.75, 6.75, 9.0, 15.0)
    assert isinstance(figure.axes[0].images[0].norm, LogNorm)
    assert figure.axes[1].get_ylabel() == "Normalized Intensity"
    render_wave(figure, document.wave("map"))  # type: ignore[arg-type]
    assert len(figure.axes) == 2


def test_pxp_session_restores_snapshot_without_parser(tmp_path: Path, monkeypatch: object) -> None:
    document = _document()
    state = ProjectState(pxp_documents={document.source_id: document},
                         pxp_view_states={document.source_id: {
                             "graphs": {"XY": {"traces": {"y": {"visible": False}},
                                                "axes": {"left": {"label": "Edited"}}}},
                             "selected": ("graph", "XY"),
                         }})
    target = tmp_path / "view.mdss"
    save_session(state, target)
    def reject_reparse(_self: object, _path: Path) -> None:
        raise AssertionError("Restoring MDSS must not reparse the PXP")
    monkeypatch.setattr(Igor2Backend, "open", reject_reparse)  # type: ignore[attr-defined]
    restored = load_session(target)
    result = restored.pxp_documents[document.source_id]
    assert result.sha256 == hashlib.sha256(document.source_bytes).hexdigest()
    assert result.source_bytes == document.source_bytes
    assert np.array_equal(result.wave("map").data, document.wave("map").data)  # type: ignore[union-attr]
    assert restored.pxp_view_states[document.source_id]["graphs"]["XY"]["traces"]["y"]["visible"] is False
    assert not restored.modified
    target.unlink()
    assert np.array_equal(result.wave("x").data, document.wave("x").data)  # type: ignore[union-attr]


def test_multi_axis_panels_and_swap_xy_render() -> None:
    document = _document()
    text = """Window Multi() : Graph
Display y vs x
AppendToGraph/L=left2 err vs x
ModifyGraph axisEnab(left)={0.5,1},axisEnab(left2)={0,0.5},swapXY=1
Label left "Signal"
Label left2 "Error"
Label bottom "Position"
EndMacro
"""
    graph = parse_recreation(text)[0][0]
    figure = Figure()
    render_graph(figure, document, graph)
    assert len(figure.axes) == 2
    assert len(figure.axes[0].lines) == 1
    assert len(figure.axes[1].lines) == 1
    assert figure.axes[0].lines[0].get_xdata().tolist() == document.wave("y").data.tolist()  # type: ignore[union-attr]


def test_view_edit_uses_workspace_undo_and_keeps_source_immutable(qtbot: object) -> None:
    document = _document()
    owner = MainWindow()
    qtbot.addWidget(owner)  # type: ignore[attr-defined]
    owner.state.pxp_documents[document.source_id] = document
    owner.state.pxp_view_states[document.source_id] = {}
    owner.undo_history.reset(clean=True)
    viewer = PxpViewerWindow(owner=owner)
    qtbot.addWidget(viewer)  # type: ignore[attr-defined]
    viewer.bind_document(document)
    graph = document.graphs[0]
    viewer._change(graph, "traces", "y", "visible", False)
    assert owner.state.pxp_view_states[document.source_id]["graphs"]["XY"]["traces"]["y"]["visible"] is False
    assert owner.undo_stack.canUndo()
    owner.undo_stack.undo()
    assert "graphs" not in owner.state.pxp_view_states[document.source_id]
    assert document.source_bytes == b"synthetic source only"


def test_igor_plot_geometry_axis_ticks_fonts_and_mirror() -> None:
    document = _document()
    recreation = r'''Window Styled() : Graph
Display y vs x
ModifyGraph width=198.425,height=340.157,margin(right)=56
ModifyGraph tick=2,mirror=1,axThick=1.5,btLen=5,stLen=2,font="Arial",fSize=14
ModifyGraph manTick(left)={0,2,0,1},manMinor(left)={4,5}
ModifyGraph manTick(bottom)={0,4,0,0},manMinor(bottom)={1,5}
ModifyGraph axRGB=(48059,48059,48059),tlblRGB(left)=(65535,0,0)
Label left "\\Z16Signal"
SetAxis left 0,6
EndMacro'''
    graph = parse_recreation(recreation)[0][0]
    assert graph.status == "Fully supported"
    assert graph.style.width_points == 198.425
    assert graph.style.height_points == 340.157
    assert dict(graph.style.margins)["right"] == 56
    figure = Figure(figsize=(8, 6))
    FigureCanvasAgg(figure)
    render_graph(figure, document, graph)
    figure.canvas.draw()
    ax = figure.axes[0]
    width, height = figure.get_size_inches()
    assert abs((ax.get_position().height * height) / (ax.get_position().width * width)
               - 340.157 / 198.425) < 0.01
    assert ax.spines["right"].get_visible() and ax.spines["top"].get_visible()
    scale = ax.spines["left"].get_linewidth() / 1.5
    assert scale > 0
    assert isinstance(ax.yaxis.get_major_locator(), MultipleLocator)
    assert isinstance(ax.yaxis.get_minor_locator(), AutoMinorLocator)
    assert ax.yaxis.get_minor_locator().ndivs == 5
    assert abs(float(ax.yaxis.label.get_fontsize()) - 16 * scale) < 1e-8
    assert abs(float(ax.yaxis.get_ticklabels()[0].get_fontsize()) - 14 * scale) < 1e-8


def test_image_appearance_and_aspect_from_recreation() -> None:
    document = _document()
    graph = parse_recreation('''Window Image() : Graph
Display
AppendImage map
ModifyGraph width={Aspect,0.6},height=340.157,tick=3,mirror=2
ModifyImage map ctab={*,1,VioletOrangeYellow,1},log=1,minRGB=NaN,maxRGB=(65535,0,0)
ColorScale/C/N=text0 image=map
EndMacro''')[0][0]
    assert graph.style.width_aspect == 0.6
    assert graph.images[0].color_range == (None, 1)
    assert graph.images[0].min_color == "transparent"
    assert graph.images[0].max_color == (65535, 0, 0)
    figure = Figure(figsize=(8, 6))
    FigureCanvasAgg(figure)
    render_graph(figure, document, graph)
    figure.canvas.draw()
    ax = figure.axes[0]
    assert ax.spines["top"].get_visible() and ax.spines["right"].get_visible()
    assert isinstance(ax.images[0].norm, LogNorm)
    assert ax.images[0].cmap.get_under()[-1] == 0
    assert ax.images[0].cmap.get_over()[0] == 1


def test_styled_snapshot_round_trip_and_old_snapshot_compatibility() -> None:
    document = _document()
    metadata, arrays = encode_document(document)
    restored = decode_document(metadata, arrays, document.source_bytes)
    assert restored.graphs[0].style == document.graphs[0].style
    metadata["pxp_viewer_schema_version"] = 1
    for graph in metadata["graphs"]:
        graph.pop("style")
        for axis in graph["axes"]:
            axis.pop("style")
    old = decode_document(metadata, arrays, document.source_bytes)
    assert old.graphs[0].style.width_points is None


def test_marker_fill_stroke_and_igor_numbering() -> None:
    document = _document()
    graph = parse_recreation('''Window Markers() : Graph
Display y vs x
AppendToGraph err vs x
ModifyGraph mode=3,opaque=1,msize=4,mrkThick=1
ModifyGraph marker(y)=8,rgb(y)=(65535,0,0)
ModifyGraph marker(err)=19,rgb(err)=(0,65535,0),useMrkStrokeRGB(err)=1
ModifyGraph mrkStrokeRGB=(0,0,65535),mrkFillRGB(err)=(65535,0,0)
EndMacro''')[0][0]
    figure = Figure()
    render_graph(figure, document, graph)
    a, b = figure.axes[0].lines
    assert a.get_marker() == b.get_marker() == "o"
    assert a.get_markerfacecolor() == "white"
    assert a.get_markeredgecolor() == (1, 0, 0)  # stroke ignored without useMrkStrokeRGB
    assert b.get_markerfacecolor() == (0, 1, 0)  # filled marker uses rgb, not mrkFillRGB
    assert b.get_markeredgecolor() == (0, 0, 1)
    assert a.get_markersize() == 9
    assert a.get_markeredgewidth() == 1
    hollow = parse_recreation('''Window Hollow() : Graph
Display y vs x
ModifyGraph mode=3,marker=5,mrkFillRGB=(65535,0,0)
EndMacro''')[0][0]
    render_graph(figure, document, hollow)
    assert figure.axes[0].lines[0].get_marker() == "s"
    assert figure.axes[0].lines[0].get_markerfacecolor() == "none"


def test_all_63_geometric_markers_render_without_fallback_errors() -> None:
    figure = Figure(figsize=(9, 4))
    FigureCanvasAgg(figure)
    ax = figure.add_subplot()
    for number in range(63):
        ax.plot(number % 16, number // 16, marker=marker_path(number), markersize=9,
                markerfacecolor="none", markeredgecolor="black", linestyle="none")
    figure.canvas.draw()
    assert marker_path(5) == "s"
    assert marker_path(7) == "D"
    assert isinstance(marker_path(3), MarkerPath)


def test_run_typography_keeps_partial_italics_scripts_sizes_and_colors(tmp_path: Path) -> None:
    raw = r"\F'Arial'\Z16\f02a\f00/\f02a\f00\B0\M (\f01K\f00)"
    runs = text_runs(raw, "Arial", 14)
    assert [(run.text, run.style) for run in runs[:4]] == [("a", 2), ("/", 0), ("a", 2), ("0", 0)]
    assert runs[3].baseline < 0 and runs[3].size < runs[0].size
    assert runs[0].size == 16
    colors = text_runs(r"A\K(65535,0,0)B\S2\M\F'Symbol'μ", "Arial", 12)
    assert colors[1].color == (1, 0, 0)
    assert colors[2].baseline > 0
    assert colors[-1].family == "Arial"
    figure = Figure()
    FigureCanvasAgg(figure)
    ax = figure.add_subplot()
    ax.set_xlabel("old")
    label = IgorText(ax.xaxis.label, raw)
    label.set_figure(figure)
    ax.xaxis.label = label
    figure.canvas.draw()
    assert label.get_position()[0] == 0.5
    assert label.get_window_extent(figure.canvas.get_renderer()).width > 30
    figure.savefig(tmp_path / "styled.svg", bbox_inches="tight")
    assert (tmp_path / "styled.svg").stat().st_size > 500


def test_tick_color_thickness_rotation_and_integer_trimming() -> None:
    document = _document()
    graph = parse_recreation(r'''DefaultFont "Arial"
Window Ticks() : Graph
Display y vs x
ModifyGraph mirror=1,tick=2,axThick=1.5,axRGB=(48059,48059,48059),zapTZ=1
ModifyGraph btThick(left)=2,stThick(left)=0.75,btLen=6,stLen=2
ModifyGraph manTick(bottom)={0,20,0,0},manMinor(bottom)={9,5},ftLen(bottom)=4
ModifyGraph lblRot(left)=10,fStyle(left)=2,minor(left)=1
Label left "Value"
SetAxis bottom 0,60
EndMacro''')[0][0]
    figure = Figure()
    FigureCanvasAgg(figure)
    render_graph(figure, document, graph)
    figure.canvas.draw()
    ax = figure.axes[0]
    assert ax.yaxis.get_major_ticks()[0].tick1line.get_markeredgewidth() == 2
    assert ax.yaxis.get_minor_ticks()[0].tick1line.get_markeredgewidth() == 0.75
    assert ax.xaxis.get_major_ticks()[0].tick1line.get_color() == pytest.approx((48059 / 65535,) * 3)
    assert ax.xaxis.get_major_formatter()(20, 0) == "20"
    assert ax.yaxis.label.get_rotation() == 100
    assert ax.yaxis.get_ticklabels()[0].get_fontstyle() == "italic"
    lengths = {tick.tick1line.get_markersize() for tick in ax.xaxis.get_minor_ticks()}
    assert lengths == {2, 4}


def test_swapped_plot_geometry_and_annotation_styles_survive_snapshot() -> None:
    document = _document()
    graph = parse_recreation(r'''Window Swap() : Graph
Display /W=(0,0,450,310) y vs x
ModifyGraph width={Aspect,0.6},height=340.157,mode=3,marker=8,swapXY=1
Legend/C/N=text0/F=0/B=1/A=MC/X=9/Y=33 "\Z16\s(y) Synthetic"
EndMacro''')[0][0]
    assert graph.style.window_size == (450, 310)
    figure = Figure(figsize=(8, 6))
    FigureCanvasAgg(figure)
    render_graph(figure, document, graph)
    figure.canvas.draw()
    ax = figure.axes[0]
    box = ax.get_window_extent()
    assert box.height / box.width == pytest.approx(0.6)
    annotations = [a for a in ax.artists if hasattr(a, "text")]
    assert len(annotations) == 1
    assert annotations[0].style.frame == 0
    assert graph.legend_style.x_percent == 9 and graph.legend_style.transparent
    from dataclasses import replace
    document = replace(document, graphs=(graph,))
    metadata, arrays = encode_document(document)
    restored = decode_document(json.loads(json.dumps(metadata)), arrays)
    assert restored.graphs[0].legend_style == graph.legend_style
    metadata["pxp_viewer_schema_version"] = 2
    assert decode_document(metadata, arrays).graphs[0].swap_xy


def test_image_linear_colorscale_inside_log_image_and_configured_minimum() -> None:
    document = _document()
    graph = parse_recreation(r'''Window Map() : Graph
Display
AppendImage map
ModifyImage map ctab={0.17,12,VioletOrangeYellow,0},log=1
ColorScale/C/N=text0/F=0/G=(65535,65535,65535)/B=1/A=MC/X=34/Y=-9
ColorScale/C/N=text0 image=map
AppendText "\Z14Intensity"
EndMacro''')[0][0]
    figure = Figure()
    FigureCanvasAgg(figure)
    render_graph(figure, document, graph)
    figure.canvas.draw()
    ax, bar = figure.axes
    assert ax.images[0].norm.vmin == 0.17
    assert bar.get_yscale() == "linear"
    assert ax.get_position().contains(*bar.get_position().get_points()[0])
    assert bar.yaxis.label.get_color() == (1, 1, 1)
    assert graph.images[0].colorbar_style.x_percent == 34


@pytest.mark.parametrize("mode", range(9))
def test_basic_trace_display_modes(mode: int) -> None:
    document = _document()
    graph = parse_recreation(f'''Window Modes() : Graph
Display y vs x
ModifyGraph mode={mode},marker=8
EndMacro''')[0][0]
    figure = Figure()
    FigureCanvasAgg(figure)
    render_graph(figure, document, graph)
    figure.canvas.draw()
