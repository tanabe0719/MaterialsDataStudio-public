"""Interactive PyQtGraph rendering from PlotSpec."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import numpy as np
import pyqtgraph as pg
from pyqtgraph.graphicsItems.ViewBox.ViewBox import ViewBox
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QAction, QColor, QFont
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

from materials_data_studio.plotting.appearance import active_plot_appearance
from materials_data_studio.plotting.igor_export import graph_export_state_from_plot_spec
from materials_data_studio.plotting.interactive_export import (
    InteractiveExportDialog,
    export_plot_curves,
)
from materials_data_studio.plotting.marker_symbols import qt_symbol
from materials_data_studio.plotting.matplotlib_backend import export_figure
from materials_data_studio.plotting.plot_spec import CurveSpec, PlotSpec

_PEN_STYLES = {
    "solid": Qt.PenStyle.SolidLine,
    "-": Qt.PenStyle.SolidLine,
    "dash": Qt.PenStyle.DashLine,
    "--": Qt.PenStyle.DashLine,
    "dot": Qt.PenStyle.DotLine,
    ":": Qt.PenStyle.DotLine,
    "dash_dot": Qt.PenStyle.DashDotLine,
    "-.": Qt.PenStyle.DashDotLine,
}


class PeakTextItem(pg.TextItem):
    """Keep the start of rotated text anchored just above its marker."""

    def __init__(self, *args, gap=8., **kwargs):
        self.gap = gap
        self._materials_label_options: tuple[Any, ...] | None = None
        super().__init__(*args, **kwargs)

    def updateTextPos(self):
        super().updateTextPos()
        anchor = self.mapToScene(QPointF(0, 0))
        bounds = self.textItem.boundingRect()
        start = self.textItem.mapToScene(QPointF(bounds.left(), bounds.center().y()))
        delta = QPointF(anchor.x() - start.x(), anchor.y() - self.gap - start.y())
        local = self.mapFromScene(anchor + delta) - self.mapFromScene(anchor)
        self.textItem.moveBy(local.x(), local.y())

    def dataBounds(self, ax, frac=1., orthoRange=None):
        return [0., 0.]

    def pixelPadding(self):
        bounds = self.textItem.boundingRect()
        return self.gap + float(np.hypot(bounds.width(), bounds.height()))

_SYMBOLS = {
    "o": "o",
    "s": "s",
    "^": "t1",
    "v": "t",
    "d": "d",
    "D": "d",
    "+": "+",
    "x": "x",
}


class ScientificAxisItem(pg.AxisItem):
    """Independent major/minor tick lengths, including minor-only axes."""

    major_length: float = 5.0
    minor_length: float = 3.0

    def generateDrawSpecs(self, p: Any) -> Any:
        specs = super().generateDrawSpecs(p)
        if specs is None:
            return None
        axis_spec, ticks, labels = specs
        baseline = abs(cast(Any, self).style["tickLength"])
        adjusted = []
        for pen, start, end in ticks:
            vector = end - start
            length = float(np.hypot(vector.x(), vector.y()))
            major = abs(length - baseline) < 0.01
            desired = self.major_length if major else self.minor_length
            if length > 0 and desired > 0:
                adjusted.append((pen, start, start + vector * (desired / length)))
        return axis_spec, adjusted, labels


class RightDragZoomViewBox(ViewBox):
    """Keep three-button panning but use right-drag for rectangle zoom."""

    def enableAutoRange(self, axis: Any = None, enable: Any = True, x: Any = None, y: Any = None) -> None:
        super().enableAutoRange(axis=axis, enable=enable, x=x, y=y)
        if self.autoRangeEnabled()[0]:
            # Bounds of a clipped curve describe only its old zoomed display.
            # Rebuild through the public API before asking Auto Scale for bounds.
            for item in getattr(self, "addedItems", ()):
                if isinstance(item, pg.PlotDataItem) and item.opts.get("clipToView"):
                    item.setClipToView(False)
                    item.setClipToView(True)
            self.updateAutoRange()

    def mouseDragEvent(self, event, axis=None) -> None:  # type: ignore[no-untyped-def]
        if (
            axis is None
            and self.state["mouseMode"] == ViewBox.PanMode
            and event.button() == Qt.MouseButton.RightButton
        ):
            event.accept()
            if event.isFinish():
                self.rbScaleBox.hide()
                rectangle = QRectF(
                    event.buttonDownPos(Qt.MouseButton.RightButton),
                    event.pos(),
                ).normalized()
                if rectangle.width() > 0 and rectangle.height() > 0:
                    axis_rectangle = self.childGroup.mapRectFromParent(rectangle)
                    self.showAxRect(axis_rectangle)
                    self.axHistoryPointer += 1
                    self.axHistory = [
                        *self.axHistory[: self.axHistoryPointer],
                        axis_rectangle,
                    ]
            else:
                self.updateScaleBox(event.buttonDownPos(Qt.MouseButton.RightButton), event.pos())
            return
        super().mouseDragEvent(event, axis=axis)


def create_plot_widget() -> pg.PlotWidget:
    """Create a plot with the application's three-button mouse behavior."""
    widget = pg.PlotWidget(
        viewBox=RightDragZoomViewBox(),
        axisItems={
            name: ScientificAxisItem(orientation=name)
            for name in ("left", "bottom", "top", "right")
        },
    )
    plot_item = widget.getPlotItem()
    assert plot_item is not None
    # Reserve a small outside margin for the upper/right frame strokes. Without
    # it, fractional-width pens can be clipped by the GraphicsView viewport.
    cast(Any, plot_item.layout).setContentsMargins(2, 2, 8, 3)
    grid_item = pg.GridItem()
    grid_item.setZValue(-100)
    grid_item.setVisible(False)
    plot_item.addItem(grid_item, ignoreBounds=True)
    widget._materials_grid_item = grid_item  # type: ignore[attr-defined]
    view_box = widget.getViewBox()
    # PyQtGraph's stock View All performs a one-shot range update.  Make it
    # equivalent to enabling Auto Scale on both axes, as users expect from the
    # X axis and Y axis context-menu controls.
    menu = view_box.getMenu(None)
    if menu is not None and hasattr(menu, "viewAll"):
        menu.viewAll.triggered.disconnect()
        menu.viewAll.triggered.connect(
            lambda _checked=False: view_box.enableAutoRange(axis=ViewBox.XYAxes, enable=True)
        )
    scene = cast(Any, widget.scene())
    if scene is not None and getattr(scene, "contextMenu", None):
        export_action = scene.contextMenu[0]
        export_action.setObjectName("materialsInteractiveExport")
        export_action.triggered.disconnect()
        export_action.triggered.connect(lambda _checked=False: export_interactive_plot(widget))
    from materials_data_studio.ui.theme import current_theme
    apply_plot_theme(widget, current_theme())
    return widget


def export_interactive_plot(widget: pg.PlotWidget, *, default_kind: str = "data") -> Path | None:
    """Export selected plot data or the visible view with explicit image settings."""
    spec = getattr(widget, "_materials_plot_spec", None)
    if not isinstance(spec, PlotSpec):
        QMessageBox.warning(widget, "Export Interactive Plot", "There is no plot to export.")
        return None
    dialog = InteractiveExportDialog(spec, widget)
    dialog.tabs.setCurrentIndex(
        {"data": 0, "vector": 1, "raster": 2, "h5xp": 3}.get(default_kind, 0)
    )
    try:
        return _complete_interactive_export(widget, spec, dialog)
    finally:
        dialog.deleteLater()


def _complete_interactive_export(widget: pg.PlotWidget, spec: PlotSpec, dialog: InteractiveExportDialog) -> Path | None:
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    export_format = dialog.selected_format()
    filters = {
        "csv": "CSV (*.csv)",
        "tsv": "TSV (*.tsv)",
        "svg": "SVG (*.svg)",
        "pdf": "PDF (*.pdf)",
        "png": "PNG (*.png)",
        "tiff": "TIFF (*.tiff)",
        "jpg": "JPEG (*.jpg)",
        "h5xp": "Igor HDF5 Packed Experiment (*.h5xp)",
    }
    name, _selected_filter = QFileDialog.getSaveFileName(
        widget,
        "Export Interactive Plot",
        f"interactive-plot.{export_format}",
        filters[export_format],
    )
    if not name:
        return None
    path = Path(name).with_suffix(f".{export_format}")
    if dialog.export_kind() == "data":
        try:
            from materials_data_studio.ui.tasks import blocking_task
            indices = dialog.selected_curve_indices()
            blocking_task(widget, "Export plotted data", lambda: export_plot_curves(path, spec, indices), allow_cancel=False)
        except Exception as exc:
            QMessageBox.critical(widget, "Export Interactive Plot", str(exc))
            return None
        return path
    if dialog.export_kind() == "h5xp":
        try:
            from materials_data_studio.plotting.igor_h5xp import IgorH5xpExporter

            module_name = str(widget.property("materialsIgorModule") or "Graph")
            graph_state = graph_export_state_from_plot_spec(spec, module_name)
            from materials_data_studio.ui.tasks import blocking_task
            blocking_task(widget, "Export Igor experiment", lambda: IgorH5xpExporter().export_graph(graph_state, path), allow_cancel=False)
        except Exception as exc:
            QMessageBox.critical(widget, "Export Interactive Plot", str(exc))
            return None
        return path
    x_range, y_range = widget.getViewBox().viewRange()
    if spec.x_log_scale:
        x_range = [float(np.power(10.0, np.clip(float(value), -300.0, 300.0))) for value in x_range]
    if spec.y_log_scale:
        y_range = [float(np.power(10.0, np.clip(float(value), -300.0, 300.0))) for value in y_range]
    width, height, dpi, transparent = dialog.image_options()
    visible_spec = replace(
        spec,
        x_range=(float(x_range[0]), float(x_range[1])),
        y_range=(float(y_range[0]), float(y_range[1])),
        figure_width_cm=width,
        figure_height_cm=height,
        dpi=dpi,
        transparent=transparent,
    )
    try:
        from materials_data_studio.ui.tasks import blocking_task
        appearance = getattr(dialog, "appearance_override", None) or active_plot_appearance()
        theme = str(widget.property("materialsTheme") or "light")
        blocking_task(widget, "Export figure", lambda: export_figure(path, visible_spec, appearance=appearance, theme=theme), allow_cancel=False)
    except Exception as exc:
        QMessageBox.critical(widget, "Export Interactive Plot", str(exc))
        return None
    return path


def install_legend_toggle(
    widget: pg.PlotWidget,
    on_toggled: Callable[[bool], None],
    *,
    checked: bool = True,
) -> QAction:
    """Add a persistent Legends toggle to the Plot Options context menu."""
    action = QAction("Legends", widget)
    action.setObjectName("materialsLegendToggle")
    action.setCheckable(True)
    action.setChecked(checked)
    action.toggled.connect(on_toggled)
    plot_item = widget.getPlotItem()
    assert plot_item is not None
    menu = plot_item.getMenu()
    assert menu is not None
    menu.addSeparator()
    menu.addAction(action)
    return action


def apply_plot_theme(widget: pg.PlotWidget, theme: str) -> None:
    """Apply light/dark colors to the graphics scene and its legend."""
    dark = theme == "dark"
    appearance = active_plot_appearance()
    colors = appearance.dark if dark else appearance.light
    spec = getattr(widget, "_materials_plot_spec", None)
    widget.setProperty("materialsTheme", "dark" if dark else "light")
    widget.setBackground(colors.background)
    grid_item = getattr(widget, "_materials_grid_item", None)
    if isinstance(grid_item, pg.GridItem):
        grid_pen = pg.mkPen(colors.grid, width=appearance.grid_width)
        grid_color = grid_pen.color()
        grid_color.setAlphaF(appearance.grid_opacity)
        grid_pen.setColor(grid_color)
        grid_item.setPen(grid_pen)
        grid_item.setVisible(
            isinstance(spec, PlotSpec) and spec.grid_enabled and appearance.show_grid
        )
    plot_item = widget.getPlotItem()
    assert plot_item is not None
    # The ViewBox owns an opaque background item of its own. Updating only the
    # PlotWidget leaves that item at its previous (often light-theme) color.
    plot_item.getViewBox().setBackgroundColor(colors.background)
    for axis_name, visible in (
        ("left", appearance.show_left_axis),
        ("bottom", appearance.show_bottom_axis),
        ("right", appearance.show_right_axis),
        ("top", appearance.show_top_axis),
    ):
        plot_item.showAxis(axis_name, visible)
    axis_label_font = QFont()
    axis_label_font.setPointSize(appearance.axis_label_font_size)
    tick_label_font = QFont()
    tick_label_font.setPointSize(appearance.tick_label_font_size)
    title_font = QFont()
    title_font.setPointSize(appearance.title_font_size)
    for axis_name in ("left", "bottom", "right", "top"):
        axis = plot_item.getAxis(axis_name)
        axis.setPen(pg.mkPen(colors.frame, width=appearance.frame_width))
        axis.setTextPen(pg.mkPen(colors.tick_label))
        axis.setTickPen(pg.mkPen(colors.tick, width=appearance.tick_width))
        axis.setStyle(
            tickFont=tick_label_font,
            tickLength=-max(1, round(appearance.tick_length)),
            maxTickLevel=1 if appearance.show_minor_ticks else 0,
            showValues=(appearance.show_tick_labels and axis_name in {"left", "bottom"}),
        )
        if isinstance(axis, ScientificAxisItem):
            axis.major_length = appearance.tick_length if appearance.show_major_ticks else 0
            axis.minor_length = appearance.minor_tick_length if appearance.show_minor_ticks else 0
            axis.picture = None
        if axis_name == "right":
            axis.setWidth(max(10, round(appearance.tick_length + appearance.frame_width + 4)))
        elif axis_name == "top":
            axis.setHeight(max(10, round(appearance.tick_length + appearance.frame_width + 4)))
        if axis_name in {"bottom", "top"}:
            major = appearance.x_major_tick_interval
            minor = appearance.x_minor_tick_interval
        else:
            major = appearance.y_major_tick_interval
            minor = appearance.y_minor_tick_interval
        logarithmic = (
            plot_item.ctrl.logYCheck.isChecked()
            if axis_name in {"left", "right"}
            else plot_item.ctrl.logXCheck.isChecked()
        )
        if major > 0 and not logarithmic:
            axis.setTickSpacing(
                major=major,
                minor=minor if appearance.show_minor_ticks and minor > 0 else major / 5.0,
            )
        else:
            axis.setTickSpacing()
        assert axis.label is not None
        axis.label.setFont(axis_label_font)
        axis.label.setDefaultTextColor(QColor(colors.axis_label))
        axis.label.setVisible(appearance.show_axis_labels and axis_name in {"left", "bottom"})
    title_item = plot_item.titleLabel
    title_item.setFont(title_font)
    if isinstance(spec, PlotSpec):
        widget.setTitle(spec.title, color=colors.title)
        title_item.setVisible(appearance.show_title and bool(spec.title))
    legend = plot_item.legend
    if legend is not None:
        legend.setVisible(
            isinstance(spec, PlotSpec)
            and spec.legend_enabled
            and bool(spec.curves)
            and appearance.show_legend
        )
        legend.setBrush(pg.mkBrush(colors.legend_background))
        legend.setPen(pg.mkPen(colors.legend_border))
        for _sample, label in legend.items:
            label.setText(
                label.text,
                color=colors.legend_text,
                size=f"{appearance.legend_font_size}pt",
            )


def render_plot(widget: pg.PlotWidget, spec: PlotSpec) -> None:
    """Replace a PyQtGraph preview while retaining full source arrays."""
    previous = getattr(widget, "_materials_plot_spec", None)
    first_data = bool(spec.curves) and (not isinstance(previous, PlotSpec) or not previous.curves)
    if update_rendered_plot_data(widget, spec):
        plot = widget.getPlotItem()
        assert plot is not None
        retained = [*getattr(widget, "_materials_curve_items", []), *getattr(widget, "_materials_point_labels", []), *getattr(widget, "_materials_persistent_overlays", [])]
        for item in list(plot.items):
            if item not in retained and not isinstance(item, pg.GridItem):
                plot.removeItem(item)
        return
    widget._materials_plot_spec = spec  # type: ignore[attr-defined]
    widget.clear()
    widget._materials_point_labels = []  # type: ignore[attr-defined]
    mode_changed = isinstance(previous, PlotSpec) and (previous.x_log_scale != spec.x_log_scale or previous.y_log_scale != spec.y_log_scale)
    # AxisItem interprets its current range as exponents immediately on enabling
    # log mode. Never let an old linear intensity range (e.g. 0..100000) become
    # that exponent range, even for one synchronous axis update.
    if mode_changed:
        widget.getViewBox().setRange(xRange=(0, 1), yRange=(0, 1), padding=0)
    widget.setLogMode(x=spec.x_log_scale, y=spec.y_log_scale)
    widget.setLabel("bottom", spec.x_label)
    widget.setLabel("left", spec.y_label)
    widget.setTitle(spec.title)
    colors = (
        active_plot_appearance().dark
        if str(widget.property("materialsTheme") or "light") == "dark"
        else active_plot_appearance().light
    )
    grid_visible = spec.grid_enabled and active_plot_appearance().show_grid
    widget.showGrid(x=False, y=False)
    plot_item = widget.getPlotItem()
    assert plot_item is not None
    grid_item = getattr(widget, "_materials_grid_item", None)
    if isinstance(grid_item, pg.GridItem) and grid_item not in plot_item.items:
        plot_item.addItem(grid_item, ignoreBounds=True)
    if isinstance(grid_item, pg.GridItem):
        grid_item.setVisible(grid_visible)
    plot_item.getViewBox().setBackgroundColor(colors.background)
    legend = plot_item.legend
    if spec.legend_enabled and spec.curves and active_plot_appearance().show_legend:
        legend = widget.addLegend()
        legend.setVisible(True)
    elif legend is not None:
        legend.setVisible(False)
    curve_items: list[pg.PlotDataItem] = []
    for curve in spec.curves:
        style = curve.style
        # PyQtGraph's clip-to-view/downsampling path uses binary searches that
        # assume ascending x values.  FC cooling branches and hysteresis loops
        # are commonly descending or non-monotonic; enabling the optimisation
        # for those curves makes them disappear after zooming.
        ascending_x = _can_reduce(curve)
        item = widget.plot(
            curve.x,
            curve.y,
            name=style.legend_text or curve.label,
            pen=None
            if style.line_style.lower() == "none"
            else pg.mkPen(
                style.color,
                width=style.line_width,
                style=_PEN_STYLES.get(style.line_style, Qt.PenStyle.SolidLine),
            ),
            symbol=qt_symbol(style.marker) if style.marker in {"dagger", "|"} else _SYMBOLS.get(style.marker),
            symbolSize=curve.marker_sizes if curve.marker_sizes is not None else style.marker_size,
            symbolPen=pg.mkPen(style.marker_edge_color or style.color, width=style.marker_edge_width),
            symbolBrush=pg.mkBrush(style.marker_fill_color) if style.marker_fill_color else None,
            autoDownsample=ascending_x,
        )
        # PyQtGraph 0.14 with PySide 6.10 can resolve the PlotWidget instead of
        # its ViewBox when clipToView is supplied to PlotItem.plot(). Enabling
        # clipping after the item has joined the scene avoids that broken
        # initialization path while retaining fast interaction for large data.
        item.setClipToView(ascending_x)
        item.setDownsampling(auto=ascending_x, method="peak")
        curve_items.append(item)
    _update_point_labels(widget, spec)
    widget._materials_curve_items = curve_items  # type: ignore[attr-defined]
    widget._materials_curve_labels = [  # type: ignore[attr-defined]
        curve.label for curve in spec.curves
    ]
    widget._materials_curve_styles = [curve.style.to_dict() for curve in spec.curves]  # type: ignore[attr-defined]
    widget._materials_curve_sizes = [None if curve.marker_sizes is None else curve.marker_sizes.copy() for curve in spec.curves]  # type: ignore[attr-defined]
    if first_data or mode_changed:
        widget.getViewBox().enableAutoRange(axis=ViewBox.XYAxes, enable=True)
    if not spec.curves:
        hint = pg.TextItem(
            "No plottable data\nImport or drop measurement files to begin",
            color=colors.tick_label,
            anchor=(0.5, 0.5),
        )
        hint.setPos(0.5, 0.5)
        plot_item.addItem(hint, ignoreBounds=True)
        widget.getViewBox().setRange(xRange=(0, 1), yRange=(0, 1), padding=0)
    _apply_spec_ranges(widget, spec)
    apply_plot_theme(widget, str(widget.property("materialsTheme") or "light"))

    for overlay in getattr(widget, "_materials_persistent_overlays", []):
        widget.addItem(overlay)


def _can_reduce(curve: CurveSpec) -> bool:
    # PlotDataItem clips/downsamples x/y without slicing per-point symbolSize.
    # Keep sparse marker data paired, while retaining LOD for dense observations.
    return curve.marker_sizes is None and bool(len(curve.x) < 2 or np.all(curve.x[1:] >= curve.x[:-1]))


def _update_point_labels(widget: pg.PlotWidget, spec: PlotSpec) -> None:
    plot_item = widget.getPlotItem()
    assert plot_item is not None
    old_items = list(getattr(widget, "_materials_point_labels", []))
    label_items = []
    for curve in spec.curves:
        for x, y, text in zip(curve.x, curve.y, curve.point_labels, strict=False):
            if not text or not np.isfinite([x, y]).all() or (spec.x_log_scale and x <= 0) or (spec.y_log_scale and y <= 0):
                continue
            index = len(label_items)
            label = old_items[index] if index < len(old_items) else PeakTextItem(anchor=(0., 1.))
            gap = (curve.style.marker_size / 2 if curve.style.marker != "none" else 0.) + 4
            options = (text, curve.label_color, curve.label_font, curve.label_size, curve.label_angle, gap)
            if getattr(label, "_materials_label_options", None) != options:
                label.gap = gap
                label.setText(text, color=curve.label_color)
                font = QFont(curve.label_font)
                font.setPointSizeF(curve.label_size)
                label.setFont(font)
                label.setAngle(curve.label_angle)
                label.updateTextPos()
                label._materials_label_options = options
            position = QPointF(float(np.log10(x) if spec.x_log_scale else x), float(np.log10(y) if spec.y_log_scale else y))
            if label.pos() != position:
                label.setPos(position)
            if index >= len(old_items):
                label.setZValue(100)
                plot_item.addItem(label)
            label_items.append(label)
    for label in old_items[len(label_items):]:
        plot_item.removeItem(label)
    widget._materials_point_labels = label_items  # type: ignore[attr-defined]


def _apply_spec_ranges(widget: pg.PlotWidget, spec: PlotSpec) -> None:
    """PlotSpec limits are physical values; ViewBox log limits are exponents."""
    for axis, bounds, logarithmic in (("x", spec.x_range, spec.x_log_scale), ("y", spec.y_range, spec.y_log_scale)):
        if bounds is not None and np.isfinite(bounds).all() and (not logarithmic or min(bounds) > 0):
            values = tuple(float(v) for v in (np.log10(bounds) if logarithmic else bounds))
            widget.getViewBox().setRange(**{axis + "Range": values}, padding=0)


def update_rendered_plot_data(widget: pg.PlotWidget, spec: PlotSpec) -> bool:
    """Replace curve arrays in place when plot structure and styling are unchanged."""
    items = getattr(widget, "_materials_curve_items", None)
    labels = getattr(widget, "_materials_curve_labels", None)
    previous = getattr(widget, "_materials_plot_spec", None)
    if (
        not isinstance(items, list)
        or not isinstance(labels, list)
        or not isinstance(previous, PlotSpec)
        or len(items) != len(spec.curves)
        or labels != [curve.label for curve in spec.curves]
        or previous.x_log_scale != spec.x_log_scale
        or previous.y_log_scale != spec.y_log_scale
        or previous.x_label != spec.x_label
        or previous.y_label != spec.y_label
        or previous.title != spec.title
        or previous.legend_enabled != spec.legend_enabled
        or previous.grid_enabled != spec.grid_enabled
        or not spec.curves
    ):
        return False
    plot_item = widget.getPlotItem()
    if plot_item is None:
        return False
    styles = [curve.style.to_dict() for curve in spec.curves]
    old_styles = getattr(widget, "_materials_curve_styles", [])
    old_sizes = getattr(widget, "_materials_curve_sizes", [])
    if len(old_styles) != len(styles) or len(old_sizes) != len(styles):
        return False
    for index, (item, curve, old_curve) in enumerate(zip(items, spec.curves, previous.curves, strict=True)):
        if not isinstance(item, pg.PlotDataItem) or item not in plot_item.items:
            return False
        same_style = styles[index] == old_styles[index]
        same_sizes = (curve.marker_sizes is None and old_sizes[index] is None) or (curve.marker_sizes is not None and old_sizes[index] is not None and np.array_equal(curve.marker_sizes, old_sizes[index]))
        if same_style and same_sizes and curve.x is old_curve.x and curve.y is old_curve.y and not curve.x.flags.writeable and not curve.y.flags.writeable:
            continue
        ascending = _can_reduce(curve)
        if not ascending:
            item.setClipToView(False)
            item.setDownsampling(auto=False)
        if same_style and same_sizes:
            item.setData(curve.x, curve.y)
        else:
            style = curve.style
            name = style.legend_text or curve.label
            item.setData(curve.x, curve.y, name=name,
                         pen=None if style.line_style.lower() == "none" else pg.mkPen(style.color, width=style.line_width, style=_PEN_STYLES.get(style.line_style, Qt.PenStyle.SolidLine)),
                         symbol=qt_symbol(style.marker) if style.marker in {"dagger", "|"} else _SYMBOLS.get(style.marker),
                         symbolSize=curve.marker_sizes if curve.marker_sizes is not None else style.marker_size,
                         symbolPen=pg.mkPen(style.marker_edge_color or style.color, width=style.marker_edge_width),
                         symbolBrush=pg.mkBrush(style.marker_fill_color) if style.marker_fill_color else None)
            if plot_item.legend is not None:
                for sample, legend_label in plot_item.legend.items:
                    if sample.item is item:
                        sample.update()
                        if legend_label.text != name:
                            legend_label.setText(name)
        item.setClipToView(ascending)
        item.setDownsampling(auto=ascending, method="peak")
    _update_point_labels(widget, spec)
    widget._materials_curve_styles = styles  # type: ignore[attr-defined]
    widget._materials_curve_sizes = [None if curve.marker_sizes is None else curve.marker_sizes.copy() for curve in spec.curves]  # type: ignore[attr-defined]
    _apply_spec_ranges(widget, spec)
    widget._materials_plot_spec = spec  # type: ignore[attr-defined]
    return True
