"""Editing-only selection outlines and cubic Bezier control-point gestures."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from materials_data_studio.tools.graph_drawing import DrawingArtist, drawing_transform


def modifier(event: Any, name: str) -> bool:
    flag = Qt.KeyboardModifier.ShiftModifier if name == "shift" else Qt.KeyboardModifier.AltModifier
    return name in str(getattr(event, "key", "")).lower() or bool(
        QApplication.keyboardModifiers() & flag
    )


def guided(window: Any, event: Any, start: Any) -> np.ndarray:
    point = np.array([event.x, event.y], dtype=float)
    delta = point - start
    if modifier(event, "shift"):
        delta = point - start
        point[1 if abs(delta[0]) >= abs(delta[1]) else 0] = start[
            1 if abs(delta[0]) >= abs(delta[1]) else 0
        ]
    if window.drawing_snap and not modifier(event, "alt"):
        point = np.round(point / 10) * 10
        if modifier(event, "shift"):
            point[1 if abs(delta[0]) >= abs(delta[1]) else 0] = start[
                1 if abs(delta[0]) >= abs(delta[1]) else 0
            ]
    return point


class DrawingSelectionOverlay(QWidget):
    def __init__(self, window: Any) -> None:
        super().__init__(window.canvas)
        self._graph_window: Any = window
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        window.canvas.installEventFilter(self)
        window.canvas.mpl_connect("draw_event", lambda _event: self.update())
        self.setGeometry(window.canvas.rect())

    def eventFilter(self, watched: Any, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Resize:
            self.setGeometry(watched.rect())
        return False

    def paintEvent(self, _event: Any) -> None:
        window = self._graph_window
        insertion = window._insert_drag
        if insertion and insertion["mode"] == "Bezier":
            self.paint_insertion(insertion)
            return
        index = window.drawing_selected
        artist = next(
            (a for a, target in window.artist_targets.items() if target == ("drawing", str(index))),
            None,
        )
        if index is None or artist is None or window.graph() is None:
            return
        painter = QPainter(self)
        painter.setPen(QPen(QColor("#246bff"), 1))
        painter.setBrush(QColor("#ffffff"))
        ratio = window.canvas.device_pixel_ratio
        height = window.figure.bbox.height

        def point(xy: Any) -> QPointF:
            return QPointF(float(xy[0]) / ratio, (height - float(xy[1])) / ratio)

        box = artist.get_window_extent(window.canvas.get_renderer())
        rectangle = QRectF(point((box.x0, box.y1)), point((box.x1, box.y0)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rectangle)
        painter.setBrush(QColor("#ffffff"))
        for corner in (
            rectangle.topLeft(),
            rectangle.topRight(),
            rectangle.bottomLeft(),
            rectangle.bottomRight(),
        ):
            painter.drawRect(QRectF(corner.x() - 2, corner.y() - 2, 4, 4))
        drawing = getattr(artist, "drawing", None)
        if window.mode == "Bezier" and drawing and drawing.kind == "DrawBezier":
            vertices = artist.base_transform.transform(
                np.asarray(drawing.coordinates).reshape(-1, 2)
            )
            for i in range(0, len(vertices), 3):
                for j in (i - 1, i + 1):
                    if 0 <= j < len(vertices):
                        painter.drawLine(point(vertices[i]), point(vertices[j]))
            for i, xy in enumerate(vertices):
                p = point(xy)
                painter.setBrush(QColor("#ffffff" if i % 3 == 0 else "#e9416a"))
                painter.drawRect(QRectF(p.x() - 3, p.y() - 3, 6, 6))
        painter.end()

    def insertion_geometry(self, drag: Any) -> tuple[np.ndarray, np.ndarray]:
        transform = drawing_transform(drag["ax"], drag["defaults"])
        nodes = np.asarray(drag["points"])
        vectors = np.asarray(drag["vectors"])
        return transform.transform(nodes), transform.transform(
            np.stack((nodes - vectors, nodes + vectors), axis=1).reshape(-1, 2)
        ).reshape(-1, 2, 2)

    def paint_insertion(self, drag: Any) -> None:
        window = self._graph_window
        nodes, handles = self.insertion_geometry(drag)
        ratio, height = window.canvas.device_pixel_ratio, window.figure.bbox.height
        def point(xy: Any) -> QPointF:
            return QPointF(float(xy[0]) / ratio, (height - float(xy[1])) / ratio)
        painter = QPainter(self)
        painter.setPen(QPen(QColor("#246bff"), 1))
        for node, pair in zip(nodes, handles, strict=True):
            for handle in pair:
                if np.linalg.norm(handle - node) < .5:
                    continue
                painter.drawLine(point(node), point(handle))
                painter.setBrush(QColor("#e9416a"))
                p = point(handle)
                painter.drawRect(QRectF(p.x() - 3, p.y() - 3, 6, 6))
            painter.setBrush(QColor("#ffffff"))
            p = point(node)
            painter.drawRect(QRectF(p.x() - 3, p.y() - 3, 6, 6))
        painter.end()


def begin_edit(window: Any, event: Any) -> bool:
    """Try handles, then the curve; a blank click starts a new drawing."""
    graph = window.graph()
    if graph is None:
        return False
    index = window.drawing_selected
    if index is not None and index < len(graph.drawings):
        drawing = graph.drawings[index]
        if drawing.kind == "DrawBezier":
            ax = window.figure.axes[0]
            pixels = drawing_transform(ax, drawing).transform(
                np.asarray(drawing.coordinates).reshape(-1, 2)
            )
            distances = np.linalg.norm(pixels - [event.x, event.y], axis=1)
            handle = int(np.argmin(distances))
            if distances[handle] <= 9 * window.canvas.device_pixel_ratio:
                artist = next(
                    a
                    for a, target in window.artist_targets.items()
                    if target == ("drawing", str(index))
                )
                artist.set_visible(False)
                window.canvas.draw()
                window.canvas._draw_pending = False
                artist.set_visible(True)
                window._bezier_drag = dict(
                    index=index,
                    handle=handle,
                    drawing=drawing,
                    ax=ax,
                    pixels=pixels,
                    start=np.array([event.x, event.y]),
                    background=window.canvas.copy_from_bbox(window.figure.bbox),
                    draft=drawing,
                )
                return True
    for artist, target in window.artist_targets.items():
        if (
            target[0] == "drawing"
            and artist.drawing.kind == "DrawBezier"
            and artist.contains(event)[0]
        ):
            window.drawing_selected = int(target[1])
            window.select_target(*target)
            return True
    return False


def move_edit(window: Any, event: Any) -> None:
    drag = window._bezier_drag
    original = drag["pixels"]
    index = drag["handle"]
    position = guided(window, event, original[index])
    points = original.copy()
    delta = position - original[index]
    points[index] = position
    if index % 3 == 0:
        for neighbor in (index - 1, index + 1):
            if 0 <= neighbor < len(points):
                points[neighbor] += delta
    else:
        node = index - 1 if index % 3 == 1 else index + 1
        opposite = 2 * node - index
        if 0 <= opposite < len(points) and not modifier(event, "alt"):
            points[opposite] = 2 * points[node] - position
    native = drawing_transform(drag["ax"], drag["drawing"]).inverted().transform(points)
    drag["draft"] = replace(drag["drawing"], coordinates=tuple(float(v) for v in native.ravel()))
    if not window.drag_paint_timer.isActive():
        window.drag_paint_timer.start()


def paint_edit(window: Any) -> None:
    drag = window._bezier_drag
    window.canvas.restore_region(drag["background"])
    from materials_data_studio.tools.pxp_render import _visual_scale

    artist = DrawingArtist(
        drag["ax"], drag["draft"], _visual_scale(window.figure, window.graph()), drag["index"]
    )
    window.figure.draw_artist(artist)
    original = next(
        a
        for a, target in window.artist_targets.items()
        if target == ("drawing", str(drag["index"]))
    )
    original.drawing = drag["draft"]
    original.child = artist.child
    window.canvas.blit(window.figure.bbox)
    window.drawing_selection_overlay.update()


def finish_edit(window: Any) -> None:
    drag, window._bezier_drag = window._bezier_drag, None
    window.drag_paint_timer.stop()
    graph = window.graph()
    drawings = tuple(
        drag["draft"] if i == drag["index"] else d for i, d in enumerate(graph.drawings)
    )
    window.editor.edit_graph(replace(graph, drawings=drawings), "Edit Bezier node / tangent")
    window.drawing_selected = drag["index"]
    window.select_target("drawing", str(drag["index"]))
