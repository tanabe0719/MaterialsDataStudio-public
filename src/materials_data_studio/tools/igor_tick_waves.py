"""Generate Igor user-tick waves from the rendered axis."""
from __future__ import annotations

from dataclasses import replace

import numpy as np

from materials_data_studio.tools.graph_waves import make_wave


def tick_waves(document, graph, name, values_name, labels_name, *, mode=1, figsize=(6.4, 4.8), dpi: float = 100, view=None):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    from materials_data_studio.tools.pxp_render import render_graph

    horizontal_names = {"bottom", "top", *(trace.x_axis for trace in graph.traces)}
    names = horizontal_names | {"left", "right", *(trace.axis for trace in graph.traces), *(axis.name for axis in graph.axes)}
    if name not in names or mode not in {0, 1, 2}:
        raise ValueError("Select an existing axis and an /AUTO mode of 0, 1, or 2")
    if any(trace.x_axis == name and trace.x_wave and (wave := document.wave(trace.x_wave)) is not None
           and np.dtype(wave.dtype).kind in "US" for trace in graph.traces):
        raise ValueError("TickWavesFromAxis requires a numerical axis")
    def tick_style(style):
        if mode == 1:
            return replace(style, user_ticks=None, manual_ticks=None, manual_minor=None)
        if mode == 0 and style.manual_ticks is None:
            return replace(style, user_ticks=None, manual_ticks=(0., 1., 0, 0))
        return replace(style, user_ticks=None)

    reference = replace(graph, axes=tuple(
        replace(axis, style=tick_style(axis.style)) if axis.name == name else axis for axis in graph.axes
    ), style=replace(graph.style, axis_defaults=tick_style(graph.style.axis_defaults)))
    figure = Figure(figsize=figsize, dpi=dpi)
    FigureCanvasAgg(figure)
    render_graph(figure, document, reference, view)
    axes = next((axes for axes in figure.axes if name in getattr(axes, "_mds_axis_names", ())), None)
    if axes is None:
        raise ValueError("No plotted axis is available for tick generation")
    axis = axes.xaxis if (name in horizontal_names) != graph.swap_xy else axes.yaxis
    low, high = sorted(axis.get_view_interval())
    def visible(values):
        values = np.asarray(values, dtype=float)
        return values[np.isfinite(values) & (values >= low) & (values <= high)]
    major = visible(axis.get_majorticklocs())
    minor = visible(axis.get_minorticklocs())
    if not 0 < len(major) <= 2000 or len(major) + len(minor) > 4000:
        raise ValueError("No bounded automatic ticks are available")
    formatter = axis.get_major_formatter()
    labels = formatter.format_ticks(major.tolist())
    if formatter.get_offset() or any("$" in label for label in labels):
        labels = [f"{value:g}" for value in major]
    positions = np.concatenate((major, minor))
    labels += [""] * len(minor)
    types = ["Major"] * len(major) + ["Minor"] * len(minor)
    order = np.argsort(positions, kind="stable")
    values_wave = make_wave(values_name, positions[order])
    labels_wave = make_wave(labels_name, np.column_stack((labels, types))[order])
    return values_wave, replace(labels_wave, dimension_labels=((), ("", "Tick Type")))
