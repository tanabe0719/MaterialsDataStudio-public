"""Mouse-first drawing gestures and coalesced, object-only live previews."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np

from materials_data_studio.tools.graph_drawing import KINDS, DrawingArtist, drawing_transform
from materials_data_studio.tools.graph_handles import guided, modifier
from materials_data_studio.tools.pxp_render import _visual_scale


def point(window: Any, event: Any) -> tuple[float, float]:
    drag = window._insert_drag
    pixels = guided(window, event, drag.get("guide", drag["start"]))
    xy = drawing_transform(drag["ax"], drag["defaults"]).inverted().transform(pixels)
    if (
        window.drawing_snap
        and not modifier(event, "alt")
        and drag["defaults"].xcoord == drag["defaults"].ycoord == "prel"
    ):
        xy = np.round(xy * 20) / 20
    return float(xy[0]), float(xy[1])


def begin(window: Any, event: Any) -> None:
    if window._insert_drag and window._insert_drag["mode"] == "Bezier":
        drag = window._insert_drag
        xy = point(window, event)
        if xy != drag["points"][-1]:
            drag["points"].append(xy)
            drag["vectors"].append((0.0, 0.0))
        drag["pressed"] = True
        drag["guide"] = (event.x, event.y)
        if event.dblclick:
            finish(window)
        else:
            window.drawing_selection_overlay.update()
            window.drag_paint_timer.start()
        return
    if window._insert_drag and window._insert_drag["mode"] == "Polygon":
        xy = point(window, event)
        if xy != window._insert_drag["points"][-1]:
            window._insert_drag["points"].append(xy)
        window._insert_drag["end"] = xy
        if event.dblclick:
            finish(window)
        else:
            update(window, event)
        return
    window.canvas.draw()
    window.canvas._draw_pending = False
    window._insert_drag = {
        "ax": event.inaxes,
        "defaults": window.drawing_defaults,
        "start": (event.x, event.y),
        "mode": window.mode,
    }
    xy = point(window, event)
    window._insert_drag.update(
        point=xy, end=xy, points=[xy], background=window.canvas.copy_from_bbox(window.figure.bbox)
    )
    if window.mode == "Bezier":
        window._insert_drag.update(vectors=[(0.0, 0.0)], pressed=True, guide=(event.x, event.y))
        window.drawing_selection_overlay.update()
        window.drag_paint_timer.start()
    window.statusBar().showMessage(
        "Polygon: click vertices, double-click / Enter to finish; Esc to cancel"
        if window.mode == "Polygon"
        else "Bezier: click nodes, drag tangents, double-click / Enter to finish; Esc to cancel"
        if window.mode == "Bezier"
        else "Drag to draw; Shift constrains; Alt bypasses snap; Esc cancels"
    )


def draft(window: Any) -> Any:
    drag = window._insert_drag
    start, end, mode = drag["point"], drag["end"], drag["mode"]
    coordinates = (*start, *end)
    if mode in {"Polygon", "Freehand"}:
        coordinates = tuple(v for xy in [*drag["points"], end] for v in xy)
    elif mode == "Bezier":
        points, vectors = np.array(drag["points"]), np.array(drag["vectors"])
        coordinates = list(points[0])
        for i in range(len(points) - 1):
            coordinates.extend(
                (*(points[i] + vectors[i]), *(points[i + 1] - vectors[i + 1]), *points[i + 1])
            )
        if len(points) == 1:
            coordinates *= 4  # Valid degenerate cubic preview, never committed.
        coordinates = tuple(float(v) for v in coordinates)
    return replace(
        drag["defaults"],
        kind=KINDS[mode],
        coordinates=coordinates,
        arrow=1 if mode == "Arrow" else 0 if mode == "Line" else drag["defaults"].arrow,
    )


def update(window: Any, event: Any) -> None:
    drag = window._insert_drag
    xy = point(window, event)
    if drag["mode"] == "Bezier":
        if drag["pressed"]:
            drag["vectors"][-1] = tuple(np.array(xy) - drag["points"][-1])
        if not window.drag_paint_timer.isActive():
            window.drag_paint_timer.start()
        return
    if drag["mode"] == "Freehand" and len(drag["points"]) < 10000 and xy != drag["points"][-1]:
        drag["points"].append(xy)
    drag["end"] = xy
    if not window.drag_paint_timer.isActive():
        window.drag_paint_timer.start()


def paint(window: Any) -> None:
    drag = window._insert_drag
    if not drag:
        return
    window.canvas.restore_region(drag["background"])
    artist = DrawingArtist(
        drag["ax"], draft(window), _visual_scale(window.figure, window.graph()), -1
    )
    window.figure.draw_artist(artist)
    window.canvas.blit(window.figure.bbox)
    window.drawing_selection_overlay.update()


def finish(window: Any, event: Any = None) -> None:
    drag = window._insert_drag
    if not drag:
        return
    if event is not None and event.x is not None and event.y is not None:
        update(window, event)
    if drag["mode"] == "Bezier" and event is not None:
        drag["pressed"] = False
        return
    if drag["mode"] == "Polygon" and event is not None:
        return  # Vertex clicks continue until a double-click/Enter.
    drawing = draft(window)
    if drag["mode"] == "Polygon":
        vertices = list(dict.fromkeys(drag["points"]))
        if len(vertices) < 3:
            return
        drawing = replace(
            drawing, coordinates=tuple(v for xy in [*vertices, vertices[0]] for v in xy)
        )
    elif drag["mode"] == "Bezier":
        if len(drag["points"]) < 2:
            return
    elif np.allclose(drag["point"], drag["end"]) and drag["mode"] != "Freehand":
        window._insert_drag = None
        window.canvas.draw_idle()
        return
    window._insert_drag = None
    window.drag_paint_timer.stop()
    graph = window.graph()
    if graph is not None:
        window.editor.edit_graph(replace(graph, drawings=(*graph.drawings, drawing)), "Add drawing")
        window.drawing_selected = len(graph.drawings)
        window._property_target = "drawing", str(window.drawing_selected)
        window._selected_object = "drawing", str(window.drawing_selected)
    window.set_mode("Pointer")
    window.drawing_selection_overlay.update()
    window.statusBar().clearMessage()
