"""Semantic box traces, native quartiles and explicit appearance settings."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np

from materials_data_studio.tools.command_language import reference, split
from materials_data_studio.tools.pxp_model import PxpAxis, PxpBoxPlot, PxpGraph, PxpTrace


def append_box(graph: PxpGraph, waves: tuple[str, ...], x: str | None = None, *,
               name: str = "", axis: str = "left", x_axis: str = "bottom",
               vertical: bool = False, categories: bool = False) -> PxpGraph:
    """Append one box trace referencing all original datasets."""
    if not waves or len(waves) > 100:
        raise ValueError("Box Plot requires 1..100 dataset waves")
    base = name or waves[0].rsplit(":", 1)[-1]
    name, i = base, 0
    while any(t.name == name for t in graph.traces):
        i += 1
        name = base + f"#{i}"
    axes = list(graph.axes)
    for identifier in (axis, x_axis):
        if not any(a.name == identifier for a in axes):
            axes.append(PxpAxis(identifier))
    trace = PxpTrace(name, waves[0], x, axis=axis, x_axis=x_axis,
                     box=PxpBoxPlot(waves=waves, vertical=vertical, category_labels=categories))
    return replace(graph, traces=(*graph.traces, trace), axes=tuple(axes))


def modify_box(traces: list[PxpTrace], source: str) -> None:
    """Apply supported ModifyBoxPlot properties; never ignore unknown settings."""
    target = traces[0].name if traces else ""
    fields = {"notched": "notched", "quartilemethod": "quartile_method", "whiskermethod": "whisker_method",
              "outliermethod": "outlier_method", "showdata": "show_data", "showmean": "show_mean",
              "showfences": "show_fences", "medianismarker": "median_is_marker", "boxwidth": "box_width",
              "capwidth": "cap_width", "jitter": "jitter", "boxfill": "box_fill",
              "boxcolor": "box_color", "medianlinecolor": "median_color", "whiskercolor": "whisker_color"}
    for item in split(source):
        key, _, value = item.partition("=")
        key = key.strip().lower()
        value = value.strip() or "1"
        if key == "trace":
            target = reference(value).path
            continue
        trace = next((t for t in traces if t.name == target), None)
        if trace is None or trace.box is None:
            raise ValueError(f"Not a Box Plot trace: {target}")
        if key not in fields:
            raise ValueError(f"Unsupported Box Plot property: {key}")
        field = fields[key]
        result: Any
        if key in {"notched", "showmean", "showfences", "medianismarker"}:
            if value not in {"0", "1"}:
                raise ValueError(f"{key} must be 0 or 1")
            result = value == "1"
        elif key == "showdata":
            if value.casefold() not in {"all", "none", "outliers", "faroutliers"}:
                raise ValueError("showData must be All, None, Outliers or FarOutliers")
            result = value
        elif key == "quartilemethod":
            result = int(value)
            if result not in range(4):
                raise ValueError("quartileMethod must be 0..3")
        elif key in {"whiskermethod", "outliermethod"}:
            result = tuple(float(v) for v in split(value.strip("{}")))
            method = int(result[0])
            limit = 7 if key == "whiskermethod" else 3
            expected = (3 if method == 6 else 2 if method == 7 else 1) if key == "whiskermethod" else (3 if method == 2 else 5 if method == 3 else 1)
            if result[0] != method or not 0 <= method <= limit or len(result) != expected:
                raise ValueError(f"Invalid {key} method or parameters")
        elif key in {"boxfill", "boxcolor", "medianlinecolor", "whiskercolor"}:
            result = None if key == "boxfill" and value == "0" else tuple(int(v) for v in split(value.strip("()")))
            if result is not None and (len(result) not in {3, 4} or any(not 0 <= c <= 65535 for c in result)):
                raise ValueError("Box colors require RGB/RGBA in 0..65535")
        else:
            result = float(value)
            if result < 0:
                raise ValueError("Box dimensions/jitter cannot be negative")
        if isinstance(result, (float, tuple)) and result is not None and not np.isfinite(result).all():
            raise ValueError("Box settings must be finite")
        traces[traces.index(trace)] = replace(trace, box=replace(trace.box, **{field: result}))


def box_statistics(data: Any, settings: PxpBoxPlot) -> dict[str, Any]:
    """Compute hinges, whiskers, notch and classifications independently of Artists."""
    values = np.sort(np.asarray(data, dtype=float).ravel())
    values = values[np.isfinite(values)]
    if not values.size:
        return dict(med=np.nan, q1=np.nan, q3=np.nan, whislo=np.nan, whishi=np.nan,
                    mean=np.nan, cilo=np.nan, cihi=np.nan, fliers=[], values=values,
                    outliers=np.zeros(0, bool), far_outliers=np.zeros(0, bool), fences=(np.nan,) * 4)
    n = len(values)
    median = float(np.median(values))
    method = settings.quartile_method
    if method in {0, 2}:
        half = (n + 1) // 2 if method == 0 else max(1, n // 2)
        q1, q3 = float(np.median(values[:half])), float(np.median(values[-half:]))
    else:
        indices = np.array([.25, .75]) * (n + 1) - 1
        if method == 3:
            indices = np.array([np.floor(indices[0] + .5), np.ceil(indices[1] - .5)])
        q1, q3 = np.interp(indices, np.arange(n), values)
    iqr = q3 - q1
    inner_lo, inner_hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    outer_lo, outer_hi = q1 - 3 * iqr, q3 + 3 * iqr
    mean = float(np.mean(values))
    std = float(np.std(values, ddof=1)) if n > 1 else 0.
    m, *params = settings.whisker_method
    if m == 0:
        lo, hi = values[0], values[-1]
    elif m == 1:
        lo, hi = inner_lo, inner_hi
    elif m == 2:
        valid = values[(values >= inner_lo) & (values <= inner_hi)]
        lo, hi = valid[0], valid[-1]
    elif m in {3, 7}:
        k = params[0] if m == 7 else 1.
        lo, hi = mean - k * std, mean + k * std
    else:
        limits = params if m == 6 else (9, 91) if m == 4 else (2, 98)
        if not 0 <= limits[0] <= limits[1] <= 100:
            raise ValueError("Whisker percentiles must be ordered in 0..100")
        lo, hi = np.percentile(values, limits)
    om, *op = settings.outlier_method
    if om == 1:
        out, far = (values < lo) | (values > hi), np.zeros(n, bool)
    elif om == 2:
        out, far = np.abs(values - mean) > op[0] * std, np.abs(values - mean) > op[1] * std
    elif om == 3:
        out, far = (values < op[1]) | (values > op[2]), (values < op[0]) | (values > op[3])
    else:
        out, far = (values < inner_lo) | (values > inner_hi), (values < outer_lo) | (values > outer_hi)
    notch = 1.57 * iqr / np.sqrt(n)
    return dict(med=median, q1=q1, q3=q3, whislo=lo, whishi=hi, mean=mean,
                cilo=median - notch, cihi=median + notch, fliers=[], values=values,
                outliers=out, far_outliers=far, fences=(inner_lo, inner_hi, outer_lo, outer_hi))


def render_box(ax: Any, document: Any, trace: PxpTrace, *, swap: bool, scale: float) -> Any:
    """Render dataset summaries and original points with stable, bounded jitter."""
    box = trace.box
    assert box is not None
    source = [document.wave(ref) for ref in box.waves]
    if any(w is None for w in source):
        raise ValueError(f"Box Plot {trace.name}: missing dataset")
    datasets = [w.data for w in source] if source[0].data.ndim == 1 else [source[0].data[:, i] for i in range(source[0].shape[1])]
    count = len(datasets)
    xwave = document.wave(trace.x_wave) if trace.x_wave else None
    categories = None
    if xwave is not None:
        if xwave.data.dtype.kind in "SU":
            categories = list(map(str, xwave.data[:count]))
            positions = np.arange(count, dtype=float)
        else:
            positions = np.asarray(xwave.data[:count], dtype=float)
    elif box.category_labels:
        from materials_data_studio.tools.igor_wave_ops import dimension_label
        categories = [dimension_label(source[0], 1, i) for i in range(count)]
        positions = np.arange(count, dtype=float)
    else:
        positions = source[0].coordinate(0)[:count] if source[0].data.ndim == 2 else np.arange(count, dtype=float)
    stats = [box_statistics(d, box) for d in datasets]
    horizontal = box.vertical != swap
    width = box.box_width if box.box_width is not None else .5
    if width > 1:
        extent = ax.bbox.height if horizontal else ax.bbox.width
        width = width * ax.figure.dpi / 72 / extent * max(float(np.ptp(positions)), 1.)
    color = np.asarray(box.box_color[:3]) / 65535
    fill = np.asarray(box.box_fill) / 65535 if box.box_fill else "none"
    from inspect import signature
    orientation = {"orientation": "horizontal" if horizontal else "vertical"} if "orientation" in signature(ax.bxp).parameters else {"vert": not horizontal}
    artists = ax.bxp(stats, positions=positions, widths=width, **orientation,
                     shownotches=box.notched, showmeans=box.show_mean, showfliers=False,
                     showcaps=box.cap_width != 0, capwidths=width * box.cap_width,
                     patch_artist=True, manage_ticks=False,
                     boxprops={"facecolor": fill, "edgecolor": color, "linewidth": (trace.line_width or 1) * scale},
                     medianprops={"color": np.asarray(box.median_color[:3]) / 65535},
                     whiskerprops={"color": np.asarray(box.whisker_color[:3]) / 65535})
    for group in artists.values():
        for artist in group:
            artist.set_gid(f"trace:{trace.name}")
    # bxp adds filled boxes as Artists without extending the data limits by
    # their width. Include the complete polygon so edge boxes are not clipped.
    for patch in artists["boxes"]:
        ax.update_datalim(patch.get_path().vertices)
    ax.autoscale_view()
    for pos, stat in zip(positions, stats, strict=True):
        if box.median_is_marker:
            xy = (stat["med"], pos) if horizontal else (pos, stat["med"])
            median = ax.scatter(*xy, color=color, marker="o", s=9 * scale**2)
            median.set_gid(f"trace:{trace.name}")
            for line in artists["medians"]:
                line.set_visible(False)
        values, out, far = stat["values"], stat["outliers"], stat["far_outliers"]
        show = box.show_data.casefold()
        selected = np.ones(len(values), bool) if show == "all" else out if show == "outliers" else far if show == "faroutliers" else np.zeros(len(values), bool)
        # Deterministic collision offsets; no random state or stored Artists.
        offsets = (np.arange(len(values)) % 7 - 3) / 6 * box.jitter * width
        for mask, marker, face in [(selected & ~out, "o", "none"), (selected & out & ~far, "o", color), (selected & far, "s", color)]:
            xy = (values[mask], pos + offsets[mask]) if horizontal else (pos + offsets[mask], values[mask])
            artist = ax.scatter(*xy, marker=marker, facecolors=face, edgecolors=color, s=9 * scale**2, linewidths=.5 * scale)
            artist.set_gid(f"trace:{trace.name}")
        if box.show_fences:
            for fence in stat["fences"]:
                xy = ([fence, fence], [pos - width / 2, pos + width / 2]) if horizontal else ([pos - width / 2, pos + width / 2], [fence, fence])
                ax.plot(*xy, linestyle=":", color=color)
    if categories:
        (ax.set_yticks if horizontal else ax.set_xticks)(positions, categories)
    return artists["boxes"][0] if artists["boxes"] else None
