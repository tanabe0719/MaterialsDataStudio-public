"""Attached reflection text commits the same location shown during dragging."""
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from materials_data_studio.core.models import PlotStyle
from materials_data_studio.plotting.plot_spec import CurveSpec, PlotSpec
from materials_data_studio.tools.graph_document import load_graph_document, save_graph_document
from materials_data_studio.tools.graph_window import GraphEditorWindow
from materials_data_studio.tools.graph_workflows import from_plot_spec


@pytest.mark.parametrize("secondary", [False, True])
@pytest.mark.parametrize("logarithmic,angle", [(False, 90.), (True, 90.), (False, 35.)])
def test_reflection_drag_keeps_drop_location_and_attachment(qtbot, tmp_path, logarithmic, angle, secondary):
    document = from_plot_spec(PlotSpec("Angle", "Intensity", [CurveSpec(
        np.array([30., 50.]), np.array([60., 100.]), "Synthetic markers",
        PlotStyle(marker="none", line_style="none"), point_labels=("002", "110"), label_angle=angle)],
        x_range=(10., 90.), y_range=(10., 200.), y_log_scale=logarithmic))
    if secondary:
        graph = document.current.graphs[0]
        document.current.graphs = (replace(graph, traces=(replace(graph.traces[0], axis="right"),), axes=(*graph.axes, replace(graph.axes[-1], name="right"))),)
    window = GraphEditorWindow()
    window._discard_approved = True
    qtbot.addWidget(window)
    window.resize(1200, 850)
    window.show()
    window.bind_graph_document(document)
    window.select_target("annotation", "MDSIndex0")
    qtbot.wait(20)
    def frame():
        return next(artist for artist, target in window.artist_targets.items() if target == ("annotation", "MDSIndex0"))
    def position():
        window.canvas.draw()
        text = frame().text
        return text.get_transform().transform(text.get_position())
    editor = window.editor
    assert editor is not None
    original = position()
    for dx, dy in ((35., 18.), (-12., 24.)):
        before = position()
        box = frame().get_window_extent(window.canvas.get_renderer())
        x, y = box.x0 + box.width / 2, box.y0 + box.height / 2
        window.pointer_press(SimpleNamespace(button=1, dblclick=False, x=x, y=y, inaxes=None))
        assert window._drag is not None and window._drag["target"] == ("annotation", "MDSIndex0")
        window.pointer_move(SimpleNamespace(x=x+dx, y=y+dy, key=""))
        preview = frame().text.get_transform().transform(frame().text.get_position())
        np.testing.assert_allclose(preview, np.add(before, (dx, dy)), atol=1.)
        window.pointer_release(SimpleNamespace())
        np.testing.assert_allclose(position(), preview, atol=1.)
    final = position()
    editor.history.undo()
    editor.history.undo()
    np.testing.assert_allclose(position(), original, atol=1.)
    editor.history.redo()
    editor.history.redo()
    np.testing.assert_allclose(position(), final, atol=1.)
    path = tmp_path / "dragged.mdsg"
    save_graph_document(path, editor.document)
    annotation = load_graph_document(path).graph("MDS_Plot").annotations[0]
    assert annotation.trace == "curve1" and annotation.point == 0
