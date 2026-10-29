"""Bounded semantic contour and page layout operations (no executable macros)."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np

from materials_data_studio.tools.command_language import split
from materials_data_studio.tools.pxp_model import PxpContour, PxpLayoutItem


def canvas_command(engine: Any, command: Any, flag: Any, numeric_flag: Any) -> None:
    services, args = engine.services, command.arguments
    if command.name == "newlayout":
        graph = services.new_graph(flag("N", "Layout0"))
        services.put_graph(replace(graph, window_kind="Layout", style=replace(graph.style, window_size=(612, 792))))
        return
    graph = services.graph()
    if command.name == "appendlayoutobject":
        if graph.window_kind != "Layout" or len(args) != 2 or engine.text(args[0]).lower() != "graph":
            raise ValueError("AppendLayoutObject requires a Layout and graph object")
        child = services.graph(engine.text(args[1]))
        if child.window_kind != "Graph":
            raise ValueError("Only graph objects can be appended; nested layouts are unsupported")
        rect = tuple(map(float, numeric_flag("R", (36, 36, 576, 396))))
        width, height = graph.style.window_size or (612, 792)
        if len(rect) != 4 or not np.isfinite(rect).all() or not 0 <= rect[0] < rect[2] <= width or not 0 <= rect[1] < rect[3] <= height:
            raise ValueError("Layout rectangle must lie inside the page in points")
        if len(graph.layout_items) >= 32:
            raise ValueError("Layout is limited to 32 graph objects")
        services.put_graph(replace(graph, layout_items=(*graph.layout_items, PxpLayoutItem(child.name, rect))))
    elif command.name == "appendmatrixcontour":
        wave = services.wave(engine.text(args[0]))
        if wave.data.ndim != 2 or wave.data.dtype.kind not in "biuf" or min(wave.shape) < 2:
            raise ValueError("Matrix contour requires a real matrix with at least 2 rows and columns")
        services.put_graph(replace(graph, contours=(*graph.contours, PxpContour(wave.igor_path))))
    elif command.name == "modifycontour":
        target, settings = args
        contour = next((c for c in graph.contours if c.wave.rsplit(":", 1)[-1] == target.path or c.wave == target.path), None)
        if contour is None:
            raise ValueError("Contour does not exist")
        contour = modify_contour(contour, settings, engine.number)
        services.put_graph(replace(graph, contours=tuple(contour if c.wave == contour.wave else c for c in graph.contours)))



def modify_contour(contour: Any, settings: Any, evaluate: Any) -> Any:
    for setting in settings:
        if setting.name.lower() in {"ctablines", "ctabfill"}:
            from materials_data_studio.tools.pxp_style import colormap
            parts = split(setting.value.strip("{}"))
            if len(parts) != 4 or parts[:2] != ["*", "*"] or parts[3] not in {"0", "1"}:
                raise ValueError("Contour palettes support automatic limits and reverse 0|1")
            contour = replace(contour, color_table=colormap(parts[2], reverse=parts[3] == "1"))
            continue
        value = evaluate(setting.expression)
        if setting.name.lower() in {"manlevels", "morelevels"}:
            levels = tuple(map(float, value)) if np.ndim(value) else ()
            if setting.name.lower() == "manlevels" and setting.value.startswith("{"):
                if len(levels) != 3 or levels[2] != int(levels[2]) or not 1 <= levels[2] <= 1024 or levels[1] <= 0:
                    raise ValueError("manLevels={first,increment,count} requires positive increment and count 1..1024")
                levels = tuple(levels[0] + levels[1]*np.arange(int(levels[2])))
            if not 1 <= len(levels) <= 1024 or not np.isfinite(levels).all() or not np.all(np.diff(levels) > 0):
                raise ValueError("Contour levels must be finite and strictly increasing")
            if setting.name.lower() == "morelevels":
                if contour.auto_levels:
                    raise ValueError("Disable automatic levels with autoLevels={0,0,0} before moreLevels")
                levels = tuple(sorted(set((*contour.levels, *levels))))
            contour = replace(contour, levels=levels, auto_levels=False)
        elif setting.name.lower() == "autolevels" and tuple(value) == (0,0,0):
            contour = replace(contour, levels=(), auto_levels=False)
        elif setting.name.lower() == "fill" and value in {0, 1}:
            contour = replace(contour, filled=bool(value))
        else:
            raise ValueError("ModifyContour supports manLevels, autoLevels={0,0,0}, moreLevels and fill=0|1")
    if contour.filled and not contour.auto_levels and len(contour.levels) == 1:
        raise ValueError("Filled contours require at least two explicit levels")
    return contour

def render_contours(ax: Any, document: Any, graph: Any) -> None:
    for contour in graph.contours:
        wave = document.wave(contour.wave)
        if wave is None:
            continue
        if not contour.auto_levels and not contour.levels:
            continue
        x, y, data = wave.coordinate(0), wave.coordinate(1), wave.data.T
        if graph.swap_xy:
            x, y, data = y, x, data.T
        artist = (ax.contourf if contour.filled else ax.contour)(x, y, data, levels=contour.levels or 11, cmap=contour.color_table)
        artist.set_gid("contour:" + contour.wave)


def render_layout(figure: Any, document: Any, layout: Any) -> None:
    from matplotlib.gridspec import GridSpec

    from materials_data_studio.tools.pxp_render import render_graph
    width, height = layout.style.window_size or (612, 792)
    for item in layout.layout_items:
        child = next((g for g in document.graphs if g.name == item.graph and g.window_kind == "Graph"), None)
        if child is None:
            continue
        left, top, right, bottom = item.rect
        grid = GridSpec(1, 1, figure=figure, left=left/width, right=right/width, bottom=1-bottom/height, top=1-top/height)
        sub = figure.add_subfigure(grid[0])
        # SubFigure uses GridSpec cell ratios, ignoring its outer margins.
        # Set the semantic page rectangle explicitly before point layout.
        sub.bbox_relative.set_points(np.array([[left/width, 1-bottom/height], [right/width, 1-top/height]]))
        # Matplotlib SubFigure exposes its physical bbox, but lacks the Figure
        # size accessor used by the shared point geometry resolver.
        sub.get_size_inches = lambda sub=sub: np.array((sub.bbox.width, sub.bbox.height))/sub.dpi
        render_graph(sub, document, child)


def render_subwindows(figure: Any, document: Any, host: Any) -> None:
    children = [g for g in document.graphs if g.host == host.name and g.host_rect]
    if children:
        page = replace(host, layout_items=tuple(PxpLayoutItem(g.name, g.host_rect) for g in children))
        render_layout(figure, document, page)
