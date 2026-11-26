"""Error-bar source and rendering shared by imported and edited graphs."""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from materials_data_studio.tools.pxp_model import PxpTrace
from materials_data_studio.tools.pxp_style import igor_rgb


def error_sources(direction: str | tuple[str, tuple[int, ...] | None], pairs: Any, resolve: Any) -> dict[str, Any]:
    display, fill = direction if isinstance(direction, tuple) else ("bars", None)
    if isinstance(direction, tuple):
        direction = "XY" if display == "BOX" else "Y"
    changes: dict[str, Any] = dict(error_display=display.lower(), error_fill=fill, error_axis="Y", error_low=None, error_high=None,
                                   x_error_low=None, x_error_high=None,
                                   x_error_formula=None, y_error_formula=None)
    for axis, (positive, negative) in zip(direction if direction != "OFF" else "", pairs, strict=True):
        if isinstance(positive, str):
            if not math.isfinite(negative) or negative < 0:
                raise ValueError("Error amplitude must be a finite, nonnegative value")
            changes[axis.lower() + "_error_formula"] = (positive, negative)
        else:
            prefix = "x_error" if axis == "X" else "error"
            changes[prefix + "_low"] = resolve(negative.path)
            changes[prefix + "_high"] = resolve(positive.path)
    return changes


def render_errors(ax: Any, document: Any, trace: PxpTrace, plot_x: Any, plot_y: Any,
                  *, swapped: bool, scale: float, color: Any) -> None:
    section = slice(trace.point_range[0], trace.point_range[1] + 1) if trace.point_range else slice(0, len(plot_x))
    directions = {}
    for direction, formula, low, high, multiplier, cap in (
        ("X", trace.x_error_formula, trace.x_error_low or (trace.error_low if trace.error_axis == "X" else None),
         trace.x_error_high or (trace.error_high if trace.error_axis == "X" else None), trace.multiplier[0], trace.x_error_cap),
        ("Y", trace.y_error_formula, trace.error_low if trace.error_axis != "X" else None,
         trace.error_high if trace.error_axis != "X" else None, trace.multiplier[1], trace.y_error_cap),
    ):
        horizontal = (direction == "X") != swapped
        multiplier = multiplier or 1.
        if formula is not None:
            method, amount = formula
            displayed = np.asarray(plot_x if horizontal else plot_y, dtype=float)
            raw = abs((displayed - trace.offset[direction == "Y"]) / multiplier)
            errors = np.full(len(raw), amount) if method == "const" else raw * amount / 100 if method == "pct" else np.sqrt(raw)
        elif low:
            lower, upper = document.wave(low), document.wave(high or low)
            if lower is None or upper is None:
                continue
            errors = np.stack((np.asarray(lower.data, dtype=float)[section],
                               np.asarray(upper.data, dtype=float)[section]))
            if multiplier < 0:
                errors = errors[::-1]
        else:
            continue
        if errors.shape[-1] != len(plot_x):
            continue
        errors = errors * abs(multiplier)
        if trace.error_display != "bars":
            if errors.ndim == 1:
                errors = np.stack((errors, errors))
            directions[direction] = errors
            continue
        ax.errorbar(
            plot_x, plot_y, xerr=errors if horizontal else None, yerr=None if horizontal else errors,
            fmt="none", capsize=(trace.error_cap if cap is None else cap) * scale,
            elinewidth=trace.error_width * scale, capthick=trace.error_cap_thickness * scale,
            ecolor=igor_rgb(trace.error_color) if trace.error_color else color, clip_on=trace.error_clip,
        )
    if not directions:
        return
    face = tuple(value / 65535 for value in trace.error_fill) if trace.error_fill else None
    if face is not None and len(face) == 3:
        face = (*face, 1.)
    if trace.error_display == "shade" and "Y" in directions:
        low, high = directions["Y"]
        if face is None or face == (0, 0, 0, 0):
            from matplotlib.colors import to_rgba
            face = to_rgba(color, 20000 / 65535)
        if swapped:
            ax.fill_betweenx(plot_y, plot_x - low, plot_x + high, color=face, clip_on=trace.error_clip)
        else:
            ax.fill_between(plot_x, plot_y - low, plot_y + high, color=face, clip_on=trace.error_clip)
    if trace.error_display == "box" and directions.keys() >= {"X", "Y"}:
        from matplotlib.collections import PolyCollection

        left, right = directions["Y" if swapped else "X"]
        below, above = directions["X" if swapped else "Y"]
        x0, x1 = plot_x - left, plot_x + right
        y0, y1 = plot_y - below, plot_y + above
        vertices = np.stack((np.column_stack((x0, y0)), np.column_stack((x1, y0)),
                             np.column_stack((x1, y1)), np.column_stack((x0, y1))), axis=1)
        ax.add_collection(PolyCollection(
            list(vertices), facecolors=face or "none", edgecolors=igor_rgb(trace.error_color) if trace.error_color else color,
            linewidths=trace.error_width * scale, clip_on=trace.error_clip,
        ))
