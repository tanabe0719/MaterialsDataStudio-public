from __future__ import annotations

import importlib.util
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QDialog, QFileDialog

from materials_data_studio.core.models import PlotStyle
from materials_data_studio.plotting.plot_spec import CurveSpec, PlotSpec

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None
    or importlib.util.find_spec("pyqtgraph") is None,
    reason="Qt plotting dependencies are not installed",
)


def test_render_adds_curve_without_clip_initialization_failure(qtbot) -> None:  # type: ignore[no-untyped-def]
    import pyqtgraph as pg

    from materials_data_studio.plotting.pyqtgraph_backend import render_plot

    widget = pg.PlotWidget(background="w")
    qtbot.addWidget(widget)
    x = np.linspace(10.0, 80.0, 1000)
    y = 100.0 + 50.0 * np.sin(x)
    spec = PlotSpec(
        "2θ (deg)",
        "Intensity",
        [CurveSpec(x, y, "XRD", PlotStyle())],
    )

    render_plot(widget, spec)

    items = widget.listDataItems()
    assert len(items) == 1
    np.testing.assert_allclose(items[0].xData, x)
    np.testing.assert_allclose(items[0].yData, y)
    assert items[0].opts["clipToView"]


def test_live_data_update_reuses_existing_curve_item(qtbot) -> None:  # type: ignore[no-untyped-def]
    import pyqtgraph as pg

    from materials_data_studio.plotting.pyqtgraph_backend import (
        render_plot,
        update_rendered_plot_data,
    )

    widget = pg.PlotWidget(background="w")
    qtbot.addWidget(widget)
    original = PlotSpec(
        "x", "y", [CurveSpec(np.array([1.0, 2.0]), np.array([3.0, 4.0]), "phase")]
    )
    render_plot(widget, original)
    item = widget.listDataItems()[0]
    changed = PlotSpec(
        "x", "y", [CurveSpec(np.array([1.5, 2.5]), np.array([8.0, 6.0]), "phase")]
    )
    assert update_rendered_plot_data(widget, changed)
    assert widget.listDataItems()[0] is item
    np.testing.assert_allclose(item.xData, [1.5, 2.5])
    np.testing.assert_allclose(item.yData, [8.0, 6.0])


def test_unchanged_immutable_arrays_skip_upload_but_mutable_arrays_refresh(qtbot, monkeypatch):
    import pyqtgraph as pg

    from materials_data_studio.plotting.pyqtgraph_backend import (
        render_plot,
        update_rendered_plot_data,
    )
    widget = pg.PlotWidget()
    qtbot.addWidget(widget)
    x, y = np.arange(1000.), np.ones(1000)
    x.setflags(write=False)
    y.setflags(write=False)
    spec = PlotSpec("x", "y", [CurveSpec(x, y, "Observation")])
    render_plot(widget, spec)
    item = widget.listDataItems()[0]
    upload = item.setData
    calls = []
    def tracked(*args, **kwargs):
        calls.append(True)
        return upload(*args, **kwargs)
    monkeypatch.setattr(item, "setData", tracked)
    assert update_rendered_plot_data(widget, spec)
    assert not calls
    mutable = np.full(1000, 2.)
    changed = PlotSpec("x", "y", [CurveSpec(x, mutable, "Observation")])
    assert update_rendered_plot_data(widget, changed)
    mutable[:] = 3
    assert update_rendered_plot_data(widget, changed)
    assert len(calls) == 2
    assert np.array_equal(item.yData, mutable)


def test_render_enables_logarithmic_y_mode(qtbot) -> None:  # type: ignore[no-untyped-def]
    import pyqtgraph as pg

    from materials_data_studio.plotting.pyqtgraph_backend import render_plot

    widget = pg.PlotWidget(background="w")
    qtbot.addWidget(widget)
    spec = PlotSpec(
        "2θ (deg)",
        "Intensity",
        [
            CurveSpec(
                np.array([10.0, 20.0, 30.0]),
                np.array([1.0, 10.0, 100.0]),
                "XRD",
            )
        ],
        y_log_scale=True,
    )

    render_plot(widget, spec)

    assert widget.listDataItems()[0].opts["logMode"] == [False, True]


def test_descending_fc_curve_remains_visible_after_zoom(qtbot) -> None:  # type: ignore[no-untyped-def]
    import pyqtgraph as pg

    from materials_data_studio.plotting.pyqtgraph_backend import render_plot

    widget = pg.PlotWidget(background="w")
    qtbot.addWidget(widget)
    temperature = np.linspace(300.0, 2.0, 5000)
    magnetization = 1.0 / (temperature + 10.0)
    render_plot(
        widget,
        PlotSpec(
            "Temperature (K)",
            "Magnetization",
            [CurveSpec(temperature, magnetization, "FC", PlotStyle())],
        ),
    )

    item = widget.listDataItems()[0]
    assert not item.opts["clipToView"]
    assert not item.opts["autoDownsample"]
    widget.setXRange(10.0, 20.0, padding=0)
    display_x, display_y = item.getData()
    assert display_x is not None and display_y is not None
    assert len(display_x) == len(temperature)
    assert np.all(np.isfinite(display_y))


def test_legend_context_action_controls_plot_option(qtbot) -> None:  # type: ignore[no-untyped-def]
    from materials_data_studio.plotting.pyqtgraph_backend import (
        create_plot_widget,
        install_legend_toggle,
    )

    widget = create_plot_widget()
    qtbot.addWidget(widget)
    changes: list[bool] = []
    action = install_legend_toggle(widget, changes.append)
    assert action.text() == "Legends"
    assert action in widget.getPlotItem().getMenu().actions()
    action.setChecked(False)
    assert changes == [False]


def test_render_hides_existing_legend_when_disabled(qtbot) -> None:  # type: ignore[no-untyped-def]
    from materials_data_studio.plotting.pyqtgraph_backend import (
        create_plot_widget,
        render_plot,
    )

    widget = create_plot_widget()
    qtbot.addWidget(widget)
    curve = CurveSpec(np.array([1.0, 2.0]), np.array([3.0, 4.0]), "sample")
    render_plot(widget, PlotSpec("x", "y", [curve], legend_enabled=True))
    legend = widget.getPlotItem().legend
    assert legend is not None and legend.isVisible()
    render_plot(widget, PlotSpec("x", "y", [curve], legend_enabled=False))
    assert not legend.isVisible()


def test_three_button_right_drag_uses_rectangle_zoom(qtbot) -> None:  # type: ignore[no-untyped-def]
    import pyqtgraph as pg

    from materials_data_studio.plotting.pyqtgraph_backend import (
        RightDragZoomViewBox,
    )

    class TrackingViewBox(RightDragZoomViewBox):
        selected = None

        def showAxRect(self, rectangle) -> None:  # type: ignore[no-untyped-def]
            self.selected = rectangle

    class FinishedRightDrag:
        accepted = False

        def button(self):  # type: ignore[no-untyped-def]
            return Qt.MouseButton.RightButton

        def accept(self) -> None:
            self.accepted = True

        def isFinish(self) -> bool:
            return True

        def buttonDownPos(self, _button=None):  # type: ignore[no-untyped-def]
            return QPointF(10, 10)

        def pos(self) -> QPointF:
            return QPointF(80, 60)

    view = TrackingViewBox()
    widget = pg.PlotWidget(viewBox=view)
    qtbot.addWidget(widget)
    view.setMouseMode(pg.ViewBox.PanMode)
    event = FinishedRightDrag()
    view.mouseDragEvent(event)
    assert event.accepted
    assert view.selected is not None
    assert view.selected.width() > 0
    assert view.selected.height() > 0


def test_view_all_enables_auto_scale_for_both_axes(qtbot) -> None:  # type: ignore[no-untyped-def]
    from materials_data_studio.plotting.pyqtgraph_backend import create_plot_widget

    widget = create_plot_widget()
    qtbot.addWidget(widget)
    view = widget.getViewBox()
    view.disableAutoRange()
    view.getMenu(None).viewAll.trigger()
    assert view.state["autoRange"] == [1.0, 1.0]


def test_plot_theme_shows_top_right_ticks_and_applies_preferences(qtbot) -> None:  # type: ignore[no-untyped-def]
    from materials_data_studio.plotting.appearance import (
        DEFAULT_PLOT_APPEARANCE,
        set_active_plot_appearance,
    )
    from materials_data_studio.plotting.pyqtgraph_backend import (
        apply_plot_theme,
        create_plot_widget,
    )

    custom = replace(
        DEFAULT_PLOT_APPEARANCE,
        axis_label_font_size=14,
        tick_label_font_size=12,
        legend_font_size=11,
        frame_width=2.25,
        tick_width=1.75,
        tick_length=8.0,
        show_minor_ticks=True,
        x_major_tick_interval=10.0,
        x_minor_tick_interval=2.0,
        light=replace(
            DEFAULT_PLOT_APPEARANCE.light,
            axis_label="#112233",
            tick_label="#223344",
            legend_text="#334455",
            frame="#445566",
            tick="#556677",
        ),
    )
    set_active_plot_appearance(custom)
    widget = create_plot_widget()
    qtbot.addWidget(widget)
    apply_plot_theme(widget, "light")
    plot_item = widget.getPlotItem()
    assert plot_item.getAxis("top").isVisible()
    assert plot_item.getAxis("right").isVisible()
    assert plot_item.getAxis("right").minimumWidth() >= 10
    assert plot_item.getAxis("top").minimumHeight() >= 10
    assert plot_item.layout.getContentsMargins()[2] >= 8
    assert plot_item.getAxis("bottom").pen().widthF() == pytest.approx(2.25)
    assert plot_item.getAxis("bottom").tickPen().widthF() == pytest.approx(1.75)
    assert plot_item.getAxis("bottom").style["tickLength"] == -8
    assert plot_item.getAxis("bottom").style["tickFont"].pointSize() == 12
    apply_plot_theme(widget, "dark")
    assert (
        plot_item.getViewBox().background.brush().color().name().upper()
        == DEFAULT_PLOT_APPEARANCE.dark.background.upper()
    )
    set_active_plot_appearance(DEFAULT_PLOT_APPEARANCE)


@pytest.mark.parametrize("suffix,selected_filter", [(".svg", "SVG (*.svg)"), (".pdf", "PDF (*.pdf)")])
def test_context_export_writes_vector_formats(
    qtbot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, suffix: str, selected_filter: str
) -> None:  # type: ignore[no-untyped-def]
    from materials_data_studio.plotting.pyqtgraph_backend import (
        InteractiveExportDialog,
        create_plot_widget,
        render_plot,
    )

    widget = create_plot_widget()
    qtbot.addWidget(widget)
    render_plot(
        widget,
        PlotSpec(
            "x",
            "y",
            [CurveSpec(np.array([1.0, 2.0]), np.array([3.0, 4.0]), "sample")],
        ),
    )
    target = tmp_path / f"interactive{suffix}"
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(target.with_suffix("")), selected_filter),
    )
    monkeypatch.setattr(
        InteractiveExportDialog,
        "exec",
        lambda _dialog: QDialog.DialogCode.Accepted,
    )
    monkeypatch.setattr(InteractiveExportDialog, "export_kind", lambda _dialog: "vector")
    monkeypatch.setattr(
        InteractiveExportDialog,
        "selected_format",
        lambda _dialog: suffix.removeprefix("."),
    )
    monkeypatch.setattr(
        InteractiveExportDialog,
        "image_options",
        lambda _dialog: (14.0, 10.0, 300, True),
    )
    action = widget.scene().contextMenu[0]
    assert action.text() == "Export..."
    action.trigger()
    assert target.stat().st_size > 100


def test_context_export_writes_selected_curves_as_tsv(
    qtbot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    from materials_data_studio.plotting.pyqtgraph_backend import (
        InteractiveExportDialog,
        create_plot_widget,
        render_plot,
    )

    widget = create_plot_widget()
    qtbot.addWidget(widget)
    render_plot(
        widget,
        PlotSpec(
            "x",
            "y",
            [
                CurveSpec(np.array([1.0, 2.0]), np.array([3.0, 4.0]), "first"),
                CurveSpec(np.array([5.0]), np.array([6.0]), "second"),
            ],
        ),
    )
    target = tmp_path / "interactive.tsv"
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(target), "TSV (*.tsv)"),
    )
    monkeypatch.setattr(
        InteractiveExportDialog,
        "exec",
        lambda _dialog: QDialog.DialogCode.Accepted,
    )
    monkeypatch.setattr(InteractiveExportDialog, "export_kind", lambda _dialog: "data")
    monkeypatch.setattr(InteractiveExportDialog, "selected_format", lambda _dialog: "tsv")
    monkeypatch.setattr(
        InteractiveExportDialog,
        "selected_curve_indices",
        lambda _dialog: [1],
    )
    widget.scene().contextMenu[0].trigger()
    text = target.read_text(encoding="utf-8")
    assert text.splitlines()[0] == "second_x\tsecond_y"
    assert "first" not in text


def test_context_export_writes_igor_h5xp(
    qtbot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    import h5py

    from materials_data_studio.plotting.pyqtgraph_backend import (
        InteractiveExportDialog,
        create_plot_widget,
        render_plot,
    )

    widget = create_plot_widget()
    qtbot.addWidget(widget)
    widget.setProperty("materialsIgorModule", "XRD")
    render_plot(
        widget,
        PlotSpec(
            "2θ (deg)",
            "Intensity (a.u.)",
            [CurveSpec(np.array([10.0, 20.0]), np.array([3.0, 4.0]), "sample")],
        ),
    )
    target = tmp_path / "interactive.h5xp"
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(target.with_suffix("")), "Igor HDF5"),
    )
    monkeypatch.setattr(
        InteractiveExportDialog,
        "exec",
        lambda _dialog: QDialog.DialogCode.Accepted,
    )
    monkeypatch.setattr(InteractiveExportDialog, "export_kind", lambda _dialog: "h5xp")
    monkeypatch.setattr(InteractiveExportDialog, "selected_format", lambda _dialog: "h5xp")

    widget.scene().contextMenu[0].trigger()

    with h5py.File(target, "r") as handle:
        assert "Packed Data/MDS/XRD" in handle
        procedures = handle["Recreation/Recreation Procedures"].asstr()[()]
        assert "ModifyGraph tick=2,mirror=1,tickUnit=1" in procedures
