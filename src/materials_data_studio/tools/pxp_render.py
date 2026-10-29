"""PXP-only renderer. Original waves and graph models are never mutated."""

from __future__ import annotations

import re
from dataclasses import fields
from math import floor, log10
from typing import Any

import numpy as np
from matplotlib.artist import Artist
from matplotlib.cm import ScalarMappable
from matplotlib.colors import LinearSegmentedColormap, LogNorm, Normalize
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
from matplotlib.text import Text
from matplotlib.ticker import (
    AutoMinorLocator,
    FixedFormatter,
    FixedLocator,
    Formatter,
    FuncFormatter,
    Locator,
    LogLocator,
    MultipleLocator,
    NullLocator,
)
from matplotlib.transforms import Affine2D, Bbox

from materials_data_studio.tools.graph_typography import annotation_font, graph_font, marker_size
from materials_data_studio.tools.igor_ticks import (
    IgorLinearLocator,
    IgorLogMinorLocator,
    ZapFormatter,
)
from materials_data_studio.tools.pxp_markers import marker_path
from materials_data_studio.tools.pxp_model import (
    PxpAnnotationStyle,
    PxpAxis,
    PxpAxisStyle,
    PxpDocument,
    PxpGraph,
    PxpWave,
)
from materials_data_studio.tools.pxp_recreation import formatted_text
from materials_data_studio.tools.pxp_style import (
    FILLED_MARKERS,
    igor_rgb,
    image_colormap,
    legend_labels,
)
from materials_data_studio.tools.pxp_text import IgorText

_SUBSCRIPT = str.maketrans("₀₁₂₃₄₅₆₇₈₉₊₋", "0123456789+-")
_SUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻", "0123456789+-")

# Independently specified point patterns matching the default reference menu.
# Custom SetDashPattern vectors, when present, take precedence without approximation.
_DASH_PATTERNS: dict[int, tuple[float, ...]] = {
    1: (1, 1),
    2: (1, 3),
    3: (4, 4),
    4: (2, 2),
    5: (8, 3, 2, 3),
    6: (8, 2, 2, 2, 2, 2),
    7: (6, 6),
    8: (10, 10),
    9: (10, 5, 2, 5),
    10: (10, 4, 2, 4, 2, 4),
    11: (6, 2),
    12: (35, 2),
    13: (22, 4),
    14: (18, 4, 2, 4),
    15: (24, 4, 2, 4),
    16: (22, 3, 2, 3, 2, 3),
    17: (16, 3, 2, 3, 2, 3, 2, 3),
}


class _IgorMinorLocator(AutoMinorLocator):
    """Keep emphasized manual minor ticks distinct, including after zooming."""

    def __init__(
        self,
        divisions: int,
        emphasize: int,
        canon: float,
        spacing: float,
        length: float,
        width: float,
        medium_length: float,
        medium_width: float,
    ) -> None:
        super().__init__(divisions)
        self.divisions = divisions
        self.emphasize, self.canon, self.spacing = emphasize, canon, spacing
        self.length, self.width = length, width
        self.medium_length, self.medium_width = medium_length, medium_width

    def __call__(self) -> Any:
        locations = np.asarray(super().__call__())
        if self.axis is None:
            return locations
        ordinal = np.rint((locations - self.canon) / self.spacing).astype(int)
        # Matplotlib removes locations coinciding with major ticks; do so before
        # styling its tick objects to preserve their ordering.
        valid = ordinal % self.divisions != 0
        locations, ordinal = locations[valid], ordinal[valid]
        axis: Any = self.axis
        for tick, number in zip(axis.get_minor_ticks(len(locations)), ordinal, strict=True):
            medium = self.emphasize > 0 and number % self.emphasize == 0
            for line in (tick.tick1line, tick.tick2line):
                line.set_markersize(self.medium_length if medium else self.length)
                line.set_markeredgewidth(self.medium_width if medium else self.width)
        return locations


class _TickRangeLocator(Locator):
    """Filter ticks, never the axis/data range; delegate zooming to its locator."""

    def __init__(self, source: Locator, limits: tuple[float, float]) -> None:
        super().__init__()
        self.source, self.limits = source, limits
        self.unrestricted = False

    def set_axis(self, axis: Any) -> None:
        super().set_axis(axis)
        self.source.set_axis(axis)

    def _filter(self, values: Any) -> Any:
        values = np.asarray(values)
        result = values[(values >= self.limits[0]) & (values <= self.limits[1])]
        if isinstance(self.source, _IgorMinorLocator):
            source = self.source
            ordinal = np.rint((result - source.canon) / source.spacing).astype(int)
            axis: Any = self.axis
            for tick, number in zip(axis.get_minor_ticks(len(result)), ordinal, strict=True):
                medium = source.emphasize > 0 and number % source.emphasize == 0
                for line in (tick.tick1line, tick.tick2line):
                    line.set_markersize(source.medium_length if medium else source.length)
                    line.set_markeredgewidth(source.medium_width if medium else source.width)
        elif isinstance(self.source, IgorLogMinorLocator):
            self.source.style_ticks(result)
        return result

    def __call__(self) -> Any:
        if self.unrestricted:
            return self.source()
        axis: Any = self.axis
        major = axis.get_major_locator()
        if isinstance(self.source, AutoMinorLocator) and isinstance(major, _TickRangeLocator):
            # AutoMinorLocator derives its spacing from major ticks. Computing
            # it from the already-filtered list loses minors when the enabled
            # range contains fewer than two majors. Temporarily expose only
            # that delegate's original major distribution; never alter limits.
            previous = major.unrestricted
            major.unrestricted = True
            try:
                locations = self.source()
            finally:
                major.unrestricted = previous
            return self._filter(locations)
        return self._filter(self.source())

    def tick_values(self, vmin: float, vmax: float) -> Any:
        return self._filter(self.source.tick_values(vmin, vmax))

    def view_limits(self, vmin: float, vmax: float) -> Any:
        return self.source.view_limits(vmin, vmax)

    def nonsingular(self, v0: float, v1: float) -> Any:
        return self.source.nonsingular(v0, v1)


class _IgorTickFormatter(Formatter):
    """Explicit native trip/notation settings without changing legacy defaults."""

    def __init__(self, source: Formatter, style: PxpAxisStyle) -> None:
        self.source, self.style = source, style
        self.digits = 0
        self.exponential = False

    def set_axis(self, axis: Any) -> None:
        super().set_axis(axis)
        self.source.set_axis(axis)

    def set_locs(self, locs: Any) -> None:
        super().set_locs(locs)
        self.source.set_locs(locs)
        spacing = np.diff(np.unique(np.asarray(locs, dtype=float)))
        spacing = spacing[spacing > 0]
        if len(spacing):
            # Round binary noise before determining the displayed precision.
            value = float(f"{float(spacing.min()):.10g}")
            self.digits = max(0, min(12, -floor(log10(value))))
            if not np.isclose(value * 10**self.digits, round(value * 10**self.digits)):
                self.digits = min(12, self.digits + 1)
        low = self.style.low_trip if self.style.low_trip is not None else 0.1
        high = self.style.high_trip if self.style.high_trip is not None else 100_000
        axis: Any = self.axis
        extrema = np.abs(np.asarray(axis.get_view_interval(), dtype=float))
        extrema = extrema[extrema > 0]
        self.exponential = bool(len(extrema) and (extrema.min() < low or extrema.max() > high))

    def __call__(self, value: float, pos: int | None = None) -> str:
        if value == 0 and self.style.zero_is_zero:
            return "0"
        if self.style.log or all(item is None for item in (self.style.low_trip, self.style.high_trip, self.style.notation)):
            return self.source(value, pos)
        if not np.isfinite(value):
            return ""
        if self.exponential and value:
            exponent = floor(log10(abs(value)))
            if self.style.notation != 1:
                exponent = 3 * floor(exponent / 3)
            mantissa = value / 10.0**exponent
            return f"${mantissa:.10g}\\times10^{{{exponent}}}$"
        result = f"{value:.{self.digits}f}"
        if self.style.trim_trailing_zero and self.digits:
            result = result.rstrip("0").rstrip(".")
        return result

    def get_offset(self) -> str:
        return self.source.get_offset() if self.style.log or all(item is None for item in (
            self.style.low_trip, self.style.high_trip, self.style.notation)) else ""


def _plot_text(text: str) -> str:
    """Use mathtext for script glyphs that Igor fonts may lack in Matplotlib."""
    text = re.sub(
        r"[₀₁₂₃₄₅₆₇₈₉₊₋]+", lambda match: f"$_{{{match[0].translate(_SUBSCRIPT)}}}$", text
    )
    return re.sub(
        r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+", lambda match: f"$^{{{match[0].translate(_SUPERSCRIPT)}}}$", text
    )


def _axis_view(
    graph: PxpGraph, view: dict[str, Any], name: str
) -> tuple[str, tuple[float, float] | None]:
    axis = next((item for item in graph.axes if item.name == name), None)
    override = view.get("axes", {}).get(name, {})
    label = override.get("label", _plot_text(formatted_text(axis.label_raw)) if axis else "")
    limits = override.get("limits", axis.limits if axis else None)
    return label, tuple(limits) if limits is not None else None


def _style(graph: PxpGraph, name: str) -> PxpAxisStyle:
    axis = next((item for item in graph.axes if item.name == name), None)
    specific = axis.style if axis else PxpAxisStyle()
    values = {
        item.name: getattr(specific, item.name)
        if getattr(specific, item.name) is not None
        else getattr(graph.style.axis_defaults, item.name)
        for item in fields(PxpAxisStyle)
    }
    resolved = graph_font(graph, axis_size=values["font_size"], family=values["font_family"])
    values["font_size"], values["font_family"] = resolved.size, resolved.family
    return PxpAxisStyle(**values)


def _plot_dimensions(graph: PxpGraph) -> tuple[float, float] | None:
    style = graph.style
    width, height = style.width_points, style.height_points
    if height and style.width_aspect:
        width = height * style.width_aspect
    elif width and style.height_aspect:
        height = width * style.height_aspect
    if style.window_size:
        margins = _graph_margins(graph)
        width = width or max(50, style.window_size[0] - margins["left"] - margins["right"])
        height = height or max(50, style.window_size[1] - margins["bottom"] - margins["top"])
    if width and height:
        return (height, width) if graph.swap_xy else (width, height)
    return None


def _plot_ratio(graph: PxpGraph) -> float | None:
    dimensions = _plot_dimensions(graph)
    if dimensions:
        return dimensions[1] / dimensions[0]
    ratio = 1 / graph.style.width_aspect if graph.style.width_aspect else graph.style.height_aspect
    return 1 / ratio if ratio and graph.swap_xy else ratio


def _graph_margins(graph: PxpGraph) -> dict[str, float]:
    margins = dict(graph.style.margins)
    font = graph.style.axis_defaults.font_size or graph.style.font_size or graph.style.automatic_font_size or 12
    defaults = {
        "left": font * (3.3 if _style(graph, "left").tick_count == 0 else 4.8),
        "right": font
        * (
            4.8
            if any(
                t.axis == "right"
                or next((a.side for a in graph.axes if a.name == t.axis), "") == "right"
                for t in graph.traces
            )
            else 0.85
        ),
        "bottom": font * 3.1,
        "top": font * (3.1 if any(
            t.x_axis == "top" or next((a.side for a in graph.axes if a.name == t.x_axis), "") == "top"
            for t in graph.traces) else 0.85),
    }
    margins = {
        side: (
            0
            if margins.get(side, margins.get("all")) == -1
            else margins.get(side, margins.get("all")) or default
        )
        for side, default in defaults.items()
    }
    if graph.swap_xy:
        margins = {
            "left": margins["bottom"],
            "bottom": margins["left"],
            "right": margins["top"],
            "top": margins["right"],
        }
    return margins


def _plot_bounds(figure: Figure, graph: PxpGraph) -> tuple[float, float, float, float]:
    """Keep the Igor plot-area dimensions, not just the outer window ratio."""
    width, height = figure.get_size_inches()
    margins = _graph_margins(graph)
    left_points, right_points = margins["left"], margins["right"]
    bottom_points, top_points = margins["bottom"], margins["top"]
    dimensions = _plot_dimensions(graph)
    if dimensions:
        native_w, native_h = dimensions
        total_w, total_h = (
            native_w + left_points + right_points,
            native_h + bottom_points + top_points,
        )
        scale = min(72 * width / total_w, 72 * height / total_h)
        return (
            (72 * width - total_w * scale) / (144 * width) + left_points * scale / (72 * width),
            (72 * height - total_h * scale) / (144 * height)
            + bottom_points * scale / (72 * height),
            native_w * scale / (72 * width),
            native_h * scale / (72 * height),
        )
    left = left_points / (72 * width)
    right = right_points / (72 * width)
    bottom = bottom_points / (72 * height)
    top = top_points / (72 * height)
    available_w = max(0.2, 1 - left - right)
    available_h = max(0.2, 1 - bottom - top)
    ratio = _plot_ratio(graph)
    if ratio and ratio > 0:
        physical_w = available_w * width
        physical_h = available_h * height
        if physical_h / physical_w > ratio:
            available_h = physical_w * ratio / height
        else:
            available_w = physical_h / ratio / width
    return (
        left + (1 - left - right - available_w) / 2,
        bottom + (1 - bottom - top - available_h) / 2,
        available_w,
        available_h,
    )


def _visual_scale(figure: Figure, graph: PxpGraph) -> float:
    dimensions = _plot_dimensions(graph)
    if not dimensions:
        return 1
    bounds = _plot_bounds(figure, graph)
    return max(0.1, bounds[2] * figure.get_size_inches()[0] * 72 / dimensions[0])


def _apply_one_axis(
    ax: Any, graph: PxpGraph, name: str, *, horizontal: bool, scale: float = 1
) -> None:
    style = _style(graph, name)
    axis = ax.xaxis if horizontal else ax.yaxis
    lower_spine = "bottom" if horizontal else "left"
    upper_spine = "top" if horizontal else "right"
    model = next((item for item in graph.axes if item.name == name), None)
    opposite = name in {"right", "top"} or bool(model and model.side in {"right", "top"})
    if opposite:
        lower_spine, upper_spine = upper_spine, lower_spine
    axis.set_ticks_position(lower_spine)
    axis.set_label_position(lower_spine)
    outward = (style.font_size or 12) / 3 if style.standoff else 0
    if style.standoff:
        # Native reference: stand-off separates psize (data clip) from axsize
        # by 1/3 of the axis font size. Never change the data limit to simulate it.
        ax.spines[lower_spine].set_position(("outward", (style.font_size or 12) / 3 * scale))
        ax.spines[upper_spine].set_position(("outward", (style.font_size or 12) / 3 * scale))
    if style.axis_offset:
        from materials_data_studio.tools.graph_geometry import zero_advance
        outward += style.axis_offset * zero_advance(style)
        ax.spines[lower_spine].set_position(("outward", outward * scale))
    side = "x" if horizontal else "y"
    tick_mode = style.tick if style.tick is not None else 0
    direction = {0: "out", 1: "inout", 2: "in"}.get(tick_mode, "out")
    mirror = style.mirror if style.mirror is not None else 0
    ax.spines[upper_spine].set_visible(mirror in {1, 2, 3})
    ax.spines[lower_spine].set_visible(style.thickness != 0)
    thickness = style.thickness if style.thickness is not None else 0.5
    axis_color = igor_rgb(style.axis_color) if style.axis_color is not None else "black"
    for spine in (lower_spine, upper_spine):
        ax.spines[spine].set_linewidth(thickness * scale)
        ax.spines[spine].set_color(axis_color)
    major_kwargs: dict[str, Any] = {
        "direction": direction,
        "width": thickness,
        "length": 7.5,
        "color": axis_color,
        "labelcolor": "black",
        "labelsize": style.font_size or 12,
    }
    minor_kwargs: dict[str, Any] = {
        "direction": direction,
        "width": thickness,
        "length": 3,
        "color": axis_color,
    }
    if style.major_length:
        major_kwargs["length"] = style.major_length
    if style.minor_length:
        minor_kwargs["length"] = style.minor_length
    if style.major_thickness:
        major_kwargs["width"] = style.major_thickness
    if style.minor_thickness:
        minor_kwargs["width"] = style.minor_thickness
    if style.tick_color is not None:
        major_kwargs["color"] = minor_kwargs["color"] = igor_rgb(style.tick_color)
    if style.major_tick_color is not None:
        major_kwargs["color"] = igor_rgb(style.major_tick_color)
    if style.minor_tick_color is not None:
        minor_kwargs["color"] = igor_rgb(style.minor_tick_color)
    if style.tick_label_color is not None:
        major_kwargs["labelcolor"] = minor_kwargs["labelcolor"] = igor_rgb(style.tick_label_color)
    if style.font_size and style.font_size > 0:
        major_kwargs["labelsize"] = style.font_size
        if graph.style.font_size is not None:
            minor_kwargs["labelsize"] = style.font_size
    for options in (major_kwargs, minor_kwargs):
        for key in ("width", "length", "labelsize"):
            if key in options:
                options[key] *= scale
    if horizontal:
        major_kwargs.update(
            bottom=not opposite or mirror in {1, 3},
            top=opposite or mirror in {1, 3},
            labelbottom=not opposite or mirror == 3,
            labeltop=opposite or mirror == 3,
        )
        minor_kwargs.update(
            bottom=not opposite or mirror in {1, 3}, top=opposite or mirror in {1, 3}
        )
    else:
        major_kwargs.update(
            left=not opposite or mirror in {1, 3},
            right=opposite or mirror in {1, 3},
            labelleft=not opposite or mirror == 3,
            labelright=opposite or mirror == 3,
        )
        minor_kwargs.update(
            left=not opposite or mirror in {1, 3}, right=opposite or mirror in {1, 3}
        )
    ax.tick_params(axis=side, which="major", **major_kwargs)
    ax.tick_params(axis=side, which="minor", **minor_kwargs)
    if tick_mode == 3:
        ax.tick_params(axis=side, which="both", length=0)
        minor_kwargs["length"] = 0
    if style.log in {1, 2}:
        (ax.set_xscale if horizontal else ax.set_yscale)("log", base=10 if style.log == 1 else 2)
    if style.manual_ticks and style.manual_ticks[1] > 0 and not style.log:
        canon, increment, exponent, digits = style.manual_ticks
        axis.set_major_locator(
            MultipleLocator(increment * 10**exponent, offset=canon * 10**exponent)
        )
        if style.trim_trailing_zero:
            axis.set_major_formatter(
                FuncFormatter(
                    lambda value, _position: (
                        (
                            f"{value:.{max(0, digits)}f}".rstrip("0").rstrip(".")
                            if digits > 0
                            else f"{value:.0f}"
                        )
                        or "0"
                    )
                )
            )
        else:
            axis.set_major_formatter(
                FuncFormatter(lambda value, _position: f"{value:.{max(0, digits)}f}")
            )
    elif style.tick_count is not None and style.tick_count > 0 and not style.log:
        axis.set_major_locator(IgorLinearLocator(style.tick_count))
    elif not style.log and style.manual_ticks is None:
        axis.set_major_locator(IgorLinearLocator())
    if (style.zero_is_zero or (not style.log and any(item is not None for item in (
            style.low_trip, style.high_trip, style.notation)))):
        axis.set_major_formatter(_IgorTickFormatter(axis.get_major_formatter(), style))
    if style.minor_enabled is False:
        axis.set_minor_locator(NullLocator())
    elif style.log and style.minor_enabled:
        axis.set_minor_locator(IgorLogMinorLocator(style, scale))
    elif style.manual_minor and style.manual_minor[0] > 0 and not style.log:
        count, emphasize = style.manual_minor
        if style.manual_ticks:
            canon, increment, exponent, _digits = style.manual_ticks
            axis.set_minor_locator(
                _IgorMinorLocator(
                    count + 1,
                    emphasize,
                    canon * 10**exponent,
                    increment * 10**exponent / (count + 1),
                    minor_kwargs["length"],
                    minor_kwargs["width"],
                    (style.medium_length or 5) * scale if tick_mode != 3 else 0,
                    (style.medium_thickness or thickness) * scale,
                )
            )
        else:
            axis.set_minor_locator(AutoMinorLocator(count + 1))
    elif style.minor_enabled and not style.log:
        axis.set_minor_locator(AutoMinorLocator())
    if style.tick_count == 0:
        axis.set_major_locator(NullLocator())
        axis.set_minor_locator(NullLocator())
    grid_options: dict[str, Any] = {} if style.grid_hair is None else {"linewidth": thickness * scale / 2**style.grid_hair}
    minor_grid_options: dict[str, Any] = {} if style.grid_hair is None else {"linewidth": thickness * scale / 2**style.grid_hair / 2}
    if style.grid_color is not None:
        grid_options["color"] = minor_grid_options["color"] = igor_rgb(style.grid_color)
    if style.grid_style is not None:
        mode = style.grid_style or (1 if graph.style.plot_background in {None, (65535, 65535, 65535)} else 5)
        grid_options["linestyle"] = ":" if mode in {1, 2} else "-"
        minor_grid_options["linestyle"] = "--" if mode == 1 else ":" if mode in {2, 3} else "-"
    ax.grid(style.grid in {1, 2}, axis=side, which="major", **grid_options)
    ax.grid(style.grid == 1, axis=side, which="minor", **minor_grid_options)
    if style.zero_line and not style.log:
        line = Line2D([0, 0] if horizontal else [0, 1], [0, 1] if horizontal else [0, 0],
                      transform=ax.get_xaxis_transform() if horizontal else ax.get_yaxis_transform(),
                      color=axis_color, linewidth=thickness * scale, zorder=1.9)
        pattern = _DASH_PATTERNS.get(style.zero_line - 1)
        if pattern:
            width = max(thickness, 0.01)
            line.set_linestyle((0, tuple(value / width for value in pattern)))
        line.set_clip_path(ax.patch)
        line.set_gid("zeroline:" + name)
        ax.add_artist(line)
    if style.grid_color is not None:
        for gridline in axis.get_gridlines():
            gridline.set_color(igor_rgb(style.grid_color))
    label = ax.xaxis.label if horizontal else ax.yaxis.label
    label.set_fontsize((style.font_size or 12) * scale)
    if style.label_color is not None:
        label.set_color(igor_rgb(style.label_color))
    if style.font_size and style.font_size > 0:
        label.set_fontsize(style.font_size * scale)
    if style.font_family:
        label.set_fontfamily(style.font_family)
        for tick_label in axis.get_ticklabels():
            tick_label.set_fontfamily(style.font_family)
    if style.font_style is not None:
        for tick_label in axis.get_ticklabels():
            tick_label.set_fontstyle("italic" if style.font_style & 2 else "normal")
            tick_label.set_fontweight("bold" if style.font_style & 1 else "normal")
    model = next((item for item in graph.axes if item.name == name), None)
    if model:
        match = re.search(r"\\Z(\d{1,2})", model.label_raw)
        if match:
            label.set_fontsize(int(match[1]) * scale)
    if style.label_margin is not None:
        axis.labelpad = style.label_margin * scale
    if style.label_rotation is not None:
        label.set_rotation((0 if horizontal else 270 if opposite else 90) + style.label_rotation)
    elif opposite and not horizontal:
        label.set_rotation(270)
    if style.tick_rotation is not None:
        for tick_label in axis.get_ticklabels():
            tick_label.set_rotation(style.tick_rotation)
    if style.no_label in {1, 2}:
        ax.tick_params(
            axis=side, labelbottom=False, labelleft=False, labeltop=False, labelright=False
        )
    if style.no_label == 2:
        label.set_visible(False)
    if style.reverse:
        axis.set_inverted(True)
    if style.date_info is not None:
        from materials_data_studio.tools.igor_graph_extensions import axis_date_text
        axis.set_major_formatter(FuncFormatter(lambda value, _position: axis_date_text(value, style.date_info)))
    if style.tick_zap:
        axis.set_major_formatter(ZapFormatter(axis.get_major_formatter(), style.tick_zap))
        axis.set_minor_formatter(ZapFormatter(axis.get_minor_formatter(), style.tick_zap))


def _apply_axes(ax: Any, graph: PxpGraph, view: dict[str, Any], y_axis: str = "left",
                x_axis: str = "bottom") -> None:
    x_name, y_name = (y_axis, x_axis) if graph.swap_xy else (x_axis, y_axis)
    ax._mds_axis_names = (x_axis, y_axis)
    xlabel, xlim = _axis_view(graph, view, x_name)
    ylabel, ylim = _axis_view(graph, view, y_name)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    if xlim:
        ax.set_xlim(*xlim)
    if ylim:
        ax.set_ylim(*ylim)
    scale = _visual_scale(ax.figure, graph)
    _apply_one_axis(ax, graph, x_name, horizontal=True, scale=scale)
    _apply_one_axis(ax, graph, y_name, horizontal=False, scale=scale)
    for name, axis in ((x_name, ax.xaxis), (y_name, ax.yaxis)):
        model = next((item for item in graph.axes if item.name == name), None)
        override = view.get("axes", {}).get(name, {})
        raw = override.get("label", model.label_raw if model else "")
        styled = IgorText(axis.label, raw, _style(graph, name).font_style or 0)
        styled.set_figure(ax.figure)
        styled.font_scale = scale * graph.style.font_multiplier / 100
        axis.label = styled
        label_style = _style(graph, name)
        from materials_data_studio.tools.graph_geometry import position_label
        position_label(ax, axis, label_style, graph, scale)
    if graph.style.plot_background is not None:
        ax.set_facecolor(igor_rgb(graph.style.plot_background))


def _user_ticks(
    ax: Any, document: PxpDocument, graph: PxpGraph, name: str, horizontal: bool, scale: float
) -> None:
    style = _style(graph, name)
    axis = ax.xaxis if horizontal else ax.yaxis
    if not style.user_ticks:
        if style.tick_enable is not None:
            axis.set_major_locator(_TickRangeLocator(axis.get_major_locator(), style.tick_enable))
            axis.set_minor_locator(_TickRangeLocator(axis.get_minor_locator(), style.tick_enable))
        return
    positions, labels = (document.wave(ref) for ref in style.user_ticks)
    if (
        positions is None
        or labels is None
        or positions.data.ndim != 1
        or len(positions.data) != len(labels.data)
    ):
        return
    texts = labels.data
    label_column = texts[:, 0] if texts.ndim == 2 else texts
    types = texts[:, 1] if texts.ndim == 2 and texts.shape[1] > 1 else np.full(len(texts), "Major")
    values = np.asarray(positions.data, dtype=float)
    major = np.isfinite(values) & np.array(
        [str(t).lower() not in {"minor", "subminor", "emphasized"} for t in types]
    )
    minor = np.isfinite(values) & ~major
    if style.tick_enable is not None:
        enabled = (values >= style.tick_enable[0]) & (values <= style.tick_enable[1])
        major &= enabled
        minor &= enabled
    axis.set_major_locator(FixedLocator(values[major].tolist()))
    axis.set_major_formatter(
        FixedFormatter([_plot_text(formatted_text(str(t))) for t in label_column[major]])
    )
    axis.set_minor_locator(FixedLocator(values[minor].tolist()))
    medium = style.medium_length or 5
    for tick, kind in zip(axis.get_minor_ticks(int(minor.sum())), types[minor], strict=True):
        length = (
            medium
            if str(kind).lower() == "emphasized"
            else (style.subminor_length or 1.5) if str(kind).lower() == "subminor" else (style.minor_length or 3)
        ) * scale
        for line in (tick.tick1line, tick.tick2line):
            line.set_markersize(0 if style.tick == 3 else length)
            width = style.subminor_thickness if str(kind).lower() == "subminor" else style.medium_thickness if str(kind).lower() == "emphasized" else style.minor_thickness
            line.set_markeredgewidth((width or style.thickness or .5) * scale)


def render_wave(figure: Figure, wave: PxpWave) -> None:
    figure.clear()
    ax = figure.add_subplot(111)
    if wave.data.ndim == 1 and np.issubdtype(wave.data.dtype, np.number):
        if wave.data.dtype.kind == "c":
            ax.plot(wave.coordinate(0), wave.data.real, label="Real", color="tab:blue")
            ax.plot(wave.coordinate(0), wave.data.imag, label="Imaginary", color="tab:orange")
            ax.legend()
        else:
            ax.plot(wave.coordinate(0), wave.data, color="tab:blue")
        ax.set_xlabel(
            f"Dimension 0 ({wave.dimension_units[0]})" if wave.dimension_units[0] else "Dimension 0"
        )
        ax.set_ylabel(wave.data_units or wave.name)
    elif wave.data.ndim == 2 and np.issubdtype(wave.data.dtype, np.number):
        x = wave.coordinate(0)
        y = wave.coordinate(1)
        image = ax.imshow(
            np.abs(wave.data.T) if wave.data.dtype.kind == "c" else wave.data.T,
            origin="lower",
            aspect="auto",
            cmap="viridis",
            extent=(
                x[0] - wave.dimension_scaling[0][1] / 2,
                x[-1] + wave.dimension_scaling[0][1] / 2,
                y[0] - wave.dimension_scaling[1][1] / 2,
                y[-1] + wave.dimension_scaling[1][1] / 2,
            ),
        )
        figure.colorbar(image, ax=ax)
        if wave.data.dtype.kind == "c":
            ax.set_title("Complex wave magnitude (phase is not shown)")
        ax.set_xlabel(wave.dimension_units[0] or "Dimension 0")
        ax.set_ylabel(wave.dimension_units[1] or "Dimension 1")
    else:
        ax.text(
            0.5,
            0.5,
            "Preview unavailable for this wave type/dimension.\nUse Edit Working Wave to select a layer/chunk, or Command / Python.",
            ha="center",
            va="center",
            transform=ax.transAxes,
        )
    figure.tight_layout()


def _annotation_anchor(style: PxpAnnotationStyle) -> tuple[float, float, str]:
    anchors = {
        "MC": (0.5, 0.5, "center"),
        "LT": (0, 1, "upper left"),
        "RT": (1, 1, "upper right"),
        "LB": (0, 0, "lower left"),
        "RB": (1, 0, "lower right"),
        "LC": (0, 0.5, "center left"),
        "RC": (1, 0.5, "center right"),
        "MT": (0.5, 1, "upper center"),
        "MB": (0.5, 0, "lower center"),
    }
    x, y, location = anchors.get(style.anchor, anchors["MC"])
    native = style.offset_mode == "native"
    return (
        x + (-1 if native and "right" in location else 1) * style.x_percent / 100,
        y + (-1 if native and "upper" in location else 1) * style.y_percent / 100,
        location,
    )


def annotation_offsets(style: PxpAnnotationStyle, x: float, y: float) -> tuple[float, float]:
    """Inverse of native anchor-relative percentages for GUI edits and drags."""
    base_x, base_y, location = _annotation_anchor(PxpAnnotationStyle(anchor=style.anchor))
    native = style.offset_mode == "native"
    return (
        (x - base_x) * (-100 if native and "right" in location else 100),
        (y - base_y) * (-100 if native and "upper" in location else 100),
    )


def _colorbar(figure: Figure, ax: Any, image: Any, spec: Any, scale: float = 1,
              default_font_size: float | None = None) -> None:
    style = spec.colorbar_style
    font_size = default_font_size if default_font_size is not None else style.font_size
    bounds = ax.get_position()
    fw, fh = figure.get_size_inches() * 72
    bar_width = (
        style.width * scale / fw
        if style.vertical
        else bounds.width * (style.width_percent or 75) / 100
    )
    bar_height = (
        (style.height / fh if style.height else bounds.height * style.height_percent / 100)
        if style.vertical
        else 15 / fh
    )
    if style.width_percent and style.vertical:
        bar_width = bounds.width * style.width_percent / 100
    x, y, location = _annotation_anchor(style)
    # Igor anchors the complete annotation (bar, ticks, title), not just the bar.
    total_width = bar_width + (font_size * scale * 3.9 / fw if style.vertical else 0)
    x0 = bounds.x0 + x * bounds.width
    y0 = bounds.y0 + y * bounds.height
    x0 -= total_width * (0 if "left" in location else 1 if "right" in location else 0.5)
    y0 -= bar_height * (1 if "upper" in location else 0 if "lower" in location else 0.5)
    cax = figure.add_axes((x0, y0, bar_width, bar_height))
    cax.set_gid(f"colorbar:{spec.wave}")
    norm = image.norm
    if (
        isinstance(norm, LogNorm)
        and not style.log_axis
        and norm.vmin is not None
        and norm.vmax is not None
    ):
        values = np.linspace(norm.vmin, norm.vmax, 256)
        mapped = LinearSegmentedColormap.from_list("mds_color_scale", image.cmap(norm(values)))
        mappable = ScalarMappable(norm=Normalize(norm.vmin, norm.vmax), cmap=mapped)
    elif style.log_axis and not isinstance(norm, LogNorm):
        if norm.vmin is None or norm.vmin <= 0 or norm.vmax is None:
            raise ValueError("Log ColorScale requires positive limits")
        values = np.geomspace(norm.vmin, norm.vmax, 256)
        mapped = LinearSegmentedColormap.from_list("mds_log_color_scale", image.cmap(norm(values)))
        mappable = ScalarMappable(norm=LogNorm(norm.vmin, norm.vmax), cmap=mapped)
    else:
        mappable = image
    bar = figure.colorbar(
        mappable, cax=cax, orientation="vertical" if style.vertical else "horizontal"
    )
    color = igor_rgb(style.color) if style.color else "black"
    cax.tick_params(
        colors=color, labelsize=font_size * scale, width=0.5 * scale, length=2 * scale
    )
    bar.outline.set_edgecolor(igor_rgb(style.frame_color))
    bar.outline.set_linewidth(style.frame_width * scale if style.frame else 0)
    cax.set_facecolor("none" if style.transparent else igor_rgb(style.background_color))
    for label in cax.get_yticklabels() + cax.get_xticklabels():
        label.set_fontfamily("Arial")
    bar.set_label(
        formatted_text(spec.colorbar_label_raw),
        color=color,
        fontsize=font_size * scale,
        fontfamily="Arial",
        rotation=270 if style.vertical else 0,
        labelpad=15 * scale,
    )
    axis = cax.yaxis if style.vertical else cax.xaxis
    a = spec.colorbar_axis
    if a.tick_count == 0:
        axis.set_major_locator(NullLocator())
    elif a.tick_count is not None and not style.log_axis:
        axis.set_major_locator(IgorLinearLocator(int(a.tick_count)))
    if a.minor_enabled:
        axis.set_minor_locator(LogLocator(subs="auto") if style.log_axis else AutoMinorLocator())
    cax.tick_params(axis="y" if style.vertical else "x", direction={0:"out",1:"inout",2:"in"}.get(a.tick or 0,"out"),
                    length=(a.major_length if a.major_length is not None else 2)*scale,
                    width=(a.major_thickness if a.major_thickness is not None else .5)*scale,
                    labelrotation=a.tick_rotation or 0)
    if a.label_margin is not None:
        axis.labelpad = a.label_margin*scale
    if a.label_rotation is not None:
        axis.label.set_rotation((270 if style.vertical else 0) + a.label_rotation)
    if a.font_family:
        axis.label.set_fontfamily(a.font_family)
        for tick in axis.get_ticklabels():
            tick.set_fontfamily(a.font_family)
    styled = IgorText(axis.label, spec.colorbar_label_raw)
    styled.set_figure(figure)
    styled.font_scale = scale
    axis.label = styled


def _render_images(figure: Figure, ax: Any, document: PxpDocument, graph: PxpGraph,
                   view: dict[str, Any], scale: float) -> None:
    """Image layer on the same plot axes as traces; never an alternate graph path."""
    for spec in graph.images:
            override = view.get("images", {}).get(spec.wave, {})
            if not override.get("visible", not spec.hidden):
                continue
            wave = document.wave(spec.wave)
            if wave is None or (wave.data.ndim != 2 and not (wave.data.ndim == 3 and wave.shape[2] in {3, 4})) or not np.issubdtype(wave.data.dtype, np.number):
                continue
            low, high = override.get("range", spec.color_range or (None, None))
            use_log = override.get("log", spec.log_color)
            pixels = np.asarray(wave.data, dtype=float)
            finite = pixels[np.isfinite(pixels)]
            positive = finite[finite > 0]
            if use_log:
                pixels = np.ma.masked_less_equal(pixels, 0)
                vmin = float(low) if low is not None and low > 0 else float(positive.min()) if positive.size else 1
                vmax = float(high) if high is not None else float(positive.max()) if positive.size else 10
                norm = LogNorm(vmin=vmin, vmax=max(vmax, vmin * 1.000001))
            else:
                norm = Normalize(vmin=low, vmax=high)
            x, y = wave.coordinate(0), wave.coordinate(1)
            colors = image_colormap(spec, name=override.get("colormap"), reverse=override.get("reverse"))
            extremes: dict[str, Any] = {}
            for endpoint, color in (("under", spec.min_color), ("over", spec.max_color), ("bad", spec.nan_color)):
                if color is None:
                    continue
                resolved = (
                    igor_rgb(color)
                    if isinstance(color, (tuple, list))
                    else ((0, 0, 0, 0) if color == "transparent" else "black")
                )
                extremes[endpoint] = resolved
            if extremes:
                colors = colors.with_extremes(**extremes)
            if wave.data.ndim == 3 or spec.x_wave or spec.y_wave:
                from materials_data_studio.tools.igor_graph_extensions import render_image_mesh
                image = render_image_mesh(ax, document, graph, spec, wave, colors, norm)
                image.set_gid(f"image:{spec.wave}")
                if wave.data.ndim == 2 and spec.colorbar_visible:
                    _colorbar(figure, ax, image, spec, scale, annotation_font(graph, spec.colorbar_style).size)
                continue
            image = ax.imshow(
                pixels if graph.swap_xy else pixels.T,
                origin="lower",
                aspect="auto",
                cmap=colors,
                norm=norm,
                interpolation="bilinear" if spec.interpolation else "nearest",
                extent=tuple((
                    x[0] - wave.dimension_scaling[0][1] / 2,
                    x[-1] + wave.dimension_scaling[0][1] / 2,
                    y[0] - wave.dimension_scaling[1][1] / 2,
                    y[-1] + wave.dimension_scaling[1][1] / 2,
                )[i] for i in ((2, 3, 0, 1) if graph.swap_xy else (0, 1, 2, 3))),
                zorder=0,
            )
            image.set_gid(f"image:{spec.wave}")
            if spec.colorbar_visible:
                _colorbar(figure, ax, image, spec, scale, annotation_font(graph, spec.colorbar_style).size)


def render_graph(
    figure: Figure, document: PxpDocument, graph: PxpGraph, view: dict[str, Any] | None = None
) -> None:
    """Render image/trace layers against semantic X/Y axis pairs."""
    view = view or {}
    figure.clear()
    if graph.window_kind == "Layout":
        from materials_data_studio.tools.igor_canvas import render_layout
        render_layout(figure, document, graph)
        return
    from materials_data_studio.tools.graph_fidelity import assess_graph
    report = assess_graph(graph,document)
    if report.rendered == "blocked":
        errors = [d for d in report.diagnostics if d.severity == "error"]
        figure.text(.5,.5,"Graph cannot be rendered reliably.\n" + "\n".join(
            f"{d.code}: {d.property}" for d in errors[:8]),ha="center",va="center",wrap=True)
        return
    scale = _visual_scale(figure, graph)
    y_names = list(dict.fromkeys(trace.axis for trace in graph.traces))
    pairs = list(dict.fromkeys((trace.x_axis, trace.axis) for trace in graph.traces))
    if graph.images and ("bottom", "left") not in pairs:
        pairs.insert(0, ("bottom", "left"))
    pairs = pairs or [("bottom", "left")]
    axis_names = {y if x == "bottom" else f"{y}@{x}": (x, y) for x, y in pairs}
    axis_models = {axis.name: axis for axis in graph.axes}
    independent_x = any(x != "bottom" for x, _y in pairs)
    stacked = not independent_x and not graph.images and len(y_names) > 1 and all(
        axis_models.get(name) is not None and axis_models[name].enable is not None
        for name in y_names
    )
    axes: dict[str, Any] = {}
    ax: Any
    left, bottom, width, height = _plot_bounds(figure, graph)
    if independent_x:
        for key, (x_name, y_name) in axis_names.items():
            previous_x = next((a for a in axes.values() if a._mds_axis_names[0] == x_name), None)
            previous_y = next((a for a in axes.values() if a._mds_axis_names[1] == y_name), None)
            ax = figure.add_axes((left, bottom, width, height),
                                 sharex=previous_y if graph.swap_xy else previous_x,
                                 sharey=previous_x if graph.swap_xy else previous_y)
            ax.patch.set_visible(not axes)
            ax._mds_axis_names = (x_name, y_name)
            axes[key] = ax
    elif stacked:
        for name in y_names:
            low, high = axis_models[name].enable or (0.0, 1.0)
            bounds = (
                (left + low * width, bottom, (high - low) * width, height)
                if graph.swap_xy
                else (left, bottom + low * height, width, (high - low) * height)
            )
            axes[name] = figure.add_axes(bounds)
    else:
        first = figure.add_axes((left, bottom, width, height))
        for index, name in enumerate(axis_names):
            axes[name] = first if index == 0 else first.twiny() if graph.swap_xy else first.twinx()
            if index > 1:
                axes[name].spines["right"].set_position(("outward", 50 * (index - 1)))
    if not axes:
        axes["left"] = figure.add_axes((left, bottom, width, height))
    if graph.images:
        _render_images(figure, axes["left"], document, graph, view, scale)
    labels = legend_labels(graph.legend_raw, [trace.name for trace in graph.traces])
    handles = []
    handle_map: dict[str, Any] = {}
    for index, trace in enumerate(graph.traces):
        if trace.violin is not None:
            from materials_data_studio.tools.igor_violin import render_violin
            ax = axes[trace.axis if trace.x_axis == "bottom" else f"{trace.axis}@{trace.x_axis}"]
            render_violin(ax, document, trace, swap=graph.swap_xy, scale=scale)
            proxy = Line2D([], [], color=igor_rgb((trace.violin.line_color[0], trace.violin.line_color[1], trace.violin.line_color[2])), label=labels[index])
            handles.append(proxy)
            handle_map[trace.name] = proxy
            continue
        if trace.box is not None:
            from materials_data_studio.tools.igor_box import render_box
            ax = axes[trace.axis if trace.x_axis == "bottom" else f"{trace.axis}@{trace.x_axis}"]
            plotted_box = render_box(ax, document, trace, swap=graph.swap_xy, scale=scale)
            if plotted_box is not None:
                proxy = Line2D([], [], linestyle="-", marker="s",
                               color=plotted_box.get_edgecolor(),
                               markerfacecolor=plotted_box.get_facecolor(), label=labels[index])
                handles.append(proxy)
                handle_map[trace.name] = proxy
            continue
        wave_y = document.wave(trace.y_wave)
        wave_x = document.wave(trace.x_wave) if trace.x_wave else None
        if (
            wave_y is None
            or wave_y.data.ndim != 1
            or not np.issubdtype(wave_y.data.dtype, np.number)
        ):
            continue
        if trace.x_wave and (
            wave_x is None
            or wave_x.data.ndim != 1
            or wave_x.data.dtype.kind not in "biufSU"
        ):
            continue  # Do not silently invent X coordinates for an unreadable X wave.
        y = np.asarray(wave_y.data, dtype=float)
        x = (
            (np.arange(len(wave_x.data), dtype=float) if wave_x.data.dtype.kind in "SU" else np.asarray(wave_x.data, dtype=float))
            if wave_x is not None and wave_x.data.ndim == 1
            else wave_y.coordinate(0)
        )
        count = min(len(x), len(y))
        x, y = x[:count], y[:count]
        if trace.point_range is not None:
            first, last = trace.point_range
            x, y = x[first:last+1], y[first:last+1]
            count = len(y)
        if wave_x is not None and wave_x.data.dtype.kind in "SU":
            axis = axes[trace.axis if trace.x_axis == "bottom" else f"{trace.axis}@{trace.x_axis}"]
            locations = np.arange(len(wave_x.data)) * (trace.multiplier[0] or 1) + trace.offset[0]
            (axis.set_yticks if graph.swap_xy else axis.set_xticks)(locations, [str(v) for v in wave_x.data])
        x = x * (trace.multiplier[0] or 1) + trace.offset[0]
        y = y * (trace.multiplier[1] or 1) + trace.offset[1]
        spec = view.get("traces", {}).get(trace.name, {})
        if not spec.get("visible", True):
            continue
        mode = trace.mode if trace.mode is not None else 0
        line = "None" if mode in {1, 2, 3, 5, 8} else "-"
        symbol = "." if mode == 2 else marker_path(trace.marker) if mode in {3, 4, 8} else ""
        if "marker" in spec:
            symbol = spec["marker"]
        color = spec.get("color", igor_rgb(trace.color_igor))
        ax = axes[trace.axis if trace.x_axis == "bottom" else f"{trace.axis}@{trace.x_axis}"]
        plot_x, plot_y = (y, x) if graph.swap_xy else (x, y)
        if mode in {1, 8}:
            if graph.swap_xy:
                ax.hlines(
                    plot_y, 0, plot_x, colors=color, linewidth=(trace.line_width or 1) * scale
                )
            else:
                ax.vlines(
                    plot_x, 0, plot_y, colors=color, linewidth=(trace.line_width or 1) * scale
                )
        if mode == 5:
            interval = float(np.nanmedian(np.abs(np.diff(x)))) if len(x) > 1 else 1
            if graph.swap_xy:
                ax.barh(plot_y, plot_x, height=interval, color=color)
            else:
                ax.bar(plot_x, plot_y, width=interval, color=color)
        if mode == 7:
            if graph.swap_xy:
                ax.fill_betweenx(plot_y, 0, plot_x, color=color)
            else:
                ax.fill_between(plot_x, 0, plot_y, color=color)
        if trace.marker in FILLED_MARKERS or mode == 2:
            fill = color
        elif trace.opaque:
            fill = igor_rgb(trace.marker_fill_igor) if trace.marker_fill_igor else "white"
        else:
            fill = "none"
        stroke = (
            (igor_rgb(trace.marker_stroke_igor) if trace.marker_stroke_igor else "black")
            if trace.use_marker_stroke
            else color
        )
        plotted = ax.plot(
            plot_x,
            plot_y,
            linestyle=line,
            marker=symbol,
            linewidth=spec.get(
                "line_width", trace.line_width if trace.line_width is not None else 1
            )
            * scale,
            markersize=(2 * marker_size(graph, spec.get("marker_size", trace.marker_size)) + 1) * scale,
            markerfacecolor=spec.get("marker_fill", fill),
            markeredgecolor=spec.get("marker_edge", stroke),
            markeredgewidth=(trace.marker_thickness if trace.marker_thickness is not None else 0.5)
            * scale,
            color=color,
            label=labels[index],
        )[0]
        plotted.set_gid(f"trace:{trace.name}")
        if trace.z_color or trace.z_size or trace.z_marker:
            from materials_data_studio.tools.igor_trace_channels import render_channels
            plotted.set_visible(False)
            channel_artist = render_channels(ax, document, trace, plot_x, plot_y, mode=mode, scale=scale)
            if channel_artist is not None:
                proxy = Line2D([], [])
                proxy.update_from(plotted)
                proxy.set_visible(True)
                colors = channel_artist.get_facecolors()
                if not len(colors):
                    colors = channel_artist.get_edgecolors()
                if len(colors):
                    from matplotlib.colors import to_hex
                    representative = to_hex(colors[0], keep_alpha=True)
                    proxy.set_color(representative)
                    proxy.set_markerfacecolor(representative)
                    proxy.set_markeredgecolor(representative)
                handles.append(proxy)
                handle_map[trace.name] = proxy
        plotted.set_solid_capstyle(
            {0: "butt", 1: "round", 2: "projecting"}.get(trace.line_cap & 3, "butt")
        )
        plotted.set_solid_joinstyle(
            {0: "miter", 1: "round", 2: "bevel"}.get(trace.line_join, "miter")
        )
        if trace.line_style and line != "None":
            pattern = dict(graph.style.dash_patterns).get(
                trace.line_style, _DASH_PATTERNS.get(trace.line_style, (4, 4))
            )
            width = max(plotted.get_linewidth(), 0.01)
            plotted.set_linestyle((0, tuple(length * scale / width for length in pattern)))
            plotted.set_dash_capstyle(
                {0: "butt", 1: "round", 2: "projecting"}.get(trace.line_cap & 3, "butt")
            )
        if mode == 6:
            plotted.set_drawstyle("steps-mid")
        if not (trace.z_color or trace.z_size or trace.z_marker):
            handles.append(plotted)
            handle_map[trace.name] = plotted
        if trace.x_error_low:
            low_x = document.wave(trace.x_error_low)
            high_x = document.wave(trace.x_error_high or trace.x_error_low)
            if low_x is not None and high_x is not None:
                selection = slice(trace.point_range[0], trace.point_range[1]+1) if trace.point_range else slice(0, count)
                errors_x = np.stack((np.asarray(low_x.data, float)[selection], np.asarray(high_x.data, float)[selection])) * abs(trace.multiplier[0] or 1)
                if (trace.multiplier[0] or 1) < 0:
                    errors_x = errors_x[::-1]
                ax.errorbar(plot_x, plot_y, xerr=None if graph.swap_xy else errors_x, yerr=errors_x if graph.swap_xy else None,
                    fmt="none", capsize=trace.error_cap*scale, elinewidth=trace.error_width*scale,
                    capthick=trace.error_cap_thickness*scale, ecolor=igor_rgb(trace.error_color) if trace.error_color else color, clip_on=trace.error_clip)
        if trace.error_low:
            low_wave = document.wave(trace.error_low)
            high_wave = document.wave(trace.error_high or trace.error_low)
            if low_wave is not None and high_wave is not None:
                selection = slice(trace.point_range[0], trace.point_range[1]+1) if trace.point_range else slice(0, count)
                low_error = np.asarray(low_wave.data, dtype=float)[selection]
                high_error = np.asarray(high_wave.data, dtype=float)[selection]
                if len(low_error) == count and len(high_error) == count:
                    multiplier = trace.multiplier[0 if trace.error_axis == "X" else 1] or 1
                    low_error, high_error = (
                        low_error * abs(multiplier),
                        high_error * abs(multiplier),
                    )
                    if multiplier < 0:
                        low_error, high_error = high_error, low_error
                    errors = np.stack((low_error, high_error))
                    horizontal_error = (trace.error_axis == "X") != graph.swap_xy
                    ax.errorbar(
                        plot_x,
                        plot_y,
                        xerr=errors if horizontal_error else None,
                        yerr=None if horizontal_error else errors,
                        fmt="none",
                        capsize=trace.error_cap * scale,
                        color=igor_rgb(trace.error_color) if trace.error_color else color,
                        linewidth=trace.error_width * scale,
                        capthick=trace.error_cap_thickness * scale,
                        clip_on=trace.error_clip,
                    )
    if graph.contours:
        from materials_data_studio.tools.igor_canvas import render_contours
        render_contours(next(iter(axes.values())), document, graph)
    side_counts: dict[str, int] = {}
    from materials_data_studio.tools.igor_graph_extensions import render_cursors
    render_cursors(axes, document, graph)
    seen_axes: set[str] = set()
    for key, ax in axes.items():
        x_name, name = axis_names[key]
        ax.set_gid("plotaxis:" + key)
        _apply_axes(ax, graph, view, name, x_name)
        _user_ticks(ax, document, graph, x_name, not graph.swap_xy, scale)
        _user_ticks(ax, document, graph, name, graph.swap_xy, scale)
        for category_trace in graph.traces:
            if category_trace.axis != name or category_trace.x_axis != x_name or not category_trace.x_wave or category_trace.box or category_trace.violin:
                continue
            category_wave = document.wave(category_trace.x_wave)
            if category_wave is not None and category_wave.data.dtype.kind in "SU":
                locations = np.arange(len(category_wave.data)) * (category_trace.multiplier[0] or 1) + category_trace.offset[0]
                (ax.set_yticks if graph.swap_xy else ax.set_xticks)(locations, [str(v) for v in category_wave.data])
        own_side = (ax.xaxis if graph.swap_xy else ax.yaxis).get_label_position()
        if not stacked:
            count = side_counts.get(own_side, 0)
            if count:
                ax.spines[own_side].set_position(("outward", 40 * scale * count))
            side_counts[own_side] = count + 1
        model = axis_models.get(name)
        if model and model.free_position is not None:
            position = model.free_position
            if isinstance(position, tuple):
                fraction = 1-position[0] if position[1] == "kwFraction" and own_side in {"right", "top"} else position[0]
                ax.spines[own_side].set_position(
                    ("axes" if position[1] == "kwFraction" else "data", fraction)
                )
            else:
                ax.spines[own_side].set_position(("outward", position * scale))
        if not stacked and not independent_x and len(axes) > 1:
            owned_axis = ax.xaxis if graph.swap_xy else ax.yaxis
            own_side = owned_axis.get_label_position()
            other_side = {"left": "right", "right": "left", "bottom": "top", "top": "bottom"}[
                own_side
            ]
            if any(
                other != name
                and ((axis_models[other].side if other in axis_models else "") or other)
                == other_side
                for other in axes
            ):
                ax.spines[other_side].set_visible(False)
                ax.tick_params(
                    axis="x" if graph.swap_xy else "y",
                    which="both",
                    **{other_side: False, "label" + other_side: False},
                )
            if ax is not next(iter(axes.values())):
                (ax.yaxis if graph.swap_xy else ax.xaxis).set_visible(False)
                for side in ("left", "right") if graph.swap_xy else ("bottom", "top"):
                    ax.spines[side].set_visible(False)
        if independent_x:
            for identifier, horizontal in ((x_name, not graph.swap_xy), (name, graph.swap_xy)):
                component = ax.xaxis if horizontal else ax.yaxis
                sides = ("bottom", "top") if horizontal else ("left", "right")
                if identifier in seen_axes:
                    component.set_visible(False)
                    for side in sides:
                        ax.spines[side].set_visible(False)
                    continue
                seen_axes.add(identifier)
                side = component.get_label_position()
                opposite = sides[1] if side == sides[0] else sides[0]
                # A real independent axis owns its side, not another axis' mirror.
                dimension_names = {p[0 if identifier == x_name else 1] for p in pairs}
                if any(other != identifier and (axis_models.get(other, PxpAxis(other)).side or other)
                       in ({"top", "right"} if opposite in {"top", "right"} else {"bottom", "left"})
                       for other in dimension_names):
                    ax.spines[opposite].set_visible(False)
                    ax.tick_params(axis="x" if horizontal else "y", which="both",
                                   **{opposite: False, "label" + opposite: False})
                model = axis_models.get(identifier)
                if model and model.free_position is not None:
                    position = model.free_position
                    if isinstance(position, tuple):
                        value, cross = position
                        if cross == "kwFraction":
                            value = 1-value if side in {"right", "top"} else value
                        ax.spines[side].set_position(("axes" if cross == "kwFraction" else "data", value))
                    else:
                        ax.spines[side].set_position(("outward", position * scale))
        if stacked and name != y_names[-1]:
            if graph.swap_xy:
                ax.set_ylabel("")
                ax.yaxis.label.set_visible(False)
            else:
                ax.set_xlabel("")
                ax.xaxis.label.set_visible(False)
        if stacked:
            if graph.swap_xy:
                ax.tick_params(
                    axis="y",
                    which="both",
                    left=name == y_names[-1],
                    right=name == y_names[0],
                    labelleft=name == y_names[-1],
                )
            else:
                ax.tick_params(
                    axis="x",
                    which="both",
                    bottom=name == y_names[-1],
                    top=name == y_names[0],
                    labelbottom=name == y_names[-1],
                )
    ratio = _plot_ratio(graph)
    if ratio:
        for ax in axes.values():
            ax.set_box_aspect(ratio)
    if graph.annotations:
        if view.get("legend", True):
            ax = next(iter(axes.values()))
            for annotation in graph.annotations:
                raw = annotation.raw
                if annotation.trace and annotation.point is not None:
                    from materials_data_studio.tools.igor_graph_extensions import tag_text
                    raw = tag_text(document, graph, annotation)
                frame = _render_annotation(ax, graph, raw, annotation.style, handle_map, scale, annotation.name)
                if annotation.trace and annotation.point is not None:
                    from materials_data_studio.tools.igor_graph_extensions import place_tag
                    place_tag(frame, axes, document, graph, annotation)

    elif handles and view.get("legend", bool(graph.legend_raw)):
        ax = next(iter(axes.values()))
        style = graph.legend_style
        x, y, location = _annotation_anchor(style)
        font = re.search(r"\\Z(\d{1,2})", graph.legend_raw)
        legend = ax.legend(
            handles=handles,
            loc=location,
            bbox_to_anchor=(x, y),
            frameon=style.frame != 0,
            facecolor="white",
            framealpha=0 if style.transparent else 1,
            prop={
                "family": annotation_font(graph, style).family,
                "size": (int(font[1]) if font else annotation_font(graph, style).size) * scale,
            },
            borderaxespad=0,
            handlelength=1,
            handletextpad=0.4,
        )
        legend.set_gid("legend:legend")
        for text in legend.get_texts():
            text.set_color(igor_rgb(style.color) if style.color else "black")
    _render_drawings(next(iter(axes.values())), graph, scale)
    from materials_data_studio.tools.graph_geometry import (
        fit_automatic_margins,
        place_free_axes,
        position_label,
    )
    place_free_axes(figure, graph, scale)
    fit_automatic_margins(figure, graph)
    place_free_axes(figure, graph, scale)
    for ax in axes.values():
        names = getattr(ax, "_mds_axis_names", ("bottom", "left"))
        if graph.swap_xy:
            names = names[::-1]
        for axis, name in zip((ax.xaxis, ax.yaxis), names, strict=True):
            position_label(ax, axis, _style(graph, name), graph, scale)
    from materials_data_studio.tools.igor_canvas import render_subwindows
    render_subwindows(figure, document, graph)


class _AnnotationFrame(Artist):
    def __init__(self, text: IgorText, style: PxpAnnotationStyle, scale: float) -> None:
        super().__init__()
        self.text, self.style, self.scale = text, style, scale
        self.set_clip_on(False)

    def get_window_extent(self, renderer: Any = None) -> Bbox:
        unit = self.scale * self.figure.dpi / 72
        halo = abs(self.style.padding) if self.style.padding_explicit else max(1, abs(self.style.padding))
        if self.style.frame == 2 and (self.style.offset_mode == "native" or self.style.inherit_graph_font):
            # Calibrated separately from glyph/symbol metrics against the
            # measured 10.03 Arial 10 pt framed legend (60 x 21 pt). This is an
            # explicit supported profile, not a claim of all native fonts.
            size = float(self.text.get_fontsize()) / self.scale
            px, py = (.3*size + halo)*unit, (.4*size + halo)*unit
            self.text.display_offset = (
                px if self.text.get_horizontalalignment() == "left" else -px if self.text.get_horizontalalignment() == "right" else 0,
                py if self.text.get_verticalalignment() == "bottom" else -py if self.text.get_verticalalignment() == "top" else 0,
            )
            box = self.text.get_window_extent(renderer)
            return Bbox.from_extents(box.x0-px, box.y0-py, box.x1+px, box.y1+py)
        self.text.display_offset = (0.0, 0.0)
        box = self.text.get_window_extent(renderer)
        return box.padded(max(.5, abs(self.style.padding))*unit)

    def draw(self, renderer: Any) -> None:
        if not self.get_visible():
            return
        box = self.get_window_extent(renderer)
        if self.style.shadow or self.style.frame_style == 3:
            distance = (self.style.shadow or 3) * self.scale * self.figure.dpi / 72
            shadow = Rectangle(
                (box.x0 + distance, box.y0 - distance),
                box.width,
                box.height,
                transform=Affine2D(),
                facecolor="black",
                edgecolor="none",
                alpha=0.2,
            )
            shadow.set_figure(self.figure)
            shadow.draw(renderer)
        if self.style.frame or not self.style.transparent:
            background = (
                self.axes.get_facecolor()
                if self.style.background_mode == 2 and self.axes is not None
                else self.figure.get_facecolor()
                if self.style.background_mode == 3
                else igor_rgb(self.style.background_color)
            )
            patch = Rectangle(
                (box.x0, box.y0),
                box.width,
                box.height,
                transform=Affine2D(),
                facecolor="none" if self.style.transparent else background,
                edgecolor=igor_rgb(self.style.frame_color) if self.style.frame == 2 else "none",
                linewidth=self.style.frame_width * self.scale,
            )
            figure = self.get_figure()
            if figure is not None:
                patch.set_figure(figure)
            patch.draw(renderer)
            if figure is None:
                return
            if self.style.frame == 1:
                line = Line2D(
                    [box.x0, box.x1],
                    [box.y0, box.y0],
                    transform=Affine2D(),
                    color=igor_rgb(self.style.frame_color),
                    linewidth=self.style.frame_width * self.scale,
                )
                line.set_figure(figure)
                line.draw(renderer)
            elif self.style.frame == 2 and self.style.frame_style in {1, 2}:
                for number in range(self.style.frame_style):
                    inset = (number + 1) * 2 * self.scale * self.figure.dpi / 72
                    outline = Rectangle(
                        (box.x0 - inset, box.y0 - inset),
                        box.width + 2 * inset,
                        box.height + 2 * inset,
                        transform=Affine2D(),
                        fill=False,
                        edgecolor=igor_rgb(self.style.frame_color),
                        linewidth=self.style.frame_width * self.scale,
                    )
                    outline.set_figure(figure)
                    outline.draw(renderer)
        self.text.draw(renderer)
        self.stale = False


def _render_annotation(
    ax: Any,
    graph: PxpGraph,
    raw: str,
    style: PxpAnnotationStyle,
    handles: dict[str, Any],
    scale: float,
    name: str = "",
) -> _AnnotationFrame:
    x, y, location = _annotation_anchor(style)
    ha = "left" if "left" in location else "right" if "right" in location else "center"
    va = "top" if "upper" in location else "bottom" if "lower" in location else "center"
    original = Text(
        x,
        y,
        ha=ha,
        va=va,
        transform=ax.figure.transFigure if style.exterior else ax.transAxes,
        rotation=style.rotation,
        fontsize=annotation_font(graph, style).size * scale,
        fontfamily=annotation_font(graph, style).family,
        color=igor_rgb(style.color) if style.color else "black",
    )
    text = IgorText(original, raw, 0)
    text.set_figure(ax.figure)
    text.symbol_handles, text.font_scale = handles, scale * graph.style.font_multiplier / 100
    text.line_spacing = style.line_spacing * scale
    text.symbol_width = style.symbol_width * scale
    text.symbol_size = style.symbol_size * scale
    text.symbol_min_width = style.symbol_min_width * scale
    text.symbol_max_width = style.symbol_max_width * scale
    text.set_clip_on(False)
    frame = _AnnotationFrame(text, style, scale)
    frame.set_gid(f"annotation:{name}")
    frame.set_zorder(10)
    ax.add_artist(frame)
    return frame


def _render_drawings(ax: Any, graph: PxpGraph, scale: float) -> None:
    from materials_data_studio.tools.graph_drawing import DrawingArtist

    for index, drawing in enumerate(graph.drawings):
        ax.add_artist(DrawingArtist(ax, drawing, scale, index))
