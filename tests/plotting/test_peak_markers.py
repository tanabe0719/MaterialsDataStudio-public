"""Marker geometry and reflection text remain consistent on screen and export."""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
import pytest
from PySide6.QtCore import QPointF

from materials_data_studio.core.models import PlotStyle
from materials_data_studio.plotting.marker_symbols import mpl_symbol, qt_symbol
from materials_data_studio.plotting.matplotlib_backend import build_figure
from materials_data_studio.plotting.plot_spec import CurveSpec, PlotSpec
from materials_data_studio.plotting.pyqtgraph_backend import create_plot_widget, render_plot


def test_triangles_and_dagger_geometry():
    from pyqtgraph.graphicsItems.ScatterPlotItem import Symbols
    # Qt scene Y increases downwards: the isolated triangle tip must point as named.
    for marker, direction in (("^", -1), ("v", 1)):
        path = Symbols[qt_symbol(marker)]
        vertices = [(path.elementAt(i).x, path.elementAt(i).y) for i in range(path.elementCount())]
        assert (0., .5 * direction) in vertices
        assert mpl_symbol(marker) == marker
    assert qt_symbol("dagger").boundingRect().height() == 1
    assert mpl_symbol("dagger").vertices[:, 1].max() == .5


@pytest.mark.parametrize("angle", [90., 0., -45.])
def test_labels_stay_centered_above_markers_on_zoom_and_log(qtbot, angle):
    widget = create_plot_widget()
    qtbot.addWidget(widget)
    widget.resize(800, 600)
    widget.show()
    curve = CurveSpec(np.array([30., 50.]), np.array([100., 80.]), "phase", PlotStyle(marker="dagger", marker_size=12, line_style="none"),
                      point_labels=("NaCl, 002", "110"), label_angle=angle)
    spec = PlotSpec("2θ", "Intensity", [curve], x_log_scale=True, y_log_scale=True)
    render_plot(widget, spec)
    qtbot.wait(20)
    for bounds in (((1., 2.), (1., 3.)), ((1.3, 1.9), (1.5, 2.5))):
        widget.getViewBox().setRange(xRange=bounds[0], yRange=bounds[1], padding=0)
        qtbot.wait(10)
        for label in widget._materials_point_labels:
            label.updateTextPos()
            anchor = label.mapToScene(QPointF(0, 0))
            box = label.textItem.mapRectToScene(label.textItem.boundingRect())
            assert box.center().x() == pytest.approx(anchor.x(), abs=.1)
            assert box.bottom() == pytest.approx(anchor.y() - 10, abs=.1)
    assert len(widget._materials_point_labels) == 2
    assert isinstance(widget._materials_curve_items[0], pg.PlotDataItem)
    figure = build_figure(spec)
    assert [text.get_text() for text in figure.axes[0].texts] == ["NaCl, 002", "110"]
    assert figure.axes[0].texts[0].get_rotation() == angle % 360


def test_per_point_sizes_are_markers_in_both_renderers(qtbot):
    widget = create_plot_widget()
    qtbot.addWidget(widget)
    curve = CurveSpec(np.array([30., 31.]), np.array([0., 0.]), "ticks", PlotStyle(marker="|", line_style="none"), marker_sizes=np.array([24., 12.]))
    spec = PlotSpec("x", "y", [curve])
    render_plot(widget, spec)
    item = widget._materials_curve_items[0]
    assert item.opts["pen"] is None
    assert item.opts["symbol"] == "|"
    assert item.opts["symbolSize"] == pytest.approx([24, 12])
    figure = build_figure(spec)
    assert figure.axes[0].collections[0].get_sizes() == pytest.approx([24**2, 12**2])
    assert figure.axes[0].collections[0].get_edgecolors()[0, 3] == 1
