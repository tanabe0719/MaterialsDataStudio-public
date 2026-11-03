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

    def update_tag_positions(self, figure, previous, graph, layout, document) -> bool:
        """Move attached text without rebuilding axes or changing plot geometry."""
        old, old_layout, old_waves = previous
        if layout != old_layout or len(document.waves) != len(old_waves) or any(a is not b for a, b in zip(document.waves, old_waves, strict=True)):
            return False
        if replace(old, annotations=graph.annotations) != graph or len(old.annotations) != len(graph.annotations):
            return False
        changes = [(a, b) for a, b in zip(old.annotations, graph.annotations, strict=True) if a != b]
        if not changes:
            return False
        frames = {artist.get_gid(): artist for ax in figure.axes for artist in ax.artists if isinstance(artist, _AnnotationFrame)}
        for before, after in changes:
            if not after.trace or after.point is None or after.arrow or replace(before, style=after.style) != after:
                return False
            if replace(before.style, x_percent=after.style.x_percent, y_percent=after.style.y_percent) != after.style or "annotation:" + after.name not in frames:
                return False
        from materials_data_studio.tools.igor_graph_extensions import place_tag
        axes = {str(ax.get_gid()).removeprefix("plotaxis:"): ax for ax in figure.axes if str(ax.get_gid()).startswith("plotaxis:")}
        for _before, after in changes:
            frame = frames["annotation:" + after.name]
            frame.style = after.style
            place_tag(frame, axes, document, graph, after)
        return True

    def update_trace_styles(self, figure, previous, graph, layout, waves) -> bool:
        """Reuse plain XY artists only when data, axes and structure are identical."""
        from matplotlib.lines import Line2D

        from materials_data_studio.tools.pxp_markers import marker_path
        from materials_data_studio.tools.pxp_render import (
            _DASH_PATTERNS,
            FILLED_MARKERS,
            marker_size,
        )
        from materials_data_studio.tools.pxp_style import igor_rgb
        old, old_layout, old_waves = previous
        if layout != old_layout or len(waves) != len(old_waves) or any(a is not b for a, b in zip(waves, old_waves, strict=True)) or replace(old, traces=graph.traces) != graph or len(old.traces) != len(graph.traces):
            return False
        if graph == old:
            return True
        visual = ("line_width", "line_style", "marker", "marker_size", "marker_thickness", "color_igor", "marker_fill_igor", "marker_stroke_igor", "opaque", "use_marker_stroke")
        for a, b in zip(old.traces, graph.traces, strict=True):
            if replace(a, **{key: getattr(b, key) for key in visual}) != b:
                return False
            if b.mode not in {None, 0, 2, 3, 4} or any((b.z_color, b.z_size, b.z_marker, b.error_low, b.error_high, b.x_error_low, b.x_error_high, b.box, b.violin)):
                return False
        artists = {a.get_gid(): a for ax in figure.axes for a in ax.lines if isinstance(a, Line2D)}
        changed = [(a, b) for a, b in zip(old.traces, graph.traces, strict=True) if a != b]
        if any("trace:" + b.name not in artists for a, b in changed):
            return False
        if graph.annotations and any(replace(a, color_igor=b.color_igor, line_width=b.line_width, line_style=b.line_style) != b for a, b in changed):
            return False
        legends = [ax.get_legend() for ax in figure.axes if ax.get_legend() is not None]
        if graph.legend_raw and not graph.annotations and (not legends or any(len(legend.legend_handles) != len(graph.traces) for legend in legends)):
            return False
        scale = _visual_scale(figure, graph)
        for _a, b in changed:
            line = artists["trace:" + b.name]
            spec = layout.get("view", {}).get("traces", {}).get(b.name, {})
            color = spec.get("color", igor_rgb(b.color_igor))
            fill = color if b.marker in FILLED_MARKERS or b.mode == 2 else igor_rgb(b.marker_fill_igor) if b.opaque and b.marker_fill_igor else "white" if b.opaque else "none"
            stroke = (igor_rgb(b.marker_stroke_igor) if b.marker_stroke_igor else "black") if b.use_marker_stroke else color
            line.set_color(color)
            line.set_linewidth(spec.get("line_width", b.line_width if b.line_width is not None else 1) * scale)
            line.set_linestyle("None" if b.mode in {2, 3} else (0, dict(graph.style.dash_patterns).get(b.line_style, _DASH_PATTERNS.get(b.line_style, ()))) if b.line_style else "-")
            line.set_marker(spec.get("marker", "." if b.mode == 2 else marker_path(b.marker) if b.mode in {3, 4} else ""))
            line.set_markersize((2 * marker_size(graph, spec.get("marker_size", b.marker_size)) + 1) * scale)
            line.set_markerfacecolor(spec.get("marker_fill", fill))
            line.set_markeredgecolor(spec.get("marker_edge", stroke))
            line.set_markeredgewidth((b.marker_thickness if b.marker_thickness is not None else .5) * scale)
        for legend in legends:
            for trace, handle in zip(graph.traces, legend.legend_handles, strict=True):
                line = artists.get("trace:" + trace.name)
                if isinstance(handle, Line2D) and line is not None:
                    for property_name in ("color", "linewidth", "linestyle", "marker", "markersize", "markerfacecolor", "markeredgecolor", "markeredgewidth"):
                        getattr(handle, "set_" + property_name)(getattr(line, "get_" + property_name)())
        return True

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
