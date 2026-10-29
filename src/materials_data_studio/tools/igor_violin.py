"""Raw-data violin traces with bounded explicit kernel density estimates."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np

from materials_data_studio.tools.command_language import reference, split
from materials_data_studio.tools.igor_box import append_box
from materials_data_studio.tools.pxp_model import PxpViolinPlot


def append_violin(graph: Any, waves: tuple[str, ...], x: str | None = None, **options: Any) -> Any:
    result = append_box(graph, waves, x, **options)
    trace = result.traces[-1]
    assert trace.box is not None
    return replace(result, traces=(*result.traces[:-1], replace(trace, box=None,
        violin=PxpViolinPlot(waves=waves, vertical=trace.box.vertical, category_labels=trace.box.category_labels))))


def modify_violin(traces: list[Any], source: str) -> None:
    target = traces[0].name if traces else ""
    fields = {"bandwidth": "bandwidth", "bandwidthmethod": "bandwidth_method", "kernel": "kernel",
              "curveextension": "extension", "maxdensity": "max_density", "plotside": "side",
              "closeoutline": "close_outline", "boxwidth": "box_width", "showdata": "show_data",
              "showmean": "show_mean", "showmedian": "show_median", "jitter": "jitter",
              "linecolor": "line_color", "fillcolor": "fill_color"}
    for item in split(source):
        key, _, value = item.partition("=")
        key, value = key.strip().lower(), value.strip() or "1"
        if key == "trace":
            target = reference(value).path
            continue
        trace = next((t for t in traces if t.name == target), None)
        if trace is None or trace.violin is None or key not in fields:
            raise ValueError(f"Unsupported Violin Plot trace/property: {target}/{key}")
        if key in {"linecolor", "fillcolor"}:
            parsed: Any = tuple(int(v) for v in split(value.strip("()")))
            if len(parsed) not in {3, 4} or any(not 0 <= c <= 65535 for c in parsed):
                raise ValueError("Violin colors require RGB/RGBA in 0..65535")
        elif key in {"closeoutline", "showdata", "showmean", "showmedian"}:
            if value not in {"0", "1"}:
                raise ValueError("Violin visibility flags must be 0 or 1")
            parsed = value == "1"
        else:
            parsed = float(value)
            if not np.isfinite(parsed) or parsed < 0:
                raise ValueError("Violin values must be finite and nonnegative")
            if key in {"bandwidthmethod", "kernel", "plotside"}:
                allowed = range(1, 7) if key == "kernel" else range(3)
                if parsed != int(parsed) or parsed not in allowed:
                    raise ValueError("Invalid violin method/kernel/side")
                parsed = int(parsed)
        traces[traces.index(trace)] = replace(trace, violin=replace(trace.violin, **{fields[key]: parsed}))


def kde(values: Any, points: Any, bandwidth: float, kernel: int = 5) -> Any:
    values, points = np.asarray(values, float), np.asarray(points, float)
    values = values[np.isfinite(values)]
    if not len(values) or not np.isfinite(bandwidth) or bandwidth <= 0 or kernel not in range(1, 7):
        raise ValueError("KDE requires finite data, positive bandwidth and kernel 1..6")
    if len(values) * len(points) > 20_000_000:
        raise ValueError("KDE is limited to 20 million sample/grid evaluations")
    u = (points[:, None] - values[None, :]) / bandwidth
    inside = np.abs(u) <= 1
    if kernel == 5:
        density = np.exp(-u*u/2) / np.sqrt(2*np.pi)
    elif kernel == 4:
        density = np.where(inside, 1-np.abs(u), 0)
    elif kernel == 6:
        density = inside * .5
    else:
        power = kernel
        density = np.where(inside, (1-u*u)**power * {1: .75, 2: 15/16, 3: 35/32}[kernel], 0)
    return density.mean(axis=1) / bandwidth


def violin_data(values: Any, settings: PxpViolinPlot) -> tuple[Any, Any, float]:
    values = np.asarray(values, float)
    values = values[np.isfinite(values)]
    if not len(values):
        return np.array([]), np.array([]), 0.
    std = float(np.std(values, ddof=1)) if len(values) > 1 else 0.
    if settings.bandwidth:
        bw = settings.bandwidth
    elif std:
        robust = min(std, float(np.subtract(*np.percentile(values, [75, 25]))) / 1.34) or std
        bw = (.9 * robust if settings.bandwidth_method == 0 else 1.06 * std if settings.bandwidth_method == 1 else (4/3)**.2 * std) * len(values)**(-.2)
    else:
        bw = max(abs(float(values[0])) * .01, .01)
    points = np.linspace(values.min()-settings.extension*bw, values.max()+settings.extension*bw, 128)
    return points, kde(values, points, bw, settings.kernel), bw


def render_violin(ax: Any, document: Any, trace: Any, *, swap: bool, scale: float) -> Any:
    v = trace.violin
    waves = [document.wave(ref) for ref in v.waves]
    datasets = [w.data for w in waves] if waves[0].data.ndim == 1 else [waves[0].data[:, i] for i in range(waves[0].shape[1])]
    summaries = [violin_data(d, v) for d in datasets]
    maximum = v.max_density or max((float(np.max(d)) for _, d, _ in summaries if len(d)), default=1)
    xwave = document.wave(trace.x_wave) if trace.x_wave else None
    categorical = xwave is not None and xwave.data.dtype.kind in "SU"
    positions = np.arange(len(datasets), dtype=float) if xwave is None or categorical else np.asarray(xwave.data[:len(datasets)], float)
    if xwave is None and waves[0].data.ndim > 1:
        positions = waves[0].coordinate(0)[:len(datasets)]
    specified_width = v.box_width if v.box_width is not None else 1 / (2 * len(datasets))
    span = max(float(np.ptp(positions)) + 1, 1)
    width = specified_width * span if specified_width <= 1 else specified_width * ax.figure.dpi / 72 / max(ax.bbox.width, 1) * span
    color = np.asarray(v.line_color) / 65535
    fill = np.asarray(v.fill_color) / 65535 if v.fill_color else "none"
    horizontal = v.vertical != swap
    artist = None
    for pos, raw, (points, density, _bw) in zip(positions, datasets, summaries, strict=True):
        if not len(points):
            continue
        radius = density / max(maximum, 1e-30) * width / 2
        left, right = pos-radius if v.side != 2 else np.full_like(radius, pos), pos+radius if v.side != 1 else np.full_like(radius, pos)
        artist = (ax.fill_betweenx if not horizontal else ax.fill_between)(points, left, right,
                    facecolor=fill, edgecolor=color if v.close_outline else "none", linewidth=(trace.line_width or 1)*scale)
        artist.set_gid("trace:" + trace.name)
        if not v.close_outline:
            for boundary in ([left] if v.side == 1 else [right] if v.side == 2 else [left, right]):
                xy = (points, boundary) if horizontal else (boundary, points)
                line = ax.plot(*xy, color=color, linewidth=(trace.line_width or 1)*scale)[0]
                line.set_gid("violin-outline:" + trace.name)

        finite = np.asarray(raw)[np.isfinite(raw)]
        if v.show_data:
            jitter = (np.arange(len(finite)) % 7 - 3) / 6 * width * v.jitter
            xy = (finite, pos+jitter) if horizontal else (pos+jitter, finite)
            dots = ax.scatter(*xy, s=9*scale**2, facecolors="none", edgecolors=color)
            dots.set_gid("trace:" + trace.name)
        for show, value, marker in [(v.show_mean, np.mean(finite), "^"), (v.show_median, np.median(finite), "s")]:
            if show:
                ax.scatter(*(value, pos) if horizontal else (pos, value), color=color, marker=marker, s=16*scale**2)
    if categorical or v.category_labels:
        if categorical:
            assert xwave is not None
        labels = list(xwave.data) if xwave is not None and categorical else [waves[0].dimension_labels[1][i] if len(waves[0].dimension_labels) > 1 and i < len(waves[0].dimension_labels[1]) else "" for i in range(len(datasets))]
        (ax.set_yticks if horizontal else ax.set_xticks)(positions, labels)
    return artist
