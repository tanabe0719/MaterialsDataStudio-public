"""Synthetic regressions for folder-scoped traces and responsive text geometry."""
from dataclasses import replace

import numpy as np
import pytest
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.text import Text

from materials_data_studio.tools.pxp_model import PxpAnnotationStyle, PxpDocument, PxpWave
from materials_data_studio.tools.pxp_recreation import parse_recreation
from materials_data_studio.tools.pxp_render import (
    _annotation_anchor,
    annotation_offsets,
    render_graph,
)
from materials_data_studio.tools.pxp_snapshot import decode_document, encode_document
from materials_data_studio.tools.pxp_text import IgorText


def wave(path: str, values: list[float]) -> PxpWave:
    data = np.array(values)
    data.setflags(write=False)
    return PxpWave(path, path, path.rsplit(":", 1)[-1], data.dtype.str, data.shape, data,
                   ((0., 1.),), ("",), "", "")


def source() -> PxpDocument:
    return PxpDocument("synthetic", "synthetic.pxp", "", 0, "10", "synthetic", "1", waves=(
        wave("root:alpha:x", [1., 2., 3.]), wave("root:beta:x", [10., 20., 30.]),
        wave("root:alpha:y", [4., 5., 6.]), wave("root:beta:y", [7., 8., 9.]),
    ), status="FULL")


def test_folder_scoped_traces_render_their_own_x_and_y() -> None:
    graphs, _ = parse_recreation('''SetDataFolder root:beta:
Window G() : Graph
String saved=GetDataFolder(1)
SetDataFolder root:alpha:
Display y vs x
SetDataFolder saved
AppendToGraph y vs x
EndMacro
Window H() : Graph
Display y vs x
EndMacro''')
    assert [(t.y_wave, t.x_wave) for t in graphs[0].traces] == [
        ("root:alpha:y", "root:alpha:x"), ("root:beta:y", "root:beta:x")]
    assert [t.name for t in graphs[0].traces] == ["y", "y#1"]
    assert graphs[1].traces[0].y_wave == "root:beta:y"
    assert not any(i.scope in {"String", "SetDataFolder"} for i in graphs[0].issues)
    figure = Figure()
    FigureCanvasAgg(figure)
    render_graph(figure, source(), graphs[0])
    assert len(figure.axes[0].lines) == 2
    np.testing.assert_array_equal(figure.axes[0].lines[0].get_xdata(), [1., 2., 3.])
    np.testing.assert_array_equal(figure.axes[0].lines[1].get_xdata(), [10., 20., 30.])


def test_unresolved_folder_is_not_executed_or_bound_to_root() -> None:
    graph = parse_recreation('''Window G() : Graph
String unsafe=ExternalFunction()
SetDataFolder unsafe
Display y vs x
EndMacro''')[0][0]
    document = replace(source(), waves=(wave("root:x", [1.]), wave("root:y", [2.])))
    assert document.wave(graph.traces[0].y_wave) is None
    assert any(i.scope == "SetDataFolder" for i in graph.issues)


@pytest.mark.parametrize("error_command", [
    "ErrorBars 'signal one' Y,wave=(lo,hi)",
    "ErrorBars 'signal one',Y,wave=(lo,hi)",
    "ErrorBars 'signal one' XY,wave=('x low','x high'),wave=(lo,hi)",
])
def test_folder_scoped_images_error_bars_and_user_ticks(error_command: str) -> None:
    graph = parse_recreation(f'''Window G() : Graph
SetDataFolder root:'scan one':
Display 'signal one' vs x
{error_command}
ModifyGraph userTicks(left)={{positions,labels}}
AppendImage image
EndMacro''')[0][0]
    assert graph.traces[0].y_wave == "root:scan one:signal one"
    assert graph.traces[0].error_low == "root:scan one:lo"
    assert graph.traces[0].error_high == "root:scan one:hi"
    if "XY" in error_command:
        assert graph.traces[0].x_error_low == "root:scan one:x low"
        assert graph.traces[0].x_error_high == "root:scan one:x high"
    assert graph.images[0].wave == "root:scan one:image"
    assert next(a for a in graph.axes if a.name == "left").style.user_ticks == (
        "root:scan one:positions", "root:scan one:labels")


def test_wave_index_preserves_ambiguity_and_invalidates_on_revision() -> None:
    document = source()
    assert document.wave("x") is None
    assert document.wave("root:alpha:x") is document.waves[0]
    changed = replace(document, waves=(wave("root:x", [99.]), *document.waves))
    assert changed.wave("x") is changed.waves[0]
    assert document.wave("x") is None
    assert replace(changed, waves=()).wave("x") is None


@pytest.mark.parametrize("anchor", ["LT", "MT", "RT", "LC", "MC", "RC", "LB", "MB", "RB"])
@pytest.mark.parametrize("mode", ["native", "legacy"])
def test_annotation_drag_coordinates_roundtrip(anchor: str, mode: str) -> None:
    style = PxpAnnotationStyle(anchor=anchor, offset_mode=mode)
    x, y = annotation_offsets(style, .32, .67)
    actual = _annotation_anchor(replace(style, x_percent=x, y_percent=y))
    assert actual[:2] == pytest.approx((.32, .67))


def test_native_upper_right_offsets_move_inward_and_old_documents_stay_put() -> None:
    native = PxpAnnotationStyle(anchor="RT", x_percent=5, y_percent=10, offset_mode="native")
    assert _annotation_anchor(native)[:2] == pytest.approx((.95, .9))
    graph = parse_recreation('''Window G() : Graph
Display /W=(0,0,400,220) y
Legend/C/N=legend0/J "Signal"
EndMacro''')[0][0]
    assert graph.annotations[0].style.anchor == "RT"
    from materials_data_studio.tools.graph_typography import annotation_font
    assert graph.annotations[0].style.font_size == 0
    assert graph.annotations[0].style.inherit_graph_font
    assert annotation_font(graph, graph.annotations[0].style).size == 10
    original = replace(source(), graphs=(replace(graph, annotations=(replace(graph.annotations[0], style=native),)),))
    metadata, payload = encode_document(original)
    assert decode_document(metadata, payload).graphs == original.graphs
    metadata["pxp_viewer_schema_version"] = 8
    del metadata["graphs"][0]["annotations"][0]["style"]["offset_mode"]
    legacy = decode_document(metadata, payload).graphs[0].annotations[0].style
    assert _annotation_anchor(legacy)[:2] == pytest.approx((1.05, 1.1))


def test_text_metrics_reuse_survives_motion_but_invalidates_font_and_renderer() -> None:
    figure = Figure(figsize=(6, 4), dpi=100)
    canvas = FigureCanvasAgg(figure)
    text = IgorText(Text(.2, .3, fontsize=12), "One\nTwo")
    text.set_figure(figure)
    renderer = canvas.get_renderer()
    before = text.get_window_extent(renderer)
    metrics = text._metrics_value
    text.set_position((20.2, 10.3))
    after = text.get_window_extent(renderer)
    assert text._metrics_value is metrics
    assert after.x0 - before.x0 == pytest.approx(20)
    assert after.y0 - before.y0 == pytest.approx(10)
    text.set_fontsize(20)
    assert text.get_window_extent(renderer).height > before.height
    assert text._metrics_value is not metrics
    changed = text._metrics_value
    figure.set_dpi(160)
    text.get_window_extent(canvas.get_renderer())
    assert text._metrics_value is not changed
    text.raw = "Changed"
    text.set_rotation(90)
    assert text.get_window_extent(canvas.get_renderer()).height > 0
