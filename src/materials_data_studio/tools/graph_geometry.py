"""Point-based semantic label geometry and measured automatic margins.

The drawable data rectangle (psize) and stood-off axis rectangle (axsize) are
distinct. Geometry readback is evidence, not a Matplotlib axes bounding box.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np


def zero_advance(style: Any) -> float:
    from matplotlib.font_manager import FontProperties
    from matplotlib.textpath import TextToPath
    return float(TextToPath().get_text_width_height_descent(
        "0", FontProperties(family=style.font_family, size=style.font_size), False)[0])


def place_free_axes(figure: Any, graph: Any, scale: float) -> None:
    from materials_data_studio.tools.pxp_render import _style
    axes = [a for a in figure.axes if hasattr(a, "_mds_axis_names")]
    for ax in axes:
        pair = ax._mds_axis_names[::-1] if graph.swap_xy else ax._mds_axis_names
        for horizontal, name in zip((True, False), pair, strict=True):
            model = next((a for a in graph.axes if a.name == name), None)
            if model is None or model.free_position is None:
                continue
            component = ax.xaxis if horizontal else ax.yaxis
            side = component.get_label_position()
            style = _style(graph, name)
            outward = (style.axis_offset or 0) * zero_advance(style)
            position = model.free_position
            if isinstance(position, tuple):
                value, cross = position
                if cross == "kwFraction":
                    fraction = 1-value if side in {"top","right"} else value
                else:
                    matching = [(other, other._mds_axis_names[::-1] if graph.swap_xy else other._mds_axis_names) for other in axes]
                    target = next(((other, names.index(cross)) for other, names in matching if cross in names), None)
                    if target is None or target[1] == (0 if horizontal else 1):
                        raise ValueError("Free axis needs an existing perpendicular crossing axis")
                    other, index = target
                    other_axis = other.xaxis if index == 0 else other.yaxis
                    transform = other_axis.get_transform()
                    lo, hi = other_axis.get_view_interval()
                    limits = np.asarray(transform.transform(np.array([lo,hi])), float)
                    transformed = float(transform.transform(value))
                    fraction = (transformed-limits[0])/(limits[1]-limits[0])
                    if not np.isfinite(fraction):
                        raise ValueError("Invalid/logarithmically nonpositive axis crossing")
                extent = (ax.bbox.height if horizontal else ax.bbox.width) * 72/figure.dpi
                fraction += (1 if side in {"top","right"} else -1) * outward * scale/extent
                ax.spines[side].set_position(("axes", fraction))
            else:
                ax.spines[side].set_position(("outward", (position+outward)*scale))


def position_label(ax: Any, axis: Any, style: Any, graph: Any, scale: float) -> None:
    horizontal = axis is ax.xaxis
    side = axis.get_label_position()
    bounds = ax.get_position()
    fw, fh = ax.figure.get_size_inches() * 72
    extent = bounds.width * fw if horizontal else bounds.height * fh
    sign = 1 if side in {"right", "top"} else -1
    mode = style.label_position_mode or 0
    free_side = any(a.name not in {"left", "right", "top", "bottom"} and (a.side or "left") == side for a in graph.axes)
    axis_mode = mode in {3, 4} or (mode == 0 and free_side)
    factor = (style.font_size or 12) / 12 if mode in {2, 4} else 1
    lateral = (style.label_lateral_position or 0) * scale * factor
    along = style.label_lateral_fraction if style.label_lateral_fraction is not None else .5 + lateral / extent * (1 if horizontal else -1)
    if axis_mode and style.label_position:
        distance = style.label_position * scale * factor
        # Include a shifted/free spine's actual display position.
        spine = ax.spines[side]
        vertices = spine.get_transform().transform(spine.get_path().vertices)
        coord = float(np.mean(vertices[:, 1 if horizontal else 0]))
        normalized = ax.transAxes.inverted().transform((ax.bbox.x0, coord) if horizontal else (coord, ax.bbox.y0))[1 if horizontal else 0]
        across = normalized + sign * distance / (bounds.height * fh if horizontal else bounds.width * fw)
        axis.set_label_coords(along if horizontal else across, across if horizontal else along)
    elif style.label_margin is not None:
        margin = style.label_margin * scale * factor
        from materials_data_studio.tools.pxp_render import _graph_margins
        # The graph's virtual paper can be letterboxed inside a larger preview.
        # lblMargin is relative to that paper, not the outer Matplotlib canvas.
        inset = _graph_margins(graph)[side] * scale
        perpendicular_extent = bounds.height * fh if horizontal else bounds.width * fw
        across = (0 if sign < 0 else 1) + sign * (inset-margin) / perpendicular_extent
        axis.label.set_verticalalignment("bottom")
        axis.set_label_coords(along if horizontal else across, across if horizontal else along)
    elif style.label_lateral_position is not None:
        axis.label.set_position((along, axis.label.get_position()[1]) if horizontal else (axis.label.get_position()[0], along))
    if style.label_line_spacing is not None:
        axis.label.line_spacing = style.label_line_spacing * scale


def set_label_position(graph: Any, name: str, position: dict[str, Any], extent_points: float) -> Any:
    """Convert drag/form coordinates once into the same native point fields."""
    horizontal = name in {"bottom", "top"} or any(a.name == name and a.side in {"bottom", "top"} for a in graph.axes)
    if graph.swap_xy:
        horizontal = not horizontal
    lateral = (float(position["lateral"]) - .5) * extent_points * (1 if horizontal else -1)
    return replace(graph, axes=tuple(replace(a, style=replace(a.style,
        label_position_mode=3, label_position=float(position["offset_points"]),
        label_lateral_position=lateral, label_lateral_fraction=None, label_margin=None)) if a.name == name else a for a in graph.axes))


def migrate_legacy_labels(graphs: Any, layout: dict[str, Any]) -> Any:
    """No renderer/GUI needed: preserve old fractions explicitly, never guess points."""
    result = []
    for graph in graphs:
        entry = layout.get(graph.name, {})
        if not isinstance(entry, dict):
            raise ValueError("Invalid graph layout entry")
        positions = entry.pop("labels", {})
        if not isinstance(positions, dict):
            raise ValueError("Invalid legacy label layout")
        axes = []
        for axis in graph.axes:
            position = positions.get(axis.name)
            if position is not None and not isinstance(position, dict):
                raise ValueError("Invalid legacy label record")
            if position and position.get("coordinate_system") == "axis_label":
                offset, fraction = float(position["offset_points"]), float(position["lateral"])
                if not np.isfinite((offset,fraction)).all():
                    raise ValueError("Invalid legacy label position")
                axis = replace(axis, style=replace(axis.style, label_position_mode=3,
                    label_position=offset, label_lateral_position=None, label_lateral_fraction=fraction))
            elif position:
                # Unknown old layout is retained explicitly, not guessed away.
                layout.setdefault(graph.name, {}).setdefault("labels", {})[axis.name] = position
            axes.append(axis)
        for name, position in positions.items():
            if name not in {a.name for a in graph.axes}:
                layout.setdefault(graph.name, {}).setdefault("labels", {})[name] = position
        result.append(replace(graph, axes=tuple(axes)))
    return tuple(result)


def native_lateral(graph: Any, axis: Any, style: Any) -> float | None:
    if style.label_lateral_fraction is None:
        return style.label_lateral_position
    from materials_data_studio.tools.pxp_render import _plot_dimensions
    width, height = _plot_dimensions(graph) or (432,288)
    horizontal = (axis.side or axis.name) in {"bottom","top"}
    if graph.swap_xy:
        horizontal = not horizontal
    return (style.label_lateral_fraction-.5)*(width if horizontal else height)*(1 if horizontal else -1)


def fit_automatic_margins(figure: Any, graph: Any) -> None:
    """Measure glyphs/ticks, then expand only automatic margins, bounded to 2 passes.

    Never overwrite semantic explicit margins or run tight_layout over free axes.
    Derived axes rectangles are translated/scaled together, preserving pairs.
    """
    from matplotlib.backends.backend_agg import RendererAgg
    axes = [a for a in figure.axes if not str(a.get_gid() or "").startswith("colorbar:")]
    if not axes:
        return
    margins = dict(graph.style.margins)
    auto: dict[str, bool] = {s: margins.get(s, margins.get("all", 0)) == 0 for s in ("left", "right", "top", "bottom")}
    if graph.swap_xy:
        auto = dict(zip(("bottom", "left", "right", "top"), (auto[s] for s in ("left", "bottom", "top", "right")), strict=True))
    if not any(auto.values()):
        return
    width, height = figure.bbox.width, figure.bbox.height
    for _ in range(2):
        renderer = RendererAgg(int(width), int(height), figure.dpi)
        # Only measure axis glyphs. A full figure.draw here would rasterize every
        # image/trace twice and make property edits and large-wave previews slow.
        for ax in axes:
            ax.get_xlim()
            ax.get_ylim()
            ax.apply_aspect()
        boxes = [axis.get_tightbbox(renderer) for ax in axes for axis in (ax.xaxis, ax.yaxis) if axis.get_visible()]
        boxes = [b for b in boxes if b is not None and np.isfinite(b.extents).all()]
        if not boxes:
            return
        pad = renderer.points_to_pixels(2)
        extra = {"left": max(0, pad-min(b.x0 for b in boxes))/width,
                 "right": max(0, max(b.x1 for b in boxes)+pad-width)/width,
                 "bottom": max(0, pad-min(b.y0 for b in boxes))/height,
                 "top": max(0, max(b.y1 for b in boxes)+pad-height)/height}
        extra = {s: min(.2, v) if auto[s] else 0 for s, v in extra.items()}
        if max(extra.values()) < 1e-5:
            break
        for ax in axes:
            b = ax.get_position()
            sx, sy = 1-extra["left"]-extra["right"], 1-extra["bottom"]-extra["top"]
            ax.set_position((extra["left"]+b.x0*sx, extra["bottom"]+b.y0*sy, b.width*sx, b.height*sy))
    figure._mds_geometry = {"data_rectangles": [tuple(a.get_position().bounds) for a in axes],
                            "units": "figure fractions", "automatic": auto, "method": "measured glyph/tick extents; bounded two-pass"}
