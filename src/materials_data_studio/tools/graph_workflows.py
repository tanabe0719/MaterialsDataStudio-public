"""Native plot transfer, explicitly sanitized sharing, and image ROI analysis."""

from __future__ import annotations

import json
import re
from dataclasses import replace

import numpy as np

from materials_data_studio.plotting.plot_spec import PlotSpec
from materials_data_studio.tools.graph_document import EditableGraphDocument
from materials_data_studio.tools.graph_waves import empty_document, make_wave, put_waves
from materials_data_studio.tools.pxp_model import (
    PxpAnnotation,
    PxpAnnotationStyle,
    PxpAxis,
    PxpAxisStyle,
    PxpGraph,
    PxpGraphStyle,
    PxpTrace,
)


def rgb(color: str) -> tuple[int, int, int]:
    from matplotlib.colors import to_rgb
    return tuple(round(value * 65535) for value in to_rgb(color))  # type: ignore[return-value]


def from_plot_spec(spec: PlotSpec) -> EditableGraphDocument:
    """Snapshot exactly the displayed arrays; no external Igor round trip."""
    from materials_data_studio.plotting.igor_export import label_anchor, point_texts
    from materials_data_studio.tools.pxp_style import MARKERS
    document = empty_document()
    waves, traces = [], []
    used: set[str] = set()

    def wave_name(label: str, axis: str, role: str) -> str:
        # Retain Unicode sample identifiers; remove path/Igor text punctuation.
        base = re.sub(r"[^\w]+", "_", f"{label}_{axis}", flags=re.UNICODE).strip("_") or "data"
        if base[0].isdigit():
            base = "data_" + base
        number = 1
        while True:
            suffix = f"_{role}" if number == 1 else f"_{number}_{role}"
            candidate = base[:63 - len(suffix)] + suffix
            if candidate.casefold() not in used:
                used.add(candidate.casefold())
                return candidate
            number += 1

    for index, curve in enumerate(spec.curves, 1):
        label = curve.style.legend_text or curve.label or f"Dataset_{index}"
        x, y = (replace(make_wave(wave_name(label, axis, role), values),
                        wave_note=json.dumps({"source_curve": label, "axis_label": axis, "role": role}, ensure_ascii=False))
                for axis, role, values in ((spec.x_label, "x", curve.x), (spec.y_label, "y", curve.y)))
        waves.extend((x, y))
        style = curve.style
        marker = next((n for n, value in MARKERS.items() if value == style.marker), 19)
        marked = style.marker.lower() != "none"
        lined = style.line_style.lower() != "none"
        traces.append(PxpTrace(f"curve{index}", y.igor_path, x.igor_path,
                               mode=4 if marked and lined else 3 if marked else 0,
                               marker=marker, marker_size=style.marker_size,
                               line_width=style.line_width if lined else 0, color_igor=rgb(style.color),
                               marker_thickness=style.marker_edge_width,
                               marker_fill_igor=rgb(style.color if style.marker_fill_color == "none" else (style.marker_fill_color or style.color)),
                               marker_stroke_igor=rgb(style.marker_edge_color or style.color),
                               use_marker_stroke=True,
                               line_style={"-": 0, "--": 1, ":": 2, "-.": 3}.get(style.line_style, 0)))
    axes = (PxpAxis("bottom", spec.x_label, spec.x_range, style=PxpAxisStyle(log=int(spec.x_log_scale))),
            PxpAxis("left", spec.y_label, spec.y_range, style=PxpAxisStyle(log=int(spec.y_log_scale))))
    graph = PxpGraph("MDS_Plot", tuple(traces), axes,
                     annotations=(PxpAnnotation("title", spec.title, PxpAnnotationStyle(anchor="MT", frame=0, transparent=True)),) if spec.title else (),
                     legend_raw="\r".join(r"\s(" + t.name + ") " + (c.style.legend_text or c.label) for t, c in zip(traces, spec.curves, strict=True)) if spec.legend_enabled else "",
                     style=PxpGraphStyle(width_points=spec.figure_width_cm * 72 / 2.54,
                                         height_points=spec.figure_height_cm * 72 / 2.54,
                                         axis_defaults=PxpAxisStyle(tick=2, mirror=1, grid=int(spec.grid_enabled))))
    labels = point_texts(spec)
    annotations = list(graph.annotations)
    label_settings = {}
    for index, text in enumerate(labels):
        name = f"MDSIndex{index}"
        annotations.append(PxpAnnotation(name, text.text.replace("\\", "\\\\"),
                                         PxpAnnotationStyle(anchor=label_anchor(text.angle), frame=0, transparent=True,
                                                            font_size=text.size, color=rgb(text.color), rotation=text.angle,
                                                            y_percent=text.gap / (spec.figure_height_cm * 72 / 2.54) * 100),
                                         trace=traces[text.trace].name, point=text.point))
        label_settings[name] = {"font": text.font or "Arial", "point_label": True}
    if labels and graph.legend_raw:
        annotations.append(PxpAnnotation("legend0", graph.legend_raw, PxpAnnotationStyle(anchor="LT", x_percent=2, y_percent=-2)))
    graph = replace(graph, annotations=tuple(annotations))
    document.current.waves = tuple(waves)
    document.current.graphs = (graph,)
    document.current.layout[graph.name] = {"view": {"traces": {trace.name: {"marker_fill": curve.style.marker_fill_color or curve.style.color} for trace, curve in zip(traces, spec.curves, strict=True)}}}
    document.current.layout[graph.name]["annotations"] = label_settings
    document.current.environment["transfer"] = {"kind": "displayed PlotSpec snapshot", "title": spec.title}
    return document


def sharing_copy(document: EditableGraphDocument, names: list[str]) -> EditableGraphDocument:
    """Rebase only selected current graphs and references onto a clean origin.

    Graph text and wave names intentionally remain: the content review must
    explain that these can contain sample identifiers. Original bytes, source
    paths, deleted waves, procedures, command history and wave notes do not.
    """
    if not names:
        raise ValueError("Select at least one graph to share")
    selected = set(names)
    for name in list(names):
        model = document.graph(name)
        selected.update(item.graph for item in model.layout_items)
        selected.update(g.name for g in document.current.graphs if g.host == name)
        if model.host:
            selected.add(model.host)
    graphs = tuple(g for g in document.current.graphs if g.name in selected)
    projection = document.render_source()
    references = set()
    for graph in graphs:
        from materials_data_studio.tools.graph_waves import trace_wave_references
        references.update(ref for trace in graph.traces for ref in trace_wave_references(trace) if ref)
        references.update(ref for image in graph.images for ref in (image.wave, image.x_wave, image.y_wave) if ref)
        references.update(c.wave for c in graph.contours)
        references.update(image.color_table for image in graph.images if projection.wave(image.color_table))
        references.update(ref for axis in graph.axes for ref in (axis.style.user_ticks or ()))
        references.update(graph.style.axis_defaults.user_ticks or ())
    waves = []
    for ref in sorted(references):
        wave = projection.wave(ref)
        if wave is None:
            raise ValueError(f"Missing referenced wave: {ref}")
        if not any(w.id == wave.id for w in waves):
            waves.append(replace(wave, wave_note=""))
    clean = empty_document()
    safe_graphs = tuple(replace(g, issues=()) for g in graphs)
    clean.source = replace(clean.source, basename="Shared.mdsg", waves=tuple(waves), graphs=safe_graphs,
                           status="FULL", folders=tuple(dict.fromkeys(["root", *(w.igor_path.rsplit(":", 1)[0] for w in waves)])))
    clean.current.graphs = safe_graphs
    clean.current.layout = {graph.name: _sharing_layout(graph, document.current.layout.get(graph.name, {})) for graph in graphs}
    # Force a value copy of layout and restrict it to supported visual keys;
    # environment (Python source/logs) and provenance are never carried over.
    clean.current.layout = json.loads(json.dumps(clean.current.layout))
    return clean


def _sharing_layout(graph: PxpGraph, layout: dict) -> dict:
    """Do not copy unknown metadata or overrides for removed objects."""
    def select(values, identifiers, allowed):
        return {name: {key: value for key, value in settings.items() if key in allowed}
                for name, settings in values.items() if name in identifiers and isinstance(settings, dict)} if isinstance(values, dict) else {}
    axes = {"bottom", "top", "left", "right", *(axis.name for axis in graph.axes), *(trace.axis for trace in graph.traces), *(trace.x_axis for trace in graph.traces)}
    traces = {trace.name for trace in graph.traces}
    images = {image.wave for image in graph.images}
    annotations = {"legend", "text0", *(a.name for a in graph.annotations)}
    source_view = layout.get("view", {})
    view = {"legend": source_view["legend"]} if "legend" in source_view else {}
    view["axes"] = select(source_view.get("axes", {}), axes, {"label", "limits"})
    view["traces"] = select(source_view.get("traces", {}), traces, {"visible", "color", "marker_fill", "marker_edge", "marker_size", "line_width", "line_style"})
    view["images"] = select(source_view.get("images", {}), images, {"visible", "color_table", "color_range", "reverse", "log_color"})
    result = {"view": view, "labels": select(layout.get("labels", {}), axes, {"coordinate_system", "offset_points", "lateral"}),
              "annotations": select(layout.get("annotations", {}), annotations, {"visible", "font", "rotation"}),
              "colorbars": select(layout.get("colorbars", {}), images, {"font", "label_size", "position", "rotation"})}
    return result


def image_roi(document: EditableGraphDocument, reference: str, bounds: tuple[float, float, float, float],
              operation: str, name: str):
    """Inclusive native-coordinate ROI; no hidden resampling or extrapolation."""
    wave = document.render_source().wave(reference)
    if wave is None or wave.data.ndim != 2 or wave.data.dtype.kind not in "biuf":
        raise ValueError("Select a real numeric 2D image wave")
    xmin, xmax, ymin, ymax = bounds
    if xmin > xmax or ymin > ymax:
        raise ValueError("ROI minimum must not exceed maximum")
    x, y = wave.coordinate(0), wave.coordinate(1)
    mx, my = (x >= xmin) & (x <= xmax), (y >= ymin) & (y <= ymax)
    if not np.any(mx) or not np.any(my):
        raise ValueError("ROI contains no samples")
    data = np.asarray(wave.data[np.ix_(mx, my)], dtype=float)
    if operation in {"profile_x", "profile_y"}:
        dimension = 0 if operation == "profile_x" else 1
        coordinates = x[mx] if dimension == 0 else y[my]
        values = np.nanmean(data, axis=1 if dimension == 0 else 0)
        result = make_wave(name, values)
        result = replace(result, dimension_scaling=((float(coordinates[0]), wave.dimension_scaling[dimension][1]),),
                         dimension_units=(wave.dimension_units[dimension],), data_units=wave.data_units)
    elif operation in {"sum", "mean", "integral"}:
        if operation == "integral":
            if data.shape[0] < 2 or data.shape[1] < 2 or not np.isfinite(data).all():
                raise ValueError("Physical integration requires at least 2×2 finite samples")
            ox, oy = np.argsort(x[mx]), np.argsort(y[my])
            value = np.trapezoid(np.trapezoid(data[np.ix_(ox, oy)], y[my][oy], axis=1), x[mx][ox])
        else:
            value = np.nansum(data) if operation == "sum" else np.nanmean(data)
        result = make_wave(name, [value])
        units = " ".join(filter(None, (wave.data_units, *wave.dimension_units))) if operation == "integral" else wave.data_units
        result = replace(result, data_units=units)
    else:
        raise ValueError("Unknown ROI operation")
    result = replace(result, wave_note=f"MDS ROI {operation}: {reference}, bounds={bounds}; finite samples={np.isfinite(data).sum()}")
    return put_waves(document, [result])
