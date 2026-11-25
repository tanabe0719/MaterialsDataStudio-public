"""Tabbed annotation editor for text/legends and image color scales."""

from __future__ import annotations

import copy
import math
from dataclasses import replace
from typing import Any

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.colors import to_rgb
from matplotlib.figure import Figure
from PySide6.QtCore import Qt, QTimer
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
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from materials_data_studio.tools.graph_document import EditableGraphDocument
from materials_data_studio.tools.graph_render import IgorMatplotlibRenderer, explicit_font
from materials_data_studio.tools.graph_trace_dialogs import unique_name
from materials_data_studio.tools.pxp_model import PxpAnnotation, PxpAnnotationStyle
from materials_data_studio.tools.pxp_style import COLORMAPS
from materials_data_studio.ui.style_inputs import ColorEdit, FontEdit, colormap_swatches


def rgb(value: str) -> tuple[int, int, int]:
    return tuple(round(v * 65535) for v in to_rgb(value))  # type: ignore[return-value]


def color_text(value: tuple[int, int, int] | None) -> str:
    return "#" + "".join(f"{round(v / 257):02x}" for v in (value or (0, 0, 0)))


class AnnotationDialog(QDialog):
    def __init__(self, parent: Any, document: EditableGraphDocument, graph_name: str,
                 identifier: str, colorbar: bool = False, *, kind: str = "", adding: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle("Modify Annotation — ColorScale" if colorbar else "Modify Annotation — Text / Legend")
        self.resize(800, 750)
        self.document, self.graph = document, document.graph(graph_name)
        self.identifier, self.colorbar = identifier, colorbar
        self.adding = adding
        self.result_graph: Any = None
        self.result_layout: dict[str, Any] = {}
        self.layout_state = copy.deepcopy(document.current.layout.get(graph_name, {}))
        self.duplicate_requested = False
        self.deleted = False
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        header.addWidget(QLabel("Name"))
        self.name = QLineEdit(identifier)
        self.name.setEnabled(not colorbar)
        header.addWidget(self.name)
        self.duplicate_button = QPushButton("Duplicate")
        self.duplicate_button.setVisible(not colorbar and not adding)
        self.duplicate_button.clicked.connect(self.duplicate)
        header.addWidget(self.duplicate_button)
        layout.addLayout(header)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 3)
        self.fields: dict[str, tuple[Any, Any]] = {}
        annotation = next((a for a in self.graph.annotations if a.name == identifier), None)
        self.kind = kind or ("ColorScale" if colorbar else "Tag" if annotation and annotation.trace
                             else annotation.kind if annotation and annotation.kind else "Legend" if (annotation is None or annotation.raw == self.graph.legend_raw) else "TextBox")
        self.image = next((i for i in self.graph.images if i.wave == identifier), None) if colorbar else None
        self.annotation_style = self.image.colorbar_style if self.image else annotation.style if annotation else self.graph.legend_style
        self.initial_raw = self.image.colorbar_label_raw if self.image else annotation.raw if annotation else self.graph.legend_raw
        if self.kind == "Legend" and not self.initial_raw:
            self.initial_raw = "\r".join(f"\\s({t.name}) {t.name}" for t in self.graph.traces)
        self.raw = QPlainTextEdit(self.initial_raw.replace("\r", "\n"))
        self.raw.setMinimumHeight(100)
        text_form = self.page("Axis Labels" if colorbar else "Text")
        text_form.addRow("Label" if colorbar else "Text (Igor formatting supported)", self.raw)
        formatting = QHBoxLayout()
        for caption, code in (("Bold", "\\f01"), ("Italic", "\\f02"), ("Normal", "\\f00"), ("Superscript", "\\S"), ("Subscript", "\\B")):
            button = QPushButton(caption)
            button.clicked.connect(lambda _checked=False, token=code: self.raw.insertPlainText(token))
            formatting.addWidget(button)
        text_form.addRow(formatting)
        if not colorbar and self.graph.traces:
            symbols_source = QComboBox()
            symbols_source.addItems([trace.name for trace in self.graph.traces])
            insert_symbol = QPushButton("Insert trace symbol")
            insert_symbol.clicked.connect(lambda: self.raw.insertPlainText(f"\\s({symbols_source.currentText()})"))
            text_form.addRow(insert_symbol, symbols_source)
        if self.kind == "Tag":
            target = self.page("Tag Arrow")
            traces = [trace.name for trace in self.graph.traces]
            self.field(target, "trace", "Attach to trace", annotation.trace if annotation and annotation.trace else traces[0], traces)
            self.field(target, "point", "Point index", annotation.point if annotation and annotation.point is not None else 0, int)
            self.field(target, "arrow", "Connect tag to wave", str(annotation.arrow if annotation else 0),
                       [("No line", "0"), ("Line", "1"), ("Arrow to point", "2"), ("Arrow to text", "3"), ("Double arrow", "4")])
            self.field(target, "tag_standoff", "Line / arrow standoff (pt; 0 = auto)", self.annotation_style.tag_standoff, float)
            self.field(target, "tag_line_width", "Tag line thickness (pt; 0 = auto)", self.annotation_style.tag_line_width, float)
            self.field(target, "tag_line_color", "Tag line color (blank = default)",
                       color_text(self.annotation_style.tag_line_color) if self.annotation_style.tag_line_color else "", str)
            self.field(target, "tag_line_dash", "Tag line style", str(self.annotation_style.tag_line_dash),
                       [(str(i), str(i)) for i in range(18)])
            self.field(target, "tag_head_style", "Arrowhead", str(self.annotation_style.tag_head_style),
                       [("No barbs", "0"), ("Top barb", "1"), ("Bottom barb", "2"), ("Full arrow", "3")])
            self.field(target, "tag_head_length", "Arrowhead length (pt; 0 = auto)", self.annotation_style.tag_head_length, float)
            self.field(target, "tag_head_ratio", "Arrowhead width / length (0 = auto)", self.annotation_style.tag_head_ratio, float)
            self.field(target, "tag_head_sharp", "Arrowhead sharpness (−1 to 1)", self.annotation_style.tag_head_sharp, float)
            self.field(target, "tag_head_outline", "Arrowhead outline (pt; 0 = filled)", self.annotation_style.tag_head_outline, float)
            for label, code in (("Wave name", "\\ON"), ("Point index", "\\OP"), ("X value", "\\OX"), ("Y value", "\\OY")):
                button = QPushButton(label)
                button.clicked.connect(lambda _checked=False, token=code: self.raw.insertPlainText(token))
                formatting.addWidget(button)
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
        self.field(frame, "frame_style", "Frame line style", str(self.annotation_style.frame_style),
                   [("Single", "0"), ("Double", "1"), ("Triple", "2"), ("Shadow", "3")])
        self.field(frame, "frame_width", "Border thickness (pt)", self.annotation_style.frame_width, float)
        self.field(frame, "frame_color", "Border color", color_text(self.annotation_style.frame_color), str)
        self.field(frame, "transparent", "Transparent background", self.annotation_style.transparent, bool)
        self.field(frame, "background_color", "Background color", color_text(self.annotation_style.background_color), str)
        if not colorbar:
            self.field(frame, "background_mode", "Background source", str(self.annotation_style.background_mode),
                       [("Custom color", "0"), ("Plot background", "2"), ("Window background", "3")])
            self.field(frame, "padding", "Padding / halo (pt)", self.annotation_style.padding, float)
            self.field(frame, "shadow", "Shadow offset (pt)", self.annotation_style.shadow, float)
        position = self.page("Position")
        anchors = ["LT", "MT", "RT", "LC", "MC", "RC", "LB", "MB", "RB"]
        self.field(position, "anchor", "Anchor", self.annotation_style.anchor, anchors)
        self.field(position, "x_percent", "X offset (%)", self.annotation_style.x_percent, float)
        self.field(position, "y_percent", "Y offset (%)", self.annotation_style.y_percent, float)
        if not colorbar:
            if self.kind != "Tag":
                self.field(position, "exterior", "Placement", str(self.annotation_style.exterior),
                           [("Inside plot", "0"), ("Outside, reserve margins", "1"), ("Outside, keep margins", "2")])
            self.field(position, "rotation", "Rotation (deg)", settings.get("rotation", self.annotation_style.rotation), float)
            self.field(position, "visible", "Show annotation", settings.get("visible", True), bool)
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
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Add" if adding else "Change")
        delete = buttons.addButton("Delete", QDialogButtonBox.ButtonRole.DestructiveRole)
        delete.setVisible(not adding)
        delete.clicked.connect(self.delete)
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.draw_preview)
        self.raw.textChanged.connect(self.queue_preview)
        self.frame_mode.currentIndexChanged.connect(self.queue_preview)
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
            for choice in parser:
                caption, data = choice if isinstance(choice, tuple) else (choice, choice)
                widget.addItem(caption, data)
            widget.setProperty("mdsTranslateItems", key not in {"trace", "color_table", "anchor"})
            widget.setCurrentIndex(max(0, widget.findData(str(value))))
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
            value = widget.isChecked() if parser is bool else widget.currentData() if isinstance(parser, list) else widget.text()
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
        for key, low, high in (("tag_standoff", 0, 200), ("tag_line_width", 0, 100),
                               ("tag_head_length", 0, 200), ("tag_head_ratio", 0, 10),
                               ("tag_head_sharp", -1, 1), ("tag_head_outline", 0, 100)):
            if key in values and not low <= values[key] <= high:
                raise ValueError(f"{key} must be between {low} and {high}.")
        return values

    def updated(self) -> tuple[Any, dict[str, Any]]:
        values = self.values()
        values["frame"] = self.frame_mode.currentData()
        for key in ("frame_style", "background_mode", "exterior", "tag_line_dash", "tag_head_style"):
            if key in values:
                values[key] = int(values[key])
        layout = copy.deepcopy(self.layout_state)
        style_keys = {"anchor", "x_percent", "y_percent", "frame", "frame_style", "frame_width", "transparent", "background_mode", "exterior", "padding", "shadow", "font_size", "line_spacing",
                      "symbol_size", "symbol_width", "symbol_min_width", "symbol_max_width", "vertical", "width", "height", "height_percent", "log_axis"}
        style = replace(self.annotation_style, **{k: v for k, v in values.items() if k in style_keys},
                        color=rgb(values["color"]), frame_color=rgb(values["frame_color"]), background_color=rgb(values["background_color"]))
        if style.font_size != self.annotation_style.font_size:
            style = replace(style, inherit_graph_font=style.font_size == 0)
        if style.padding != self.annotation_style.padding:
            style = replace(style, padding_explicit=True)
        if self.kind == "Tag":
            line_color = values["tag_line_color"]
            tag_style = {key: value for key, value in values.items() if key.startswith("tag_")}
            tag_style["tag_line_color"] = rgb(line_color) if line_color else None
            style = replace(style, **tag_style)
        graph = self.graph
        if self.deleted:
            if self.colorbar:
                graph = replace(graph, images=tuple(replace(i, colorbar_visible=False) if i.wave == self.identifier else i for i in graph.images))
            else:
                old = next((a for a in graph.annotations if a.name == self.identifier), None)
                graph = replace(graph, annotations=tuple(a for a in graph.annotations if a.name != self.identifier),
                                legend_raw="" if old is None or old.raw == graph.legend_raw else graph.legend_raw)
                layout.get("annotations", {}).pop(self.identifier, None)
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
            graph = replace(graph, images=tuple(replace(i, colorbar_style=style, colorbar_label_raw=raw, colorbar_visible=True,
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
            if self.kind == "Tag":
                trace, point = values["trace"], values["point"]
                model = next(t for t in graph.traces if t.name == trace)
                y = self.document.render_source().wave(model.y_wave)
                if y is None or not 0 <= point < y.shape[0]:
                    raise ValueError("The point index is outside the selected trace.")
                annotation = PxpAnnotation(name, raw, style, trace, point, int(values["arrow"]), "Tag")
            else:
                annotation = PxpAnnotation(name, raw, style, kind=self.kind)
            if self.adding:
                existing = graph.annotations or ((PxpAnnotation("legend", graph.legend_raw, graph.legend_style, kind="Legend"),) if graph.legend_raw else ())
                graph = replace(graph, legend_raw="", annotations=(*existing, annotation))
            elif self.duplicate_requested:
                existing = graph.annotations or ((PxpAnnotation(self.identifier, graph.legend_raw, graph.legend_style),) if graph.legend_raw else ())
                graph = replace(graph, legend_raw="", annotations=(*existing, annotation))
            elif graph.annotations:
                old = next((a for a in graph.annotations if a.name == self.identifier), None)
                graph = replace(graph, legend_raw="" if old and old.raw == graph.legend_raw else graph.legend_raw, annotations=tuple(annotation if a.name == self.identifier else a for a in graph.annotations))
            else:
                graph = replace(graph, legend_raw="", annotations=(annotation,))
            annotations_layout = layout.setdefault("annotations", {})
            previous = annotations_layout.get(self.identifier, {}) if self.duplicate_requested else annotations_layout.pop(self.identifier, {})
            old = next((a for a in self.graph.annotations if a.name == self.identifier), None)
            if old and (old.trace, old.point) != (annotation.trace, annotation.point):
                previous = {key: value for key, value in previous.items() if key not in {"point_label", "above_points"}}
            layout["annotations"][name] = {**previous, "font": values["font"], "rotation": values["rotation"], "movable": values["movable"], "visible": values["visible"]}
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


class AnnotationTypeDialog(QDialog):
    """Select and edit an annotation with one document transaction."""

    def __init__(self, parent: Any, document: EditableGraphDocument, graph_name: str,
                 identifier: str = "", *, colorbar: bool = False, position: tuple[float, float] = (50, 50)) -> None:
        super().__init__(parent)
        from materials_data_studio.ui.localization import tr

        self.document, self.graph = document, document.graph(graph_name)
        self.identifier, self.position = identifier, position
        self.original = next((a for a in self.graph.annotations if a.name == identifier), None)
        self.original_kind = (
            "ColorScale" if colorbar else "Tag" if self.original and self.original.trace
            else self.original.kind if self.original and self.original.kind
            else "Legend" if identifier and (self.original is None or self.original.raw == self.graph.legend_raw)
            else "TextBox"
        )
        self.editor: AnnotationDialog | None = None
        self.result_graph, self.result_layout = self.graph, {}
        self.setWindowTitle(tr("Modify Annotation" if identifier else "Add Annotation"))
        self.resize(820, 750)
        self.outer = QVBoxLayout(self)
        header = QFormLayout()
        self.kind = QComboBox()
        self.kind.addItems(["TextBox", "Legend", "ColorScale", "Tag"])
        self.kind.setCurrentText(self.original_kind)
        self.source = QComboBox()
        self.source.addItems([i.wave for i in self.graph.images])
        if colorbar:
            self.source.setCurrentText(identifier)
        header.addRow(tr("Annotation type"), self.kind)
        header.addRow(tr("ColorScale source"), self.source)
        self.outer.addLayout(header)
        self.status = QLabel()
        self.outer.addWidget(self.status)
        close = QPushButton(tr("Cancel"))
        close.clicked.connect(self.reject)
        self.outer.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
        self.close_button = close
        self.kind.currentTextChanged.connect(self.rebuild)
        self.source.currentTextChanged.connect(self.rebuild)
        self.rebuild()

    def rebuild(self, _value: str = "") -> None:
        from materials_data_studio.ui.localization import tr, translate_tree

        graph, kind = self.graph, self.kind.currentText()
        old_text = self.editor.raw.toPlainText() if self.editor and not self.editor.colorbar else ""
        if self.editor:
            self.outer.removeWidget(self.editor)
            self.editor.hide()
            self.editor.deleteLater()
            self.editor = None
        self.source.setEnabled(kind == "ColorScale")
        error = ("Add an image to this graph before adding a ColorScale." if kind == "ColorScale" and not graph.images
                 else "Add a trace to this graph before adding a Tag." if kind == "Tag" and not graph.traces else "")
        self.status.setText(tr(error))
        self.status.setVisible(bool(error))
        self.close_button.setVisible(bool(error))
        if error:
            return
        layout = self.document.current.clone()
        switching = bool(self.identifier and (kind != self.original_kind or (kind == "ColorScale" and self.source.currentText() != self.identifier)))
        new = not self.identifier or switching
        if switching and self.original_kind == "ColorScale":
            graph = replace(graph, images=tuple(replace(i, colorbar_visible=False) if i.wave == self.identifier else i for i in graph.images))
        elif switching:
            graph = replace(graph, annotations=tuple(a for a in graph.annotations if a.name != self.identifier),
                            legend_raw="" if self.original is None or self.original.raw == graph.legend_raw else graph.legend_raw)
            layout.layout.get(graph.name, {}).get("annotations", {}).pop(self.identifier, None)
        if kind == "ColorScale":
            identifier = self.source.currentText()
        else:
            identifier = self.identifier if self.identifier and self.original_kind != "ColorScale" else unique_name("text0", [a.name for a in graph.annotations])
            if new:
                style = self.original.style if self.original else PxpAnnotationStyle(anchor="LB", x_percent=self.position[0], y_percent=self.position[1], frame=0, transparent=True)
                seed = PxpAnnotation(identifier, old_text or (self.original.raw if self.original else ""), style, kind=kind)
                existing = graph.annotations or ((PxpAnnotation("legend", graph.legend_raw, graph.legend_style, kind="Legend"),) if graph.legend_raw else ())
                graph = replace(graph, legend_raw="", annotations=(*existing, seed))
        layout.graphs = tuple(graph if g.name == graph.name else g for g in layout.graphs)
        document = replace(self.document, current=layout)
        editor = AnnotationDialog(self, document, graph.name, identifier, kind == "ColorScale", kind=kind, adding=new and kind == "ColorScale")
        if new and kind != "ColorScale":
            editor.duplicate_button.hide()
            for button_box in editor.findChildren(QDialogButtonBox):
                button_box.button(QDialogButtonBox.StandardButton.Ok).setText(tr("Add" if not self.identifier else "Change"))
                for button in button_box.buttons():
                    if button_box.buttonRole(button) == QDialogButtonBox.ButtonRole.DestructiveRole:
                        button.hide()
        editor.setWindowFlags(Qt.WindowType.Widget)
        editor.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        editor.accepted.connect(lambda: self.finish(editor))
        editor.rejected.connect(self.reject)
        self.outer.addWidget(editor, 1)
        self.editor = editor
        translate_tree(editor)
        editor.show()

    def finish(self, editor: AnnotationDialog) -> None:
        self.result_graph, self.result_layout = editor.result_graph, editor.result_layout
        self.accept()
