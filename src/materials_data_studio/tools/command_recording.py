"""Bounded GUI-to-Igor operation display, never executed or used as storage.

Log differences in canonical models AND display overrides. Unsupported MDS-only
settings are explicitly labelled, not disguised as valid Igor commands.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import fields, replace
from typing import Any

from materials_data_studio.tools.graph_document import EditableGraphDocument, GraphState
from materials_data_studio.tools.pxp_model import (
    PxpAnnotation,
    PxpAnnotationStyle,
    PxpAxisStyle,
    PxpDrawing,
    PxpGraph,
    PxpTrace,
)

TRACE = {"mode": "mode", "marker": "marker", "marker_size": "msize", "line_width": "lsize",
         "line_style": "lstyle", "color_igor": "rgb", "marker_fill_igor": "mrkFillRGB",
         "marker_stroke_igor": "mrkStrokeRGB", "marker_thickness": "mrkThick", "opaque": "opaque",
         "use_marker_stroke": "useMrkStrokeRGB", "offset": "offset", "multiplier": "muloffset"}
AXIS = {"tick": "tick", "mirror": "mirror", "thickness": "axThick", "major_length": "btLen",
        "minor_length": "stLen", "major_thickness": "btThick", "minor_thickness": "stThick",
        "medium_length": "ftLen", "medium_thickness": "ftThick", "minor_enabled": "minor",
        "manual_ticks": "manTick", "manual_minor": "manMinor", "log": "log", "grid": "grid",
        "font_family": "font", "font_size": "fSize", "font_style": "fStyle", "label_margin": "lblMargin",
        "label_rotation": "lblRot", "tick_rotation": "tkLblRot", "no_label": "noLabel",
        "tick_unit": "tickUnit", "trim_trailing_zero": "zapTZ", "reverse": "reverse",
        "axis_color": "axRGB", "label_color": "alblRGB", "tick_color": "tickRGB",
        "tick_label_color": "tlblRGB", "grid_color": "gridRGB", "tick_count": "nticks",
        "tick_separation": "sep", "user_ticks": "userticks", "axis_offset": "axOffset",
        "label_position": "lblPos", "label_position_mode": "lblPosMode", "label_lateral_position": "lblLatPos",
        "subminor_length": "ttLen", "subminor_thickness": "ttThick", "tick_zap": "tickZap",
        "grid_style": "gridStyle", "label_line_spacing": "lblLineSpacing",
        "tick_enable": "tickEnab"}
BRACES = {"manTick", "manMinor", "userticks", "axisEnab", "freePos", "offset", "muloffset", "tickEnab", "tickZap"}


def name(value: str) -> str:
    return ":".join(part if re.fullmatch(r"[A-Za-z_]\w*(?:#\d+)?", part) else "'" + part.replace("'", "\\'") + "'" for part in value.split(":"))


def literal(value: Any, key: str = "") -> str:
    if value is None:
        return "*"
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, str):
        return name(value) if key in {"userticks", "freePos"} else json.dumps(value, ensure_ascii=False)
    if isinstance(value, (tuple, list)):
        left, right = ("{", "}") if key in BRACES else ("(", ")")
        return left + ",".join(literal(v, key) for v in value) + right
    return str(value)


def effective_axis(graph: PxpGraph, axis: Any, layout: dict[str, Any]) -> PxpAxisStyle:
    settings = {f.name: getattr(axis.style, f.name) for f in fields(PxpAxisStyle) if getattr(axis.style, f.name) is not None}
    settings.update({k: v for k, v in layout.get("view", {}).get("axes", {}).get(axis.name, {}).items() if k in AXIS})
    return replace(graph.style.axis_defaults, **settings)


def effective_trace(trace: PxpTrace, layout: dict[str, Any]) -> dict[str, Any]:
    result = {field: getattr(trace, field) for field in TRACE}
    view = layout.get("view", {}).get("traces", {}).get(trace.name, {})
    for display, canonical in (("color", "color_igor"), ("marker_fill", "marker_fill_igor"), ("marker_edge", "marker_stroke_igor")):
        value = view.get(display)
        if value:
            if value == "none":
                result[canonical] = (0, 0, 0, 0)  # native RGBA transparency
            else:
                from matplotlib.colors import to_rgba
                rgba = to_rgba(value)
                result[canonical] = tuple(round(v * 65535) for v in rgba[:3 if rgba[3] == 1 else 4])
    result["visible"] = view.get("visible", True)
    return result


def append_trace(graph: PxpGraph, trace: PxpTrace) -> str:
    axis = next((a for a in graph.axes if a.name == trace.axis), None)
    flag = "R" if trace.axis == "right" or (axis and axis.side == "right") else "L"
    # Igor trace-name parameters are attached to the Y wave, not the operation.
    ref = name(trace.y_wave)
    if trace.name != trace.y_wave.rsplit(":", 1)[-1]:
        ref += "/TN=" + name(trace.name)
    x_axis = next((a for a in graph.axes if a.name == trace.x_axis), None)
    x_flag = "T" if trace.x_axis == "top" or (x_axis and x_axis.side == "top") else "B"
    x_flags = f"/{x_flag}={name(trace.x_axis)}" if trace.x_axis != "bottom" else ""
    return f"AppendToGraph/W={name(graph.name)}/{flag}={name(trace.axis)}{x_flags} {ref}" + (f" vs {name(trace.x_wave)}" if trace.x_wave else "")


def drawing_commands(graph: str, drawing: PxpDrawing) -> list[str]:
    window = "/W=" + name(graph)
    settings = dict(xcoord=name(drawing.xcoord), ycoord=name(drawing.ycoord),
                    arrow=drawing.arrow, arrowlen=drawing.arrow_length, arrowfat=drawing.arrow_fat,
                    linethick=drawing.line_width, linefgc=drawing.color, fillfgc=drawing.fill_color,
                    fillpat=drawing.fill_mode, dash=drawing.line_style, linecap=drawing.line_cap,
                    linejoin=drawing.line_join, font=drawing.font_family, fsize=drawing.font_size,
                    fstyle=drawing.font_style, textrgb=drawing.color, textrot=drawing.rotation,
                    textxjust={"L": 0, "M": 1, "R": 2}.get(drawing.anchor[0], 0),
                    textyjust={"B": 0, "C": 1, "T": 2}.get(drawing.anchor[-1], 0))
    options = ",".join(f"{k}=" + (str(v) if k in {"xcoord", "ycoord"} else literal(v)) for k, v in settings.items())
    commands = [f"SetDrawLayer{window} {drawing.layer}", f"SetDrawEnv{window} {options}"]
    if len(drawing.coordinates) > 4096:
        return [*commands, f"// MDS: {drawing.kind} has {len(drawing.coordinates)} coordinates; log abbreviated, retain .mdsg"]
    coordinates = ",".join(literal(v) for v in drawing.coordinates)
    if drawing.kind == "DrawArc":
        # MDS stores an elliptical bounding box, while native DrawArc takes a
        # centre and one radius. Emit a valid cubic path, not six invalid args.
        x0, y0, x1, y1 = drawing.coordinates[:4]
        rx, ry = abs(x1 - x0) / 2, abs(y1 - y0) / 2
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        start = math.radians(drawing.coordinates[4] if len(drawing.coordinates) > 4 else 0)
        stop = math.radians(drawing.coordinates[5] if len(drawing.coordinates) > 5 else 180)
        sweep = (stop - start) % (2 * math.pi) or 2 * math.pi
        count = max(1, math.ceil(sweep / (math.pi / 4)))
        path = [cx + rx * math.cos(start), cy + ry * math.sin(start)]
        for i in range(count):
            a, b = start + i * sweep / count, start + (i + 1) * sweep / count
            k = 4 / 3 * math.tan((b - a) / 4)
            path.extend((cx + rx * (math.cos(a) - k * math.sin(a)), cy + ry * (math.sin(a) + k * math.cos(a)),
                         cx + rx * (math.cos(b) + k * math.sin(b)), cy + ry * (math.sin(b) - k * math.cos(b)),
                         cx + rx * math.cos(b), cy + ry * math.sin(b)))
        commands.append(f"DrawBezier{window}/ABS 0,0,1,1,{{" + ",".join(literal(v) for v in path) + "}")
        commands.append("// MDS elliptical DrawArc represented by cubic Bezier segments (approximation)")
    elif drawing.kind in {"DrawPoly", "DrawBezier"}:
        commands.append(f"{drawing.kind}{window}/ABS 0,0,1,1,{{{coordinates}}}")
    else:
        commands.append(f"{drawing.kind}{window} {coordinates}" + ("," + literal(drawing.text) if drawing.kind == "DrawText" else ""))
    if drawing.kind == "DrawRRect":
        commands.append(f"// MDS-only relative corner radius: {drawing.round_radius}; Igor rounding is in points")
    return commands


def annotation_command(graph: str, annotation: PxpAnnotation, layout: dict[str, Any], kind: str = "TextBox") -> str:
    style = annotation.style
    settings = layout.get("annotations", {}).get(annotation.name, {})
    if kind == "Legend":
        settings = layout.get("annotations", {}).get("legend", settings)
    raw = annotation.raw
    if settings.get("font"):
        raw = "\\F'" + settings["font"].replace("'", "") + "'" + raw
    if not style.inherit_graph_font and style.font_size > 0:
        raw = f"\\Z{round(style.font_size):02d}" + raw
    native_x = style.x_percent * (-1 if style.offset_mode != "native" and style.anchor.startswith("R") else 1)
    native_y = style.y_percent * (-1 if style.offset_mode != "native" and style.anchor.endswith("T") else 1)
    flags = f"/W={name(graph)}/C/N={name(annotation.name)}/A={style.anchor}/F={style.frame}/B={int(style.transparent)}/X={native_x}/Y={native_y}"
    flags += f"/V={int(settings.get('visible', True))}/O={settings.get('rotation', 0)}/LS={style.line_spacing}"
    if style.color is not None:
        flags += "/G=" + literal(style.color)
    if annotation.trace and annotation.point is not None:
        return f"Tag{flags} {name(annotation.trace)},{annotation.point},{literal(raw)}"
    return f"{kind}{flags} {literal(raw)}"


def annotation_extras(style: PxpAnnotationStyle, previous: PxpAnnotationStyle, target: str) -> list[str]:
    encoded = {"anchor", "x_percent", "y_percent", "frame", "transparent", "color", "font_size", "line_spacing", "offset_mode"}
    extra = {f.name: getattr(style, f.name) for f in fields(PxpAnnotationStyle)
             if f.name not in encoded and getattr(style, f.name) != getattr(previous, f.name)}
    return ["// MDS-only annotation style (" + target + "): " + json.dumps(extra, ensure_ascii=False, sort_keys=True)] if extra else []


def graph_events(graph: PxpGraph, old: PxpGraph, layout: dict[str, Any], previous: dict[str, Any]) -> list[str]:
    events: list[str] = []
    window = "/W=" + name(graph.name)

    def setting(key: str, target: str, value: Any, before_value: Any) -> None:
        if value == before_value:
            return
        if value is None:
            if key in {"manTick", "manMinor", "userticks"}:
                value = 0
            else:
                events.append(f"// MDS: {key}({target}) reset to inherited/default value")
                return
        events.append(f"ModifyGraph{window} {key}" + (f"({name(target)})" if target else "") + "=" + literal(value, key))

    old_axes = {a.name: a for a in old.axes}
    for axis in graph.axes:
        prior = old_axes.get(axis.name)
        if prior is None and axis.name not in {"bottom", "top", "left", "right"}:
            events.append(f"NewFreeAxis{window}/{ {'right':'R', 'left':'L', 'top':'T', 'bottom':'B'}.get(axis.side,'L')} {name(axis.name)}")
        limits = layout.get("view", {}).get("axes", {}).get(axis.name, {}).get("limits", axis.limits)
        prior_limits = previous.get("view", {}).get("axes", {}).get(axis.name, {}).get("limits", prior.limits if prior else None)
        if limits != prior_limits:
            events.append(f"SetAxis{window} {name(axis.name)} " + ",".join(literal(v) for v in (limits or (None, None))))
        label = layout.get("view", {}).get("axes", {}).get(axis.name, {}).get("label", axis.label_raw)
        prior_label = previous.get("view", {}).get("axes", {}).get(axis.name, {}).get("label", prior.label_raw if prior else "")
        if label != prior_label:
            events.append(f"Label{window} {name(axis.name)} {literal(label)}")
        current_style = effective_axis(graph, axis, layout)
        old_style = effective_axis(old, prior, previous) if prior else PxpAxisStyle()
        for field, key in AXIS.items():
            setting(key, axis.name, getattr(current_style, field), getattr(old_style, field))
        for field in ("major_tick_color", "minor_tick_color"):
            value = getattr(current_style, field)
            if value != getattr(old_style, field):
                events.append(f"// MDS-only {field} ({name(axis.name)}): {literal(value)}; Igor tickRGB affects both tick kinds")
        setting("axisEnab", axis.name, axis.enable, prior.enable if prior else None)
        setting("freePos", axis.name, axis.free_position, prior.free_position if prior else None)
    for target in old_axes.keys() - {a.name for a in graph.axes}:
        events.append(f"KillFreeAxis{window} {name(target)}")
    for axis, position in layout.get("labels", {}).items():
        if position != previous.get("labels", {}).get(axis):
            setting("lblPos", axis, position.get("offset_points"), None)
            setting("lblPosMode", axis, 1, None)
            events.append(f"// MDS-only label lateral fraction ({axis})={position.get('lateral')}; not Igor lblLatPos points")
    for axis in previous.get("labels", {}).keys() - layout.get("labels", {}).keys():
        events.append(f"// MDS: removed explicit label position for {axis}; inherited placement restored")

    old_traces = {t.name: t for t in old.traces}
    for target in old_traces.keys() - {t.name for t in graph.traces}:
        events.append(f"RemoveFromGraph{window} {name(target)}")
    for trace in graph.traces:
        prior = old_traces.get(trace.name)
        replaced = prior is not None and (trace.y_wave, trace.x_wave, trace.axis, trace.x_axis) != (prior.y_wave, prior.x_wave, prior.axis, prior.x_axis)
        if replaced:
            events.append(f"RemoveFromGraph{window} {name(trace.name)}")
        if prior is None or replaced:
            events.append(append_trace(graph, trace))
        values = effective_trace(trace, layout)
        previous_values = effective_trace(prior, previous) if prior and not replaced else {}
        for field, key in TRACE.items():
            setting(key, trace.name, values[field], previous_values.get(field))
        setting("hideTrace", trace.name, not values["visible"], not previous_values.get("visible", True))
        if (trace.error_low and (prior is None or (trace.error_low, trace.error_high, trace.error_axis) != (prior.error_low, prior.error_high, prior.error_axis))) or (prior and prior.error_low and not trace.error_low):
            events.append(f"ErrorBars{window} {name(trace.name)} " + (f"{trace.error_axis},wave=({name(trace.error_low)},{name(trace.error_high or trace.error_low)})" if trace.error_low else "OFF"))
        if prior and (trace.line_cap, trace.line_join) != (prior.line_cap, prior.line_join):
            events.append(f"// MDS trace end cap/join ({trace.name})={trace.line_cap},{trace.line_join}")
    order = [t.name for t in graph.traces]
    if len(order) > 1 and order != [t.name for t in old.traces]:
        events.append(f"ReorderTraces{window} {name(order[-1])},{{" + ",".join(name(t) for t in order[:-1]) + "}")

    setting("swapXY", "", graph.swap_xy, old.swap_xy)
    for dimension in ("width", "height"):
        value, prior = getattr(graph.style, dimension + "_points"), getattr(old.style, dimension + "_points")
        setting(dimension, "", value if value is not None else 0, prior if prior is not None else 0)
        aspect, old_aspect = getattr(graph.style, dimension + "_aspect"), getattr(old.style, dimension + "_aspect")
        if aspect != old_aspect and aspect is not None:
            events.append(f"ModifyGraph{window} {dimension}={{Aspect,{aspect}}}")
    for side in dict.fromkeys([*dict(old.style.margins), *dict(graph.style.margins)]):
        setting("margin", side, dict(graph.style.margins).get(side, -1), dict(old.style.margins).get(side, -1))
    setting("gbRGB", "", graph.style.plot_background, old.style.plot_background)
    for field, key in (("font_family", "gFont"), ("font_size", "gfSize"),
                       ("relative_font_size", "gfRelSize"), ("font_multiplier", "gfMult"),
                       ("marker_size", "gmSize")):
        setting(key, "", getattr(graph.style, field), getattr(old.style, field))

    old_annotations = {a.name: a for a in old.annotations}
    for target in old_annotations.keys() - {a.name for a in graph.annotations}:
        events.append(f"TextBox{window}/K/N={name(target)}")
    for annotation in graph.annotations:
        settings = layout.get("annotations", {}).get(annotation.name, {})
        prior_settings = previous.get("annotations", {}).get(annotation.name, {})
        if annotation != old_annotations.get(annotation.name) or settings != prior_settings:
            events.append(annotation_command(graph.name, annotation, layout))
            prior_annotation = old_annotations.get(annotation.name)
            events.extend(annotation_extras(annotation.style, prior_annotation.style if prior_annotation else PxpAnnotationStyle(), annotation.name))
    legend_settings = layout.get("annotations", {}).get("legend", layout.get("annotations", {}).get("legend0", {}))
    prior_legend_settings = previous.get("annotations", {}).get("legend", previous.get("annotations", {}).get("legend0", {}))
    legend_visible = layout.get("view", {}).get("legend", legend_settings.get("visible", True))
    old_legend_visible = previous.get("view", {}).get("legend", prior_legend_settings.get("visible", True))
    if (graph.legend_raw, graph.legend_style, legend_settings, legend_visible) != (old.legend_raw, old.legend_style, prior_legend_settings, old_legend_visible):
        if graph.legend_raw and legend_visible:
            events.append(annotation_command(graph.name, PxpAnnotation("legend0", graph.legend_raw, graph.legend_style), layout, "Legend"))
        else:
            events.append(f"Legend{window}/K/N=legend0")
        events.extend(annotation_extras(graph.legend_style, old.legend_style, "legend0"))

    old_images = {i.wave: i for i in old.images}
    for ref in old_images.keys() - {i.wave for i in graph.images}:
        events.append(f"RemoveImage{window} {name(ref)}")
    for image in graph.images:
        prior = old_images.get(image.wave)
        image_name = name(image.wave.rsplit(":", 1)[-1])
        if prior is None:
            events.append(f"AppendImage{window} {name(image.wave)}")
        if prior is None or (image.color_table, image.color_range, image.reverse) != (prior.color_table, prior.color_range, prior.reverse):
            lo, hi = image.color_range or (None, None)
            events.append(f"ModifyImage{window} {image_name} ctab={{{literal(lo)},{literal(hi)},{name(image.color_table)},{int(image.reverse)}}}")
        for field, key in (("log_color", "log"), ("hidden", "hide"), ("interpolation", "interpolate"), ("min_color", "minRGB"), ("max_color", "maxRGB")):
            value = getattr(image, field)
            if (prior is None or value != getattr(prior, field)) and value is not None:
                rendered = "NaN" if value == "transparent" else "1" if value == "black" else literal(value)
                events.append(f"ModifyImage{window} {image_name} {key}={rendered}")
        colorbar = layout.get("colorbars", {}).get(image.wave, {})
        previous_colorbar = previous.get("colorbars", {}).get(image.wave, {})
        if prior is None or (image.colorbar_visible, image.colorbar_label_raw, image.colorbar_style, colorbar) != (prior.colorbar_visible, prior.colorbar_label_raw, prior.colorbar_style, previous_colorbar):
            identifier = "scale" + str(list(graph.images).index(image))
            if not image.colorbar_visible:
                events.append(f"ColorScale{window}/K/N={identifier}")
            else:
                style = image.colorbar_style
                options = f"image={image_name},width={style.width},height={style.height},heightPct={style.height_percent},vert={int(style.vertical)},log={int(style.log_axis)},fsize={style.font_size}"
                events.append(f"ColorScale{window}/C/N={identifier}/A={style.anchor}/X={style.x_percent}/Y={style.y_percent} {options}")
                events.append(f"AppendText{window}/N={identifier} {literal(image.colorbar_label_raw)}")
                if colorbar:
                    events.append("// MDS-only colorbar layout: " + json.dumps(colorbar, ensure_ascii=False, sort_keys=True))
    image_order = [i.wave for i in graph.images]
    if len(image_order) > 1 and image_order != [i.wave for i in old.images]:
        events.append(f"ReorderImages{window} {name(image_order[-1])},{{" + ",".join(name(ref) for ref in image_order[:-1]) + "}")
    if graph.drawings != old.drawings:
        if graph.drawings[:len(old.drawings)] == old.drawings:
            for drawing in graph.drawings[len(old.drawings):]:
                events.extend(drawing_commands(graph.name, drawing))
        else:
            # Model objects have no stable Igor draw-item IDs. Replace affected
            # layers explicitly so a move/edit/delete does not append duplicates.
            for layer in dict.fromkeys(d.layer for d in (*old.drawings, *graph.drawings)):
                if tuple(d for d in old.drawings if d.layer == layer) == tuple(d for d in graph.drawings if d.layer == layer):
                    continue
                events.append(f"DrawAction{window}/L={layer} delete")
                for drawing in graph.drawings:
                    if drawing.layer == layer:
                        events.extend(drawing_commands(graph.name, drawing))
    return events


def semantic_events(document: EditableGraphDocument, before: GraphState, after: GraphState, title: str) -> list[str]:
    events: list[str] = []
    # Drag commits must not scan/copy all scientific arrays or rebuild folders.
    unchanged_catalog = before.waves is after.waves and before.tables is after.tables
    source_before, source_after = (document.source, document.source) if unchanged_catalog else (replace(document, current=before).render_source(), replace(document, current=after).render_source())
    old_waves = {w.id: w for w in source_before.waves} if before.waves is not after.waves else {}
    for wave in (() if before.waves is after.waves else source_after.waves):
        previous = old_waves.get(wave.id)
        ref = name(wave.igor_path)
        if previous is None:
            events.append(f"Make/{'T' if wave.data.dtype.kind in 'US' else 'D'}/N=(" + ",".join(map(str, wave.shape)) + f") {ref}")
        elif previous.igor_path != wave.igor_path:
            events.append(f"Rename {name(previous.igor_path)},{ref}")
        if previous is None or wave.data is not previous.data:
            if previous is not None and wave.shape != previous.shape:
                events.append("Redimension/N=(" + ",".join(map(str, wave.shape)) + f") {ref}")
            events.append(f"// GUI wave values: {ref} ({wave.data.size} points); use Export Data/.mdsg to retain values")
        if previous is None or wave.dimension_scaling != previous.dimension_scaling or wave.dimension_units != previous.dimension_units:
            for i, (start, delta) in enumerate(wave.dimension_scaling[:2]):
                events.append(f"SetScale/P {'xy'[i]},{start},{delta},{literal(wave.dimension_units[i])},{ref}")
        if previous is None or wave.data_units != previous.data_units:
            events.append(f"SetScale d,0,1,{literal(wave.data_units)},{ref}")
    for wave in (() if before.waves is after.waves else source_before.waves):
        if wave.id not in {w.id for w in source_after.waves}:
            events.append(f"KillWaves {name(wave.igor_path)}")
    old_graphs = {g.name: g for g in before.graphs}
    new_graph_names = {g.name for g in after.graphs}
    renamed: set[str] = set()
    for graph in after.graphs:
        old = old_graphs.get(graph.name)
        layout, previous = after.layout.get(graph.name, {}), before.layout.get(graph.name, {})
        matches = [g for g in before.graphs if g.name not in new_graph_names and g.name not in renamed and replace(g, name=graph.name) == graph] if old is None else []
        if len(matches) == 1:
            original = matches[0]
            events.append(f"RenameWindow {name(original.name)},{name(graph.name)}")
            old = replace(original, name=graph.name)
            previous = before.layout.get(original.name, {})
            renamed.add(original.name)
        if old is None:
            events.append(f"Display/N={name(graph.name)}")
            old = PxpGraph(graph.name)
        if graph != old or layout != previous:
            events.extend(graph_events(graph, old, layout, previous))
    for graph in before.graphs:
        if graph.name not in new_graph_names and graph.name not in renamed:
            events.append(f"KillWindow {name(graph.name)}")
    old_tables = {t.name: t for t in source_before.table_definitions}
    for table in (() if before.tables is after.tables else source_after.table_definitions):
        previous_table = old_tables.get(table.name)
        if table != previous_table:
            if previous_table:
                events.append(f"KillWindow {name(table.name)}")
            events.append(f"Edit/N={name(table.name)} " + ",".join(name(c) for c in table.columns))
            for ref, width in table.widths:
                events.append(f"ModifyTable/W={name(table.name)} width({name(ref)})={width}")
    for table in (() if before.tables is after.tables else source_before.table_definitions):
        if table.name not in {t.name for t in source_after.table_definitions}:
            events.append(f"KillWindow {name(table.name)}")
    if before.environment != after.environment and title != "Edit Python procedure":
        events.append("// MDS working environment changed; retained in .mdsg")
    if not events:
        events.append("// MDS-only document edit; no exact Igor command")
    header = "// GUI " + title.replace("\r", " ").replace("\n", " ")[:200]
    bounded = [header]
    size = len(header)
    for event in events:
        size += len(event)
        if len(bounded) >= 512 or size > 256 * 1024:
            bounded.append("// GUI log abbreviated; complete state is in .mdsg, not the log")
            break
        bounded.append(event)
    return bounded


def verified_replay(document: EditableGraphDocument, before: GraphState, after: GraphState,
                    events: list[str]) -> str | None:
    """Offer a replay script only after executing it against an isolated state.

    Scientific array changes require the saved document; descriptive logs never
    become executable merely because their operation names look familiar.
    """
    if before.waves is not after.waves or before.tables is not after.tables or before.environment != after.environment:
        return None
    if any(line.startswith("//") and not line.startswith("// GUI ") for line in events):
        return None
    script = "\n".join(line for line in events if not line.startswith("//"))
    if not script or len(script) > 65536:
        return None
    from dataclasses import replace

    from materials_data_studio.tools.command_engine import CommandEngine
    from materials_data_studio.tools.command_services import DocumentServices
    candidate = replace(document, current=before.clone())
    result = CommandEngine(DocumentServices(candidate)).execute(script)
    def populated(value):
        return {key: cleaned for key, item in value.items() if (cleaned := populated(item) if isinstance(item, dict) else item) not in ({}, None)}
    if result.success and candidate.current.graphs == after.graphs and populated(candidate.current.layout) == populated(after.layout):
        return script
    return None
