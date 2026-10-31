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


@pytest.mark.parametrize("angle", [90., 0., -45., 45., 135.])
def test_label_start_stays_above_markers_on_zoom_and_log(qtbot, angle):
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
            bounds = label.textItem.boundingRect()
            start = label.textItem.mapToScene(QPointF(bounds.left(), bounds.center().y()))
            assert start.x() == pytest.approx(anchor.x(), abs=.1)
            assert start.y() == pytest.approx(anchor.y() - 10, abs=.1)
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


@pytest.mark.parametrize("count", [52, 349])
def test_variable_size_ticks_survive_zoom_style_log_and_point_count_changes(qtbot, count):
    from dataclasses import replace
    widget = create_plot_widget()
    qtbot.addWidget(widget)
    widget.resize(800, 600)
    widget.show()
    positions = np.linspace(10, 100, count)
    curve = CurveSpec(positions, np.ones(count), "ticks", PlotStyle(marker="|", line_style="none", line_width=.8), marker_sizes=np.resize([20., 10.], count))
    spec = PlotSpec("x", "y", [curve])
    render_plot(widget, spec)
    item = widget._materials_curve_items[0]
    for bounds in ((20, 40), (32, 33), (0, 120)):
        widget.getViewBox().setRange(xRange=bounds, padding=0)
        qtbot.wait(10)
        curve.style = replace(curve.style, marker_size=30, color="#123456")
        render_plot(widget, spec)
        assert widget._materials_curve_items[0] is item
        assert len(item.scatter.data) == count
        assert not item.opts["clipToView"] and not item.opts["autoDownsample"]
    curve.x, curve.y, curve.marker_sizes = positions[:4], np.ones(4), np.array([20., 10., 20., 10.])
    render_plot(widget, spec)
    assert len(item.scatter.data) == 4
    spec.x_log_scale = spec.y_log_scale = True
    render_plot(widget, spec)
    widget.getViewBox().setRange(xRange=(1, 1.5), padding=0)
    qtbot.wait(10)
    assert len(widget._materials_curve_items[0].scatter.data) == 4


def test_labels_work_with_no_marker_and_reuse_text_objects(qtbot):
    widget = create_plot_widget()
    qtbot.addWidget(widget)
    curve = CurveSpec(np.array([30.]), np.array([100.]), "phase", PlotStyle(marker="none", line_style="none"), point_labels=("002",))
    spec = PlotSpec("x", "y", [curve])
    render_plot(widget, spec)
    label = widget._materials_point_labels[0]
    assert label.gap == 4
    assert label.textItem.font().family() == "Arial"
    curve.label_angle = 30
    render_plot(widget, spec)
    assert widget._materials_point_labels == [label]
    assert label.toPlainText() == "002"
    figure = build_figure(spec)
    assert figure.axes[0].texts[0].get_rotation_mode() == "anchor"
    curve.point_labels = ()
    render_plot(widget, spec)
    assert not widget._materials_point_labels
