"""Canonical text/drawing objects, native-point styles and fast drag artists."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np
from matplotlib.artist import Artist
from matplotlib.patches import Arc, Ellipse, FancyArrowPatch, FancyBboxPatch, PathPatch, Rectangle
from matplotlib.path import Path as MplPath
from matplotlib.text import Text
from matplotlib.transforms import Affine2D, Bbox, IdentityTransform, blended_transform_factory
from PySide6.QtCore import QPointF, QSize, Qt
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from materials_data_studio.tools.graph_symbols import visual_combo
from materials_data_studio.tools.pxp_model import PxpDrawing
from materials_data_studio.tools.pxp_style import igor_rgb
from materials_data_studio.tools.pxp_text import IgorText
from materials_data_studio.ui.style_inputs import ColorEdit, FontEdit

LAYERS = ("ProgBack", "UserBack", "ProgAxes", "UserAxes", "ProgFront", "UserFront", "ProgTop", "UserTop", "Overlay")
KINDS = {"Text": "DrawText", "Line": "DrawLine", "Arrow": "DrawLine", "Rectangle": "DrawRect",
         "Rounded rectangle": "DrawRRect", "Oval": "DrawOval", "Arc": "DrawArc",
         "Polygon": "DrawPoly", "Freehand": "DrawPoly", "Bezier": "DrawBezier"}


def drawing_transform(ax: Any, drawing: PxpDrawing) -> Any:
    def coordinate(name: str, horizontal: bool) -> Any:
        if name == "abs":
            # Igor absolute drawing coordinates are points from the window's top left.
            return Affine2D().scale(ax.figure.dpi / 72, -ax.figure.dpi / 72).translate(0, ax.figure.bbox.height)
        if name in {"prel", "axrel"}:
            return ax.transAxes if horizontal else Affine2D().scale(1, -1).translate(0, 1) + ax.transAxes
        matching = next((a for a in ax.figure.axes if name in getattr(a,"_mds_axis_names",()) or a.get_gid() == "plotaxis:" + name), ax)
        return matching.transData
    return blended_transform_factory(coordinate(drawing.xcoord, True), coordinate(drawing.ycoord, False))


def moved_drawing(ax: Any, drawing: PxpDrawing, delta: tuple[float, float]) -> PxpDrawing:
    transform = drawing_transform(ax, drawing)
    count = 4 if drawing.kind == "DrawArc" else len(drawing.coordinates)
    points = np.asarray(drawing.coordinates[:count]).reshape(-1, 2)
    translated = transform.inverted().transform(transform.transform(points) + delta)
    return replace(drawing, coordinates=tuple(float(v) for v in translated.ravel()) + drawing.coordinates[count:])


class DrawingArtist(Artist):
    """One blitted artist with a display translation; no scene rebuild on motion."""

    def __init__(self, ax: Any, drawing: PxpDrawing, scale: float, index: int) -> None:
        super().__init__()
        self.drawing = drawing
        self.base_transform = drawing_transform(ax, drawing)
        self.position = (0.0, 0.0)
        self.set_figure(ax.figure)
        # The wrapper's position is a DISPLAY-pixel translation, not data.
        # Explicit identity prevents Axes.add_artist from installing transData.
        self.set_transform(IdentityTransform())
        self.set_gid(f"drawing:{index}")
        self.set_clip_on(False)
        self.set_zorder({"ProgBack": 0.2, "UserBack": 0.3, "ProgAxes": 1, "UserAxes": 1.5,
                        "ProgFront": 7, "UserFront": 9, "ProgTop": 12, "UserTop": 13, "Overlay": 20}.get(drawing.layer, 9))
        color = igor_rgb(drawing.color)
        coords = drawing.coordinates
        child: Any
        if drawing.kind == "DrawText":
            anchor = drawing.anchor
            original = Text(*coords[:2], transform=self.base_transform,
                            fontsize=drawing.font_size * scale, fontfamily=drawing.font_family,
                            color=color, rotation=drawing.rotation,
                            ha="left" if anchor.startswith("L") else "right" if anchor.startswith("R") else "center",
                            va="top" if anchor.endswith("T") else "bottom" if anchor.endswith("B") else "center")
            child = IgorText(original, drawing.text, drawing.font_style)
            child.font_scale = scale
        elif drawing.kind == "DrawLine":
            shape = {0: "-", 1: "-|>", 2: "<|-", 3: "<|-|>"}.get(drawing.arrow, "-")
            if drawing.arrow:
                shape += f",head_length=1,head_width={drawing.arrow_fat / 2}"
            child = FancyArrowPatch((coords[0], coords[1]), (coords[2], coords[3]), arrowstyle=shape,
                                    mutation_scale=drawing.arrow_length * scale, shrinkA=0, shrinkB=0)
        elif drawing.kind in {"DrawPoly", "DrawBezier"}:
            points = np.asarray(coords).reshape(-1, 2)
            codes = [MplPath.MOVETO] + [MplPath.CURVE4 if drawing.kind == "DrawBezier" else MplPath.LINETO] * (len(points) - 1)
            child = PathPatch(MplPath(points, codes))
        else:
            x0, y0, x1, y1 = coords[:4]
            x, y, width, height = min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)
            if drawing.kind == "DrawOval":
                child = Ellipse((x + width / 2, y + height / 2), width, height)
            elif drawing.kind == "DrawArc":
                child = Arc((x + width / 2, y + height / 2), width, height,
                            theta1=coords[4] if len(coords) > 4 else 0, theta2=coords[5] if len(coords) > 5 else 180)
            elif drawing.kind == "DrawRRect":
                child = FancyBboxPatch((x, y), width, height, boxstyle=f"round,pad=0,rounding_size={min(width, height) * drawing.round_radius}")
            else:
                child = Rectangle((x, y), width, height)
        child.set_figure(ax.figure)
        child.set_clip_on(False)
        if drawing.kind != "DrawText":
            child.set_edgecolor(color)
            child.set_facecolor(color if drawing.kind == "DrawLine" and drawing.arrow else
                                igor_rgb(drawing.fill_color) if drawing.fill_mode and drawing.kind != "DrawLine" else "none")
            child.set_linewidth(drawing.line_width * scale)
            child.set_capstyle({0: "butt", 1: "round", 2: "projecting"}.get(drawing.line_cap, "butt"))
            child.set_joinstyle({0: "miter", 1: "round", 2: "bevel"}.get(drawing.line_join, "miter"))
            if drawing.line_style:
                from materials_data_studio.tools.pxp_render import _DASH_PATTERNS
                width = max(drawing.line_width, .01)
                child.set_linestyle((0, tuple(v / width for v in _DASH_PATTERNS.get(drawing.line_style, (4, 4)))))
        self.child = child
        self.set_position((0, 0))

    def get_position(self) -> tuple[float, float]:
        return self.position

    def set_position(self, point: Any) -> None:
        self.position = float(point[0]), float(point[1])
        self.child.set_transform(self.base_transform + Affine2D().translate(*self.position))
        self.stale = True

    def get_window_extent(self, renderer: Any = None) -> Bbox:
        return self.child.get_window_extent(renderer).padded(3)

    def contains(self, mouseevent: Any) -> tuple[bool, dict[str, Any]]:
        if self.drawing.kind in {"DrawText", "DrawRect", "DrawRRect", "DrawOval", "DrawArc"}:
            return self.get_window_extent().contains(mouseevent.x, mouseevent.y), {}
        path = self.child.get_path().transformed(self.child.get_transform())
        points = np.array([vertices[-2:] for vertices, _code in path.iter_segments(curves=False)])
        if len(points) < 2:
            return False, {}
        a, b = points[:-1], points[1:]
        delta = b - a
        fraction = np.clip(np.sum((np.array([mouseevent.x, mouseevent.y]) - a) * delta, axis=1) /
                           np.maximum(np.sum(delta * delta, axis=1), 1e-12), 0, 1)
        distance = np.linalg.norm(a + fraction[:, None] * delta - [mouseevent.x, mouseevent.y], axis=1)
        return bool(np.any(distance < 6)), {}

    def draw(self, renderer: Any) -> None:
        if self.get_visible():
            self.child.draw(renderer)
        self.stale = False


class DrawingDialog(QDialog):
    """Create/modify text or drawings without executing Igor commands."""

    def __init__(self, parent: Any, drawing: PxpDrawing) -> None:
        super().__init__(parent)
        self.drawing = drawing
        self.result_drawing: PxpDrawing | None = None
        self.setWindowTitle("Create / Modify " + drawing.kind.removeprefix("Draw"))
        self.resize(620, 650)
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        form = QFormLayout(body)
        scroll.setWidget(body)
        outer.addWidget(scroll)
        self.fields: dict[str, Any] = {}
        for key in ("xcoord", "ycoord"):
            combo = QComboBox()
            combo.addItems(["prel", "axrel", "abs", "bottom" if key == "xcoord" else "left", "top" if key == "xcoord" else "right"])
            combo.setCurrentText(getattr(drawing, key))
            self.fields[key] = combo
            form.addRow("X coordinate system" if key == "xcoord" else "Y coordinate system", combo)
        self.coordinates = QLineEdit(", ".join(f"{v:.15g}" for v in drawing.coordinates))
        form.addRow("Position (x0, y0, x1, y1…)", self.coordinates)
        entries = [("line_width", "Line thickness (pt)"), ("color", "Line / text color (#RRGGBB)"),
                   ("arrow", "Arrow (0 none, 1 end, 2 start, 3 both)"), ("arrow_length", "Arrow length (pt)"),
                   ("arrow_fat", "Arrow fat"), ("line_cap", "Cap (0 flat, 1 round, 2 square)"),
                   ("line_join", "Join (0 miter, 1 round, 2 bevel)"), ("fill_color", "Fill color (#RRGGBB)"),
                   ("round_radius", "Corner radius (fraction)"), ("font_family", "Font"),
                   ("font_size", "Font size (pt)"), ("font_style", "Text style (1 bold, 2 italic, 3 both)"),
                   ("rotation", "Text rotation (deg)")]
        allowed = ({"color", "font_family", "font_size", "font_style", "rotation"} if drawing.kind == "DrawText" else
                   {"line_width", "color", "arrow", "arrow_length", "arrow_fat", "line_cap"} if drawing.kind == "DrawLine" else
                   {"line_width", "color", "line_cap", "line_join", "fill_color", "round_radius"})
        for key, title in entries:
            if key not in allowed:
                continue
            value = getattr(drawing, key)
            if key.endswith("color") or key == "color":
                value = QColor.fromRgbF(*igor_rgb(value)).name()
            edit = FontEdit(str(value)) if key == "font_family" else ColorEdit(str(value)) if "color" in key else QLineEdit(str(value))
            self.fields[key] = edit
            form.addRow(title, edit)
        if drawing.kind != "DrawText":
            self.fields["line_style"] = visual_combo("line", drawing.line_style)
            form.addRow("Dash", self.fields["line_style"])
        anchor = QComboBox()
        anchor.addItems(["LT", "MT", "RT", "LC", "MC", "RC", "LB", "MB", "RB"])
        anchor.setCurrentText(drawing.anchor)
        if drawing.kind == "DrawText":
            self.fields["anchor"] = anchor
            form.addRow("Text anchor", anchor)
        layer = QComboBox()
        layer.addItems(LAYERS)
        layer.setCurrentText(drawing.layer)
        self.fields["layer"] = layer
        form.addRow("Layer", layer)
        fill = QCheckBox("Solid fill")
        fill.setChecked(bool(drawing.fill_mode))
        if drawing.kind not in {"DrawText", "DrawLine", "DrawArc"}:
            self.fields["fill_mode"] = fill
            form.addRow("Fill", fill)
        self.text = QPlainTextEdit(drawing.text)
        self.text.setMinimumHeight(100)
        if drawing.kind == "DrawText":
            form.addRow("Text (Igor formatting escapes supported)", self.text)
        self.text.setEnabled(drawing.kind == "DrawText")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def apply(self) -> None:
        try:
            coordinates = tuple(float(v.strip()) for v in self.coordinates.text().split(","))
            kind = self.drawing.kind
            if (len(coordinates) != (2 if kind == "DrawText" else 4) and kind not in {"DrawPoly", "DrawBezier", "DrawArc"}) or not np.isfinite(coordinates).all():
                raise ValueError("Enter finite X/Y coordinate pairs for this object.")
            if kind in {"DrawPoly", "DrawBezier"} and (len(coordinates) < 4 or len(coordinates) % 2 or (kind == "DrawBezier" and (len(coordinates) // 2 - 1) % 3)):
                raise ValueError("A polygon needs X/Y pairs; a Bezier needs 1 + 3n control points.")
            if kind == "DrawArc" and len(coordinates) not in {4, 6}:
                raise ValueError("Arc: four bounds coordinates, optionally followed by start/end angles.")
            changes: dict[str, Any] = {"coordinates": coordinates, "text": self.text.toPlainText()}
            for key, widget in self.fields.items():
                previous = getattr(self.drawing, key)
                if isinstance(widget, QComboBox):
                    value = widget.currentData() if key == "line_style" else widget.currentText()
                elif isinstance(widget, QCheckBox):
                    value = int(widget.isChecked())
                elif isinstance(previous, tuple):
                    color = QColor(widget.text())
                    if not color.isValid():
                        raise ValueError("Enter a valid #RRGGBB color.")
                    value = tuple(round(v * 65535) for v in (color.redF(), color.greenF(), color.blueF()))
                else:
                    value = (widget.text() if key == "font_family" else int(widget.text())
                             if key in {"arrow", "line_cap", "line_join", "font_style"} else float(widget.text()))
                if isinstance(value, (int, float)) and not np.isfinite(value):
                    raise ValueError("Enter finite settings.")
                changes[key] = value
            if any(changes.get(key, getattr(self.drawing, key)) < 0 for key in ("line_width", "arrow_length", "arrow_fat", "round_radius")) or changes.get("font_size", self.drawing.font_size) <= 0:
                raise ValueError("Sizes must be positive (line/arrow widths can be zero).")
            self.result_drawing = replace(self.drawing, **changes)
        except (TypeError, ValueError) as exc:
            QMessageBox.warning(self, "Drawing", str(exc))
            return
        self.accept()


def drawing_icon(kind: str) -> QIcon:
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor("#64748b"), 1.6))
    if kind == "Text":
        font = QFont("Arial")
        font.setPixelSize(23)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "T")
    elif kind == "Polygon":
        painter.drawPolygon(QPolygonF([QPointF(3, 8), QPointF(12, 3), QPointF(21, 10), QPointF(17, 21), QPointF(5, 18)]))
    elif kind in {"Freehand", "Bezier"}:
        path = QPainterPath(QPointF(3, 19))
        if kind == "Freehand":
            path.cubicTo(1, 4, 13, 23, 9, 8)
            path.cubicTo(6, 1, 22, 4, 20, 19)
        else:
            path.cubicTo(5, 1, 19, 23, 21, 5)
            painter.drawLine(3, 19, 5, 1)
            painter.drawLine(21, 5, 19, 23)
            painter.drawRect(2, 18, 3, 3)
            painter.drawRect(19, 3, 3, 3)
        painter.drawPath(path)
    elif kind in {"Oval", "Arc"}:
        painter.drawEllipse(3, 6, 18, 12) if kind == "Oval" else painter.drawArc(3, 4, 18, 16, 0, 180 * 16)
    elif kind == "Settings":
        for y, x in ((6, 9), (12, 16), (18, 6)):
            painter.drawLine(3, y, 21, y)
            painter.drawEllipse(x - 2, y - 2, 4, 4)
    elif kind in {"Rectangle", "Rounded rectangle", "Grid", "Layers"}:
        painter.drawRoundedRect(3, 4, 18, 16, 3 if kind == "Rounded rectangle" else 0, 3 if kind == "Rounded rectangle" else 0)
        if kind == "Grid":
            for i in (9, 15):
                painter.drawLine(i, 4, i, 20)
                painter.drawLine(3, i, 21, i)
    elif kind == "Pointer":
        painter.drawLine(5, 3, 5, 21)
        painter.drawLine(5, 3, 18, 15)
        painter.drawLine(5, 16, 18, 15)
    else:
        painter.drawLine(4, 20, 20, 4)
        if kind == "Arrow":
            painter.drawLine(20, 4, 12, 5)
            painter.drawLine(20, 4, 19, 12)
    painter.end()
    return QIcon(pixmap)


def install_drawing_toolbar(window: Any) -> None:
    toolbar = QToolBar("Text and Draw", window)
    toolbar.setObjectName("DrawingToolbar")
    toolbar.setIconSize(QSize(20, 20))
    toolbar.setMovable(False)
    toolbar.setStyleSheet("QToolButton:checked { background: #c7e4ff; border: 2px solid #2682c7; border-radius: 4px; } QToolButton:checked:hover { background: #b4d9fa; }")
    window.addToolBar(Qt.ToolBarArea.LeftToolBarArea, toolbar)
    window.drawing_toolbar = toolbar
    group = QActionGroup(toolbar)
    group.setExclusive(True)
    window.drawing_actions = {}
    window.drawing_defaults = PxpDrawing("DrawLine", (0, 0, .2, .2))
    window.drawing_selected = None
    window.drawing_grid = False
    window.drawing_snap = False
    for kind in ("Pointer", *KINDS):
        action = QAction(drawing_icon(kind), kind, toolbar)
        action.setCheckable(True)
        action.setToolTip(kind + (" — click to insert" if kind == "Text" else " — drag to insert" if kind != "Pointer" else " — select / move; double-click to edit"))
        group.addAction(action)
        toolbar.addAction(action)
        window.drawing_actions[kind] = action
        action.triggered.connect(lambda _checked=False, name=kind: window.set_mode(name))
    window.drawing_actions["Pointer"].setChecked(True)
    toolbar.addSeparator()
    defaults = toolbar.addAction(drawing_icon("Settings"), "Drawing defaults…")
    defaults.triggered.connect(lambda: edit_drawing(window, defaults=True))
    layer = QToolButton()
    layer.setToolTip("Drawing layer")
    layer.setIcon(drawing_icon("Layers"))
    layer.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    layer_menu = QMenu(layer)
    layer_group = QActionGroup(layer_menu)
    for name in LAYERS:
        choice = layer_menu.addAction(name)
        choice.setCheckable(True)
        choice.setChecked(name == "UserFront")
        layer_group.addAction(choice)
        choice.triggered.connect(lambda _checked=False, value=name: setattr(window, "drawing_defaults", replace(window.drawing_defaults, layer=value)))
    layer.setMenu(layer_menu)
    toolbar.addWidget(layer)
    arrange = QToolButton()
    arrange.setIcon(drawing_icon("Grid"))
    arrange.setToolTip("Arrange drawings / editing grid")
    arrange.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    menu = QMenu(arrange)
    for title, op in (("Bring to Front", "front"), ("Send to Back", "back"), ("Forward", "forward"), ("Backward", "backward"), ("Duplicate", "duplicate"), ("Delete", "delete"), ("Modify…", "edit")):
        menu.addAction(title).triggered.connect(lambda _checked=False, operation=op: drawing_operation(window, operation))
    layers = menu.addMenu("Move to Layer")
    for name in LAYERS:
        layers.addAction(name).triggered.connect(lambda _checked=False, value=name: drawing_operation(window, "layer", value))
    grid = menu.addMenu("Grid")
    for title, attribute in (("Visible", "drawing_grid"), ("Snap", "drawing_snap")):
        action = grid.addAction(title)
        action.setCheckable(True)
        action.toggled.connect(lambda checked, attr=attribute: toggle_grid(window, attr, checked))
    arrange.setMenu(menu)
    toolbar.addWidget(arrange)


def toggle_grid(window: Any, attribute: str, checked: bool) -> None:
    setattr(window, attribute, checked)
    if attribute == "drawing_grid":
        window.drawing_grid_overlay.setVisible(checked)
        window.drawing_grid_overlay.setGeometry(window.canvas.rect())
        window.drawing_grid_overlay.raise_()


def edit_drawing(window: Any, index: int | None = None, *, defaults: bool = False, new: PxpDrawing | None = None) -> None:
    graph = window.graph()
    if defaults:
        drawing = window.drawing_defaults
    elif new is not None:
        drawing = new
    elif graph is not None and index is not None and 0 <= index < len(graph.drawings):
        drawing = graph.drawings[index]
    else:
        return
    dialog = DrawingDialog(window, drawing)
    if dialog.exec() != QDialog.DialogCode.Accepted or dialog.result_drawing is None:
        return
    if defaults:
        window.drawing_defaults = dialog.result_drawing
        return
    if graph is not None and window.editor is not None:
        drawings = list(graph.drawings)
        if new is not None:
            drawings.append(dialog.result_drawing)
        elif index is not None:
            drawings[index] = dialog.result_drawing
        window.editor.edit_graph(replace(graph, drawings=tuple(drawings)), "Add drawing" if new else "Edit drawing")


def drawing_operation(window: Any, operation: str, layer: str = "", index: int | None = None) -> None:
    graph = window.graph()
    index = window.drawing_selected if index is None else index
    if graph is None or window.editor is None or index is None or not 0 <= index < len(graph.drawings):
        return
    if operation == "edit":
        edit_drawing(window, index)
        return
    drawings = list(graph.drawings)
    drawing = drawings[index]
    if operation in {"copy", "cut"}:
        import json
        from dataclasses import asdict

        from PySide6.QtWidgets import QApplication
        # Qt creates/owns the MIME object for setText. Passing a Python-owned
        # QMimeData to the Windows clipboard can double-delete it on shutdown.
        payload = json.dumps({"mds_drawing": 1, "drawing": asdict(drawing)}, ensure_ascii=False)
        QApplication.clipboard().setText(payload)
        if operation == "copy":
            return
    if operation in {"delete", "clear", "cut"}:
        drawings.pop(index)
    elif operation == "duplicate":
        drawings.append(moved_drawing(window.figure.axes[0], drawing, (12, -12)))
    elif operation == "layer":
        drawings[index] = replace(drawing, layer=layer)
    else:
        drawings.pop(index)
        to = len(drawings) if operation == "front" else 0 if operation == "back" else min(index + 1, len(drawings)) if operation == "forward" else max(0, index - 1)
        drawings.insert(to, drawing)
    window.drawing_selected = None
    window.editor.edit_graph(replace(graph, drawings=tuple(drawings)), operation.title() + " drawing")
