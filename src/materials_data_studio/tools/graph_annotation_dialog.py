"""Tabbed annotation editor for text/legends and image color scales."""

from __future__ import annotations

import copy
import math
from dataclasses import replace
from typing import Any

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.colors import to_rgb
from matplotlib.figure import Figure
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from materials_data_studio.tools.graph_document import EditableGraphDocument
from materials_data_studio.tools.graph_render import IgorMatplotlibRenderer, explicit_font
from materials_data_studio.tools.graph_trace_dialogs import unique_name
from materials_data_studio.tools.pxp_model import PxpAnnotation
from materials_data_studio.tools.pxp_style import COLORMAPS
from materials_data_studio.ui.style_inputs import ColorEdit, FontEdit, colormap_swatches


def rgb(value: str) -> tuple[int, int, int]:
    return tuple(round(v * 65535) for v in to_rgb(value))  # type: ignore[return-value]


def color_text(value: tuple[int, int, int] | None) -> str:
    return "#" + "".join(f"{round(v / 257):02x}" for v in (value or (0, 0, 0)))


class AnnotationDialog(QDialog):
    def __init__(self, parent: Any, document: EditableGraphDocument, graph_name: str,
                 identifier: str, colorbar: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle("Modify Annotation — ColorScale" if colorbar else "Modify Annotation — Text / Legend")
        self.resize(800, 750)
        self.document, self.graph = document, document.graph(graph_name)
        self.identifier, self.colorbar = identifier, colorbar
        self.result_graph: Any = None
        self.result_layout: dict[str, Any] = {}
        self.layout_state = copy.deepcopy(document.current.layout.get(graph_name, {}))
        self.duplicate_requested = False
        self.deleted = False
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        header.addWidget(QLabel("Annotation: " + identifier))
        self.name = QLineEdit(identifier)
        self.name.setEnabled(not colorbar)
        header.addWidget(self.name)
        self.duplicate_button = QPushButton("Duplicate")
        self.duplicate_button.setEnabled(not colorbar)
        self.duplicate_button.clicked.connect(self.duplicate)
        header.addWidget(self.duplicate_button)
        layout.addLayout(header)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 3)
        self.fields: dict[str, tuple[Any, Any]] = {}
        annotation = next((a for a in self.graph.annotations if a.name == identifier), None)
        self.image = next((i for i in self.graph.images if i.wave == identifier), None) if colorbar else None
        self.annotation_style = self.image.colorbar_style if self.image else annotation.style if annotation else self.graph.legend_style
        self.initial_raw = self.image.colorbar_label_raw if self.image else annotation.raw if annotation else self.graph.legend_raw
        self.raw = QPlainTextEdit(self.initial_raw)
        self.raw.setMinimumHeight(100)
        text_form = self.page("Axis Labels" if colorbar else "Text")
        text_form.addRow("Label" if colorbar else "Text (Igor formatting supported)", self.raw)
        formatting = QHBoxLayout()
        for caption, code in (("Bold", "\\f01"), ("Italic", "\\f02"), ("Normal", "\\f00"), ("Superscript", "\\S"), ("Subscript", "\\B")):
            button = QPushButton(caption)
            button.clicked.connect(lambda _checked=False, token=code: self.raw.insertPlainText(token))
            formatting.addWidget(button)
        text_form.addRow(formatting)
        settings = self.layout_state.get("colorbars" if colorbar else "annotations", {}).get(identifier, {})
        self.initial_font = settings.get("font", self.graph.style.font_family or "Arial")
        self.field(text_form, "font", "Font", self.initial_font, str)
        self.field(text_form, "font_size", "Font size (pt; 0 = graph default)", self.annotation_style.font_size, float)
        self.field(text_form, "color", "Text / tick color", color_text(self.annotation_style.color), str)
        if colorbar:
            self.field(text_form, "label_size", "Label size (pt)", settings.get("label_size", 14), float)
            self.field(text_form, "label_rotation", "Label rotation (deg)", settings.get("rotation", 270 if self.annotation_style.vertical else 0), float)
        else:
            self.field(text_form, "line_spacing", "Additional line spacing (pt)", self.annotation_style.line_spacing, float)
        frame = self.page("Frame")
        self.frame_mode = QComboBox()
        for text, value in (("None", 0), ("Underline", 1), ("Box", 2)):
            self.frame_mode.addItem(text, value)
        self.frame_mode.setCurrentIndex(max(0, self.frame_mode.findData(self.annotation_style.frame)))
        frame.addRow("Frame", self.frame_mode)
        self.field(frame, "frame_width", "Border thickness (pt)", self.annotation_style.frame_width, float)
        self.field(frame, "frame_color", "Border color", color_text(self.annotation_style.frame_color), str)
        self.field(frame, "transparent", "Transparent background", self.annotation_style.transparent, bool)
        self.field(frame, "background_color", "Background color", color_text(self.annotation_style.background_color), str)
        if not colorbar:
            self.field(frame, "padding", "Padding / halo (pt)", self.annotation_style.padding, float)
            self.field(frame, "shadow", "Shadow offset (pt)", self.annotation_style.shadow, float)
        position = self.page("Position")
        anchors = ["LT", "MT", "RT", "LC", "MC", "RC", "LB", "MB", "RB"]
        self.field(position, "anchor", "Anchor", self.annotation_style.anchor, anchors)
        self.field(position, "x_percent", "X offset (%)", self.annotation_style.x_percent, float)
        self.field(position, "y_percent", "Y offset (%)", self.annotation_style.y_percent, float)
        if not colorbar:
            self.field(position, "rotation", "Rotation (deg)", settings.get("rotation", self.annotation_style.rotation), float)
            self.field(position, "movable", "Movable by dragging", settings.get("movable", True), bool)
            symbols = self.page("Symbols")
            for key, caption in (("symbol_size", "Marker size (pt; 0 = trace size)"), ("symbol_width", "Symbol width (pt; 0 = font based)"),
                                 ("symbol_min_width", "Minimum line thickness (pt)"), ("symbol_max_width", "Maximum line thickness (pt)")):
                self.field(symbols, key, caption, getattr(self.annotation_style, key), float)
        else:
            assert self.image is not None
            main = self.page("ColorScale Main")
            self.field(main, "vertical", "Vertical", self.annotation_style.vertical, bool)
            self.field(main, "width", "Bar width (pt)", self.annotation_style.width, float)
            self.field(main, "height", "Bar height (pt; 0 = relative)", self.annotation_style.height, float)
            self.field(main, "height_percent", "Relative height (%)", self.annotation_style.height_percent, float)
            self.field(main, "log_axis", "Log scale ticks", self.annotation_style.log_axis, bool)
            self.field(main, "log_color", "Log color mapping", self.image.log_color, bool)
            self.field(main, "reverse", "Reverse color table", self.image.reverse, bool)
            self.field(main, "color_table", "Color table", self.image.color_table,
                       sorted({*COLORMAPS, self.image.color_table, "viridis", "gray", "plasma", "magma"}))
            limits = self.image.color_range or (None, None)
            self.field(main, "minimum", "Color range bottom (blank = auto)", limits[0], float)
            self.field(main, "maximum", "Color range top (blank = auto)", limits[1], float)
            ticks = self.page("Ticks")
            stored = settings.get("ticks", {})
            for key, caption, default, parser in (("visible", "Show ticks / tick labels", True, bool), ("count", "Approximate major tick count", 5, int),
                ("interval", "Major interval (blank = auto)", None, float), ("minor", "Minor ticks", False, bool),
                ("minor_count", "Minor subdivisions", 4, int), ("length", "Major length (pt)", 2, float), ("width", "Major thickness (pt)", 0.5, float),
                ("minor_length", "Minor length (pt)", 1, float), ("minor_width", "Minor thickness (pt)", 0.5, float), ("rotation", "Tick label rotation (deg)", 0, float)):
                self.field(ticks, "tick_" + key, caption, stored.get(key, default), parser)
        self.figure = Figure(figsize=(6, 1.7), dpi=90)
        self.preview = FigureCanvasQTAgg(self.figure)
        self.preview.setMinimumHeight(160)
        layout.addWidget(self.preview, 1)
        self.error = QLabel()
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Change")
        delete = buttons.addButton("Delete", QDialogButtonBox.ButtonRole.DestructiveRole)
        delete.clicked.connect(self.delete)
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.draw_preview)
        self.raw.textChanged.connect(self.queue_preview)
        self.name.textChanged.connect(self.queue_preview)
        for widget, _parser in self.fields.values():
            signal = widget.toggled if isinstance(widget, QCheckBox) else widget.currentTextChanged if isinstance(widget, QComboBox) else widget.textChanged
            signal.connect(self.queue_preview)
        self.draw_preview()

    def page(self, title: str) -> QFormLayout:
        page = QWidget()
        form = QFormLayout(page)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        self.tabs.addTab(scroll, title)
        return form

    def field(self, form: QFormLayout, key: str, caption: str, value: Any, parser: Any) -> None:
        widget: Any
        if parser is bool:
            widget = QCheckBox()
            widget.setChecked(bool(value))
        elif isinstance(parser, list):
            widget = QComboBox()
            widget.addItems(parser)
            widget.setCurrentText(str(value))
            if key == "color_table":
                colormap_swatches(widget)
        else:
            control = FontEdit if key == "font" else ColorEdit if "color" in key else QLineEdit
            widget = control("" if value is None else str(value))
        self.fields[key] = widget, parser
        form.addRow(caption, widget)

    def values(self) -> dict[str, Any]:
        values = {}
        for key, (widget, parser) in self.fields.items():
            value = widget.isChecked() if isinstance(widget, QCheckBox) else widget.currentText() if isinstance(widget, QComboBox) else widget.text()
            if parser is float or parser is int:
                value = parser(value) if value != "" else None
                if value is not None and not math.isfinite(value):
                    raise ValueError(f"{key}: enter a finite number.")
                if value is None and key not in {"minimum", "maximum", "tick_interval"}:
                    raise ValueError(f"{key}: a value is required.")
            values[key] = value
        for key in ("label_size", "width", "height_percent", "symbol_max_width", "tick_count", "tick_minor_count"):
            if key in values and values[key] <= 0:
                raise ValueError(f"{key} must be positive.")
        for key in ("font_size", "frame_width", "padding", "shadow", "height", "symbol_size", "symbol_width", "symbol_min_width", "tick_length", "tick_width", "tick_minor_length", "tick_minor_width"):
            if key in values and values[key] < 0:
                raise ValueError(f"{key} cannot be negative.")
        if values.get("tick_interval") is not None and values["tick_interval"] <= 0:
            raise ValueError("Tick interval must be positive.")
        return values

    def updated(self) -> tuple[Any, dict[str, Any]]:
        values = self.values()
        values["frame"] = self.frame_mode.currentData()
        layout = copy.deepcopy(self.layout_state)
        style_keys = {"anchor", "x_percent", "y_percent", "frame", "frame_width", "transparent", "padding", "shadow", "font_size", "line_spacing",
                      "symbol_size", "symbol_width", "symbol_min_width", "symbol_max_width", "vertical", "width", "height", "height_percent", "log_axis"}
        style = replace(self.annotation_style, **{k: v for k, v in values.items() if k in style_keys},
                        color=rgb(values["color"]), frame_color=rgb(values["frame_color"]), background_color=rgb(values["background_color"]))
        if style.font_size != self.annotation_style.font_size:
            style = replace(style, inherit_graph_font=style.font_size == 0)
        if style.padding != self.annotation_style.padding:
            style = replace(style, padding_explicit=True)
        graph = self.graph
        if self.deleted:
            if self.colorbar:
                graph = replace(graph, images=tuple(replace(i, colorbar_visible=False) if i.wave == self.identifier else i for i in graph.images))
            else:
                graph = replace(graph, annotations=tuple(a for a in graph.annotations if a.name != self.identifier),
                                legend_raw="")
            return graph, layout
        raw = self.raw.toPlainText()
        if style.font_size != self.annotation_style.font_size or values["font"] != self.initial_font:
            # Only replace inline font codes when the corresponding field changes.
            raw = explicit_font(raw, size=style.font_size if style.font_size != self.annotation_style.font_size else None,
                                family=values["font"] if values["font"] != self.initial_font else None)
        if self.colorbar:
            minimum, maximum = values["minimum"], values["maximum"]
            if minimum is not None and maximum is not None and minimum >= maximum:
                raise ValueError("Color minimum must be less than maximum.")
            if values["log_color"] and minimum is not None and minimum <= 0:
                raise ValueError("Log color range must be positive.")
            graph = replace(graph, images=tuple(replace(i, colorbar_style=style, colorbar_label_raw=raw,
                color_table=values["color_table"], log_color=values["log_color"], reverse=values["reverse"],
                color_range=(minimum, maximum)) if i.wave == self.identifier else i for i in graph.images))
            settings = layout.setdefault("colorbars", {}).setdefault(self.identifier, {})
            settings.pop("position", None)  # Anchor/offset fields now supply position.
            settings.update(font=values["font"], label_size=values["label_size"], rotation=values["label_rotation"],
                            ticks={k[5:]: v for k, v in values.items() if k.startswith("tick_")})
            if values["font"] == self.initial_font and "font" not in self.layout_state.get("colorbars", {}).get(self.identifier, {}):
                settings.pop("font", None)
        else:
            name = self.name.text().strip()
            if not name or any(a.name == name and a.name != self.identifier for a in graph.annotations):
                raise ValueError("Annotation name must be non-empty and unique.")
            annotation = PxpAnnotation(name, raw, style)
            if self.duplicate_requested:
                existing = graph.annotations or ((PxpAnnotation(self.identifier, graph.legend_raw, graph.legend_style),) if graph.legend_raw else ())
                graph = replace(graph, legend_raw="", annotations=(*existing, annotation))
            elif graph.annotations:
                graph = replace(graph, annotations=tuple(annotation if a.name == self.identifier else a for a in graph.annotations))
            else:
                graph = replace(graph, legend_raw="", annotations=(annotation,))
            annotations_layout = layout.setdefault("annotations", {})
            previous = annotations_layout.get(self.identifier, {}) if self.duplicate_requested else annotations_layout.pop(self.identifier, {})
            layout["annotations"][name] = {**previous, "font": values["font"], "rotation": values["rotation"], "movable": values["movable"]}
            if values["font"] == self.initial_font and "font" not in previous:
                layout["annotations"][name].pop("font", None)
        return graph, layout

    def queue_preview(self, *_args: Any) -> None:
        self.timer.start()

    def draw_preview(self) -> None:
        try:
            graph, layout = self.updated()
            state = self.document.current.clone()
            state.graphs = (graph,)
            state.layout[graph.name] = layout
            IgorMatplotlibRenderer().render(self.figure, replace(self.document, current=state), graph.name)
            self.preview.draw_idle()
            self.error.setText("")
        except (ValueError, TypeError) as exc:
            self.error.setText(str(exc))

    def duplicate(self) -> None:
        self.duplicate_requested = True
        self.name.setText(unique_name(self.identifier, [a.name for a in self.graph.annotations] + [self.identifier]))
        self.duplicate_button.setEnabled(False)

    def delete(self) -> None:
        if QMessageBox.question(self, "Delete Annotation", "Delete this annotation?") == QMessageBox.StandardButton.Yes:
            self.deleted = True
            self.apply()

    def apply(self) -> None:
        try:
            self.result_graph, self.result_layout = self.updated()
            self.accept()
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, "Annotation", str(exc))
