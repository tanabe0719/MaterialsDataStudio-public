"""Igor fidelity adapter for canonical editable documents and logical positions."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.legend import Legend
from matplotlib.ticker import AutoMinorLocator, LogLocator, MaxNLocator, MultipleLocator

from materials_data_studio.tools.graph_document import EditableGraphDocument
from materials_data_studio.tools.pxp_model import PxpGraph
from materials_data_studio.tools.pxp_render import (
    _AnnotationFrame,
    _plot_dimensions,
    _visual_scale,
    render_graph,
)
from materials_data_studio.tools.pxp_text import IgorText


def explicit_font(raw: str, size: float | None = None, family: str | None = None) -> str:
    """Change only requested inline font commands; preserve scripts and symbols."""
    if size is not None:
        raw = re.sub(r"\\Z\d{1,2}", "", raw)
    if family is not None:
        raw = re.sub(r"\\F'[^']*'", "", raw)
    return raw


def label_position(ax: Any, horizontal: bool, x: float, y: float,
                   dpi: float, scale: float, side: str) -> dict[str, float | str]:
    """Display coordinates to native points + normalized distance along an axis."""
    u, v = ax.transAxes.inverted().transform((x, y))
    edge = 1 if (ax.xaxis if horizontal else ax.yaxis).get_label_position() in {"top", "right"} else 0
    fraction = v - edge if horizontal else u - edge
    extent = ax.bbox.height if horizontal else ax.bbox.width
    sign = 1 if edge else -1
    return {"coordinate_system": "axis_label", "offset_points": float(sign * fraction * extent * 72 / dpi / scale),
            "lateral": float(u if horizontal else v)}


class IgorMatplotlibRenderer:
    """No automatic layout: imported geometry, ticks and explicit anchors win."""

    def render(self, figure: Figure, document: EditableGraphDocument, name: str) -> dict[Any, tuple[str, str]]:
        graph = document.graph(name)
        layout = document.current.layout.get(name, {})
        view = layout.get("view", {})
        # Per-object visibility is distinct from the legacy all-annotations toggle.
        annotations = tuple(a for a in graph.annotations if layout.get("annotations", {}).get(a.name, {}).get("visible", True))
        render_graph(figure, document.render_source(), replace(graph, annotations=annotations,
                     legend_raw="" if graph.annotations else graph.legend_raw), view)
        if graph.window_kind == "Layout":
            return {}
        scale = _visual_scale(figure, graph)
        mapping: dict[Any, tuple[str, str]] = {}
        y_names = list(dict.fromkeys(trace.axis for trace in graph.traces)) or ["left"]
        plot_axes = [ax for ax in figure.axes if ax.figure is figure and not str(ax.get_gid() or "").startswith("colorbar:")]
        for index, ax in enumerate(plot_axes):
            y_name = str(ax.get_gid()).split(":", 1)[1] if str(ax.get_gid()).startswith("plotaxis:") else y_names[min(index, len(y_names) - 1)]
            x_name, y_name = getattr(ax, "_mds_axis_names", ("bottom", y_name))
            pairs = ((ax.xaxis, y_name if graph.swap_xy else x_name, True),
                     (ax.yaxis, x_name if graph.swap_xy else y_name, False))
            for axis, identifier, horizontal in pairs:
                if not axis.get_visible():
                    continue
                axis.label.axes = ax
                mapping[axis.label] = ("label", identifier)
                for tick in axis.get_major_ticks() + axis.get_minor_ticks():
                    for artist in (tick.label1, tick.label2, tick.tick1line, tick.tick2line):
                        mapping[artist] = ("axis", identifier)
                position = layout.get("labels", {}).get(identifier)
                if position:
                    offset = position["offset_points"] * scale * figure.dpi / 72
                    edge = 1 if axis.get_label_position() in {"right", "top"} else 0
                    direction = 1 if edge else -1
                    if horizontal:
                        axis.set_label_coords(position["lateral"], edge + direction * offset / ax.bbox.height)
                    else:
                        axis.set_label_coords(edge + direction * offset / ax.bbox.width, position["lateral"])
            for side, spine in ax.spines.items():
                mapping[spine] = ("axis", (y_name if graph.swap_xy else x_name) if side in {"bottom", "top"} else (x_name if graph.swap_xy else y_name))
        for artist in figure.findobj():
            gid = artist.get_gid()
            if not gid or ":" not in gid:
                continue
            kind, identifier = gid.split(":", 1)
            if kind == "colorbar" and identifier.startswith("trace:"):
                mapping[artist] = ("trace", identifier.removeprefix("trace:"))
                continue
            if kind == "plotaxis":
                continue
            mapping[artist] = kind, identifier
            if kind == "annotation" and isinstance(artist, _AnnotationFrame):
                settings = layout.get("annotations", {}).get(identifier, {})
                artist.text.point_label = bool(settings.get("point_label") or re.fullmatch(r"MDSIndex\d+", identifier))
                if "font" in settings:
                    artist.text.set_fontfamily(settings["font"])
                if "rotation" in settings:
                    artist.text.set_rotation(settings["rotation"])
            if kind == "legend" and isinstance(artist, Legend):
                from materials_data_studio.tools.pxp_style import igor_rgb
                artist.get_frame().set_edgecolor(igor_rgb(graph.legend_style.frame_color))
                artist.get_frame().set_linewidth(graph.legend_style.frame_width * scale)
                artist.get_frame().set_facecolor(igor_rgb(graph.legend_style.background_color))
                settings = layout.get("annotations", {}).get(identifier, {})
                for text in artist.get_texts():
                    if settings.get("font"):
                        text.set_fontfamily(settings["font"])
                    if "rotation" in settings:
                        text.set_rotation(settings["rotation"])
            if kind == "colorbar" and isinstance(artist, Axes):
                settings = layout.get("colorbars", {}).get(identifier, {})
                font = settings.get("font")
                size = settings.get("label_size")
                if font or size:
                    for axis in (artist.xaxis, artist.yaxis):
                        if font:
                            axis.label.set_fontfamily(font)
                            for text in axis.get_ticklabels():
                                text.set_fontfamily(font)
                        if size:
                            if isinstance(axis.label, IgorText):
                                axis.label.raw = explicit_font(axis.label.raw, size=size)
                            axis.label.set_fontsize(size * scale)
                position = settings.get("position")
                if position:
                    base = plot_axes[0].get_position()
                    box = artist.get_position()
                    artist.set_position((base.x0 + position[0] * base.width,
                                         base.y0 + position[1] * base.height, box.width, box.height))
                ticks = settings.get("ticks", {})
                if ticks:
                    image = next(i for i in graph.images if i.wave == identifier)
                    axis = artist.yaxis if image.colorbar_style.vertical else artist.xaxis
                    if ticks.get("interval"):
                        axis.set_major_locator(MultipleLocator(ticks["interval"]))
                    elif ticks.get("count") is not None and axis.get_scale() != "log":
                        axis.set_major_locator(MaxNLocator(nbins=ticks["count"]))
                    if ticks.get("minor"):
                        axis.set_minor_locator(LogLocator(subs="auto") if axis.get_scale() == "log" else AutoMinorLocator(ticks.get("minor_count", 4)))
                    else:
                        axis.set_minor_locator(MultipleLocator(1e99))
                    artist.tick_params(axis="both", which="major", length=ticks.get("length", 2) * scale,
                                       width=ticks.get("width", 0.5) * scale, labelrotation=ticks.get("rotation", 0))
                    artist.tick_params(axis="both", which="minor", length=ticks.get("minor_length", 1) * scale,
                                       width=ticks.get("minor_width", 0.5) * scale)
                    if not ticks.get("visible", True):
                        artist.tick_params(axis="both", which="both", length=0, labelleft=False, labelbottom=False, labelright=False, labeltop=False)
                if "rotation" in settings:
                    image = next(i for i in graph.images if i.wave == identifier)
                    (artist.yaxis if image.colorbar_style.vertical else artist.xaxis).label.set_rotation(settings["rotation"])
        return mapping

    @staticmethod
    def export_size(graph: PxpGraph) -> tuple[float, float]:
        from materials_data_studio.tools.pxp_render import _graph_margins

        width, height = _plot_dimensions(graph) or (432, 288)
        margins = _graph_margins(graph)
        return ((width + margins["left"] + margins["right"]) / 72,
                (height + margins["top"] + margins["bottom"]) / 72)
