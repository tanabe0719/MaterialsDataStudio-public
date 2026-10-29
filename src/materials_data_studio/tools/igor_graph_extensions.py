"""Semantic table formatting, persistent cursors and explicit axis date formats."""
from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np

from materials_data_studio.tools.pxp_model import PxpCursor

EPOCH = datetime(1904, 1, 1, tzinfo=UTC)


def axis_date_text(value: float, info: tuple[int, int, int] | None) -> str:
    if info is None or not np.isfinite(value):
        return str(value)
    suppress, time_mode, date_mode = info
    try:
        stamp = EPOCH + timedelta(seconds=float(value))
    except (OverflowError, ValueError):
        return str(value)
    date = stamp.strftime(("%Y-%m-%d", "%A, %B %d, %Y", "%b %d, %Y")[date_mode])
    if time_mode == 2:
        total = int(abs(value))
        time = f"{'-' if value < 0 else ''}{total//3600}:{total//60%60:02d}:{total%60:02d}"
    else:
        time = stamp.strftime("%I:%M:%S %p" if time_mode == 0 else "%H:%M:%S")
    return time if suppress == 1 else date if suppress == 2 else date + "\n" + time


def date_text(value: float, mode: int = 8) -> str:
    if not np.isfinite(value):
        return str(value)
    try:
        stamp = EPOCH + timedelta(seconds=float(value))
    except (OverflowError, ValueError):
        return str(value)
    return stamp.strftime("%Y-%m-%d" if mode == 6 else "%H:%M:%S" if mode == 7 else "%Y-%m-%d %H:%M:%S")


def format_cell(value: Any, styles: dict[str, Any]) -> str:
    if not isinstance(value, (float, int, np.number)):
        return str(value)
    mode = int(styles.get("format", 0))
    digits = int(styles.get("sigdigits", styles.get("digits", 6))) if mode == 0 else int(styles.get("digits", 6))
    if not np.isfinite(value):
        return str(value)
    if mode in {6, 7, 8}:
        return date_text(float(value), mode)
    if mode in {1, 2, 9, 10}:
        return format(int(np.rint(value)), (f"0{digits}" if mode in {9,10} else "") + {1: "d", 2: ",d", 9: "o", 10: "X"}[mode])
    return format(value, f"{'#' if mode == 0 and styles.get('trailingzeros') else ',' if mode == 4 else ''}.{digits}{'f' if mode in {3, 4} else 'e' if mode == 5 else 'g'}")


def table_styles(table: Any, reference: str) -> dict[str, Any]:
    values = {}
    for column, key, raw in table.column_styles:
        if column == "*" or column == reference or column == reference.rsplit(":", 1)[-1]:
            values[key] = json.loads(raw)
    return values



def table_reference(reference: str, services: Any) -> str:
    match = re.search(r"\.(i|l|d)$", reference)
    suffix = match[0] if match else ""
    return services.wave(reference[:-len(suffix)] if suffix else reference).igor_path + suffix


def modify_table(table: Any, settings: Any, services: Any, evaluate: Any) -> Any:
    widths = dict(table.widths)
    styles = {(column, key): raw for column, key, raw in table.column_styles}
    for option in settings:
        key = option.name.lower()
        column = table_reference(option.target, services).removesuffix(".d") if option.target and option.target != "Point" else option.target or "*"
        value = evaluate(option.expression)
        if key in {"digits", "sigdigits", "format", "size", "style", "showunits", "trailingzeros", "alignment"}:
            if np.ndim(value) or not np.isfinite(value) or int(value) != value:
                raise ValueError(f"ModifyTable {key} requires a finite integer")
            bounds = (0, 15) if key in {"digits", "sigdigits"} else (0, 10) if key == "format" else (1, 200) if key == "size" else (0, 31) if key == "style" else (0, 2) if key == "alignment" else (0, 1)
            if not bounds[0] <= value <= bounds[1]:
                raise ValueError(f"ModifyTable {key} outside supported range")
            value = int(value)
        elif key == "width":
            if not np.isfinite(value) or not 1 <= value <= 10000:
                raise ValueError("Table width must be in 1..10000")
            for target in table.columns if column == "*" else (column,):
                widths[target] = float(value)
            continue
        elif key in {"font", "title"}:
            if not isinstance(value, str) or len(value) > 255:
                raise ValueError("Table font must be text")
        elif key == "rgb":
            if len(value) not in {3, 4} or any(int(c) != c or not 0 <= c <= 65535 for c in value):
                raise ValueError("Table RGB requires 3 or 4 channels in 0..65535")
            value = list(value)
        else:
            raise ValueError(f"Unsupported ModifyTable property: {key}")
        styles[column, key] = json.dumps(value, ensure_ascii=False)
    return replace(table, widths=tuple(widths.items()), column_styles=tuple((column, key, raw) for (column, key), raw in styles.items()))


def apply_cursor(engine: Any, command: Any, flag: Any) -> None:
    s, args = engine.services, command.arguments
    graph = s.graph()
    name = engine.text(args[0]).upper()
    if name not in "ABCDEFGHIJ" or len(name) != 1:
        raise ValueError("Cursor name must be A..J")
    cursors = tuple(c for c in graph.cursors if c.name != name)
    if any(f.name == "K" for f in command.flags):
        s.put_graph(replace(graph, cursors=cursors))
        return
    if len(args) != 3:
        raise ValueError("Cursor takes name, trace and X coordinate (or /P point index)")
    trace = next((t for t in graph.traces if t.name == engine.text(args[1])), None)
    if trace is None or trace.box or trace.violin:
        raise ValueError("Cursor requires a numeric XY trace")
    y = s.wave(trace.y_wave)
    x = s.wave(trace.x_wave).data if trace.x_wave else y.coordinate(0)
    value = float(engine.number(args[2]))
    if not np.isfinite(value):
        raise ValueError("Cursor position must be finite")
    if any(f.name == "P" for f in command.flags):
        if int(value) != value:
            raise ValueError("Cursor point must be an integer")
        point = int(value)
    else:
        eligible = np.isfinite(x)
        if not np.any(eligible):
            raise ValueError("No finite cursor coordinates")
        point = int(np.flatnonzero(eligible)[np.argmin(np.abs(x[eligible]-value))])
    if not 0 <= point < y.shape[0]:
        raise ValueError("Cursor point is outside the trace")
    color = tuple(int(v) for v in engine.number(next(f.expression for f in command.flags if f.name == "C"))) if any(f.name == "C" for f in command.flags) else (65535, 0, 0)
    if len(color) != 3 or any(not 0 <= c <= 65535 for c in color):
        raise ValueError("Cursor color requires RGB in 0..65535")
    s.put_graph(replace(graph, cursors=(*cursors, PxpCursor(name, trace.name, point, color))))


def cursor_value(services: Any, kind: str, cursor: str, window: str = "") -> float:
    graph = services.graph(window or None)
    c = next((c for c in graph.cursors if c.name == cursor.upper()), None)
    if c is None:
        raise ValueError("Cursor does not exist")
    trace = next(t for t in graph.traces if t.name == c.trace)
    wave = services.wave(trace.y_wave)
    if kind == "p":
        return float(c.point)
    if kind == "y":
        return float(wave.data[c.point])
    return float((services.wave(trace.x_wave).data if trace.x_wave else wave.coordinate(0))[c.point])


def render_cursors(axes: Any, document: Any, graph: Any) -> None:
    for cursor in graph.cursors:
        trace = next((t for t in graph.traces if t.name == cursor.trace), None)
        if trace is None:
            continue
        y = document.wave(trace.y_wave)
        x = document.wave(trace.x_wave) if trace.x_wave else None
        if y is None or cursor.point >= len(y.data):
            continue
        ax = axes[trace.axis if trace.x_axis == "bottom" else f"{trace.axis}@{trace.x_axis}"]
        xy = ((x.data if x else y.coordinate(0))[cursor.point] * (trace.multiplier[0] or 1) + trace.offset[0], y.data[cursor.point] * (trace.multiplier[1] or 1) + trace.offset[1])
        xy = (xy[1], xy[0]) if graph.swap_xy else xy
        color = tuple(c/65535 for c in cursor.color)
        artist = ax.scatter(*xy, marker="+", color=color, s=64, zorder=15)
        artist.set_gid("cursor:"+cursor.name)
        text = ax.annotate(cursor.name, xy, xytext=(6, 6), textcoords="offset points", color=color)
        text.set_gid("cursor-label:"+cursor.name)


def render_image_mesh(ax: Any, document: Any, graph: Any, spec: Any, wave: Any, colors: Any, norm: Any) -> Any:
    edges = []
    for dimension, reference in enumerate((spec.x_wave, spec.y_wave)):
        coordinate = document.wave(reference) if reference else None
        if coordinate is not None:
            values = np.asarray(coordinate.data, float)
        else:
            offset, delta = wave.dimension_scaling[dimension]
            values = offset + (np.arange(wave.shape[dimension]+1)-.5)*delta
        edges.append(values)
    pixels = np.asarray(wave.data, float)
    if pixels.ndim == 3:
        pixels = np.clip(pixels / (255 if wave.data.dtype == np.uint8 else 65535), 0, 1)
    pixels = pixels if graph.swap_xy else np.swapaxes(pixels, 0, 1)
    x, y = edges[::-1] if graph.swap_xy else edges
    return ax.pcolormesh(x, y, pixels, cmap=colors if wave.data.ndim == 2 else None,
                        norm=norm if wave.data.ndim == 2 else None, shading="flat", zorder=0)


def tag_text(document: Any, graph: Any, annotation: Any) -> str:
    trace = next((t for t in graph.traces if t.name == annotation.trace), None)
    if trace is None or annotation.point is None:
        return annotation.raw
    y = document.wave(trace.y_wave)
    x = document.wave(trace.x_wave) if trace.x_wave else None
    point = annotation.point
    if y is None or not 0 <= point < len(y.data):
        return annotation.raw
    values = {"N": y.name, "n": trace.name, "P": str(point), "X": f"{(x.data if x else y.coordinate(0))[point]:g}",
              "Y": f"{y.data[point]:g}", "Z": "NaN", "x": f"{trace.offset[0]:g}", "y": f"{trace.offset[1]:g}"}
    return re.sub(r"\\[O0]([NnPXYZxy])", lambda match: values[match[1]], annotation.raw)


def place_tag(frame: Any, axes: Any, document: Any, graph: Any, annotation: Any) -> None:
    from matplotlib.transforms import Affine2D
    trace = next((t for t in graph.traces if t.name == annotation.trace), None)
    if trace is None or annotation.point is None:
        return
    y = document.wave(trace.y_wave)
    x = document.wave(trace.x_wave) if trace.x_wave else None
    point = annotation.point
    if y is None or not 0 <= point < len(y.data):
        return
    ax = axes[trace.axis if trace.x_axis == "bottom" else f"{trace.axis}@{trace.x_axis}"]
    xy = ((x.data if x else y.coordinate(0))[point] * (trace.multiplier[0] or 1) + trace.offset[0],
          y.data[point] * (trace.multiplier[1] or 1) + trace.offset[1])
    xy = xy[::-1] if graph.swap_xy else xy
    # Translate in axes coordinates so offsets remain percentages after resize.
    transform = ax.transData + ax.transAxes.inverted() + Affine2D().translate(annotation.style.x_percent/100, annotation.style.y_percent/100) + ax.transAxes
    frame.text.set_transform(transform)
    frame.text.set_position(xy)
    if annotation.arrow:
        arrow = ax.annotate("", xy=xy, xycoords=ax.transData, xytext=xy, textcoords=transform,
                            arrowprops={"arrowstyle": {1: "-", 2: "->", 3: "<-", 4: "<->"}[annotation.arrow],
                                        "color": tuple(c/65535 for c in annotation.style.frame_color), "linewidth": .5})
        arrow.set_gid("tag-arrow:" + annotation.name)


def colorscale_options(image: Any, options: list[tuple[str, Any]]) -> Any:
    style, axis = image.colorbar_style, image.colorbar_axis
    dimensions = {"width": "width", "height": "height", "heightpct": "height_percent", "widthpct": "width_percent", "fsize": "font_size"}
    properties = {"nticks": "tick_count", "minor": "minor_enabled", "tickthick": "major_thickness", "lblmargin": "label_margin", "lblrot": "label_rotation", "tklblrot": "tick_rotation", "font": "font_family"}
    for key, value in options:
        key = key.lower()
        if key == "image":
            continue
        if key == "axislabel":
            image = replace(image, colorbar_label_raw=str(value))
        elif key in dimensions:
            if not np.isfinite(value) or value < 0 or value > 10000:
                raise ValueError("ColorScale dimensions must be finite and nonnegative")
            style = replace(style, **{dimensions[key]: float(value)}, inherit_graph_font=False if key == "fsize" else style.inherit_graph_font)
        elif key in {"vert", "log"}:
            if value not in {0, 1}:
                raise ValueError("ColorScale vert/log require 0 or 1")
            style = replace(style, **{"vertical" if key == "vert" else "log_axis": bool(value)})
        elif key == "ticklen":
            if not np.isfinite(value) or not -100 <= value <= 50:
                raise ValueError("ColorScale tickLen must be -100..50")
            length = .7*style.font_size if value == -1 else -(value+50) if value < -50 else abs(value)
            axis = replace(axis, major_length=length, tick=2 if value < -50 else 1 if value < -1 else 0)
        elif key in properties:
            if key == "font":
                if not isinstance(value, str):
                    raise ValueError("ColorScale font must be text")
            elif not np.isfinite(value):
                raise ValueError("ColorScale axis value must be finite")
            if key == "nticks" and (int(value) != value or not 0 <= float(value) <= 1000):
                raise ValueError("ColorScale nticks must be 0..1000")
            if key == "minor" and value not in {0, 1}:
                raise ValueError("ColorScale minor must be 0 or 1")
            axis = replace(axis, **{properties[key]: value})
        else:
            raise ValueError(f"Unsupported ColorScale property: {key}")
    return replace(image, colorbar_style=style, colorbar_axis=axis)
