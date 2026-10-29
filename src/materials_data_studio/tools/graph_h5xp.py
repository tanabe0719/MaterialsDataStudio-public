"""Bounded read-only H5XP imports and atomic, generated-only experiment saves.

No imported procedure is executed or included in exported startup commands.
The extra MDS metadata restores editing-only layout on an MDS round trip.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tempfile
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

import h5py
import numpy as np

from materials_data_studio.plotting.igor_h5xp import (
    _IGOR_NUMERIC_TYPES,
    escape_igor_string,
    sanitize_igor_name,
    unique_igor_name,
)
from materials_data_studio.tools.graph_document import EditableGraphDocument
from materials_data_studio.tools.pxp_model import PxpDocument, PxpIssue, PxpWave
from materials_data_studio.tools.pxp_recreation import parse_recreation
from materials_data_studio.tools.pxp_snapshot import decode_document, encode_document
from materials_data_studio.tools.pxp_tables import parse_tables

MAX_BYTES = 512 * 1024 * 1024
MAX_TEXT = 32 * 1024 * 1024


def text(value: Any) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


def wave_metadata_hash(wave: Any) -> str:
    """Bind MDS editing metadata to native wave attributes as well as arrays."""
    labels = []
    for dim in range(wave.data.ndim):
        values = list(wave.dimension_labels[dim] if dim < len(wave.dimension_labels) else ())
        while values and not values[-1]:
            values.pop()
        labels.append(values)
    value = dict(note=wave.wave_note, labels=labels,
                 names=[wave.dimension_names[d] if d < len(wave.dimension_names) else "" for d in range(wave.data.ndim)],
                 scaling=wave.dimension_scaling, units=wave.dimension_units, data_units=wave.data_units)
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def read_dataset(item: h5py.Dataset, maximum: int) -> Any:
    if item.size is None or item.size * max(item.dtype.itemsize, 8) > maximum or item.external or item.is_virtual:
        raise ValueError("H5XP dataset exceeds the safety limit or uses external storage")
    if item.compression not in {None, "gzip", "lzf", "szip"}:
        raise ValueError("Unsupported HDF5 compression filter")
    value = item[()]
    size = (
        sum(len(text(v).encode("utf-8")) for v in np.asarray(value).ravel())
        if np.asarray(value).dtype.kind in "OSU"
        else np.asarray(value).nbytes
    )
    if size > maximum:
        raise ValueError("H5XP dataset exceeds the safety limit")
    return value


def load_h5xp(path: Path) -> EditableGraphDocument:
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("H5XP exceeds the 512 MiB safety limit")
    source = path.read_bytes()
    waves: list[PxpWave] = []
    folders = ["root"]
    issues: list[PxpIssue] = []
    recreation = ""
    metadata: dict[str, Any] | None = None
    remaining = MAX_BYTES
    seen: set[int] = set()
    with h5py.File(io.BytesIO(source), "r") as handle:

        def safe(group: h5py.Group, name: str) -> Any:
            if not isinstance(group.get(name, getlink=True), h5py.HardLink):
                raise ValueError("H5XP external/soft links are not supported")
            return group[name]

        packed = safe(handle, "Packed Data")
        if not isinstance(packed, h5py.Group):
            raise ValueError("H5XP Packed Data is missing")

        def walk(group: h5py.Group, prefix: str, depth: int = 0) -> None:
            nonlocal remaining
            if depth > 32 or len(seen) > 10000:
                raise ValueError("H5XP hierarchy exceeds the safety limit")
            address = hash(group.id)
            if address in seen:
                raise ValueError("H5XP must have a strict hierarchy")
            seen.add(address)
            for name in group:
                name = str(name)
                item = safe(group, name)
                ref = prefix + ":" + name
                if isinstance(item, h5py.Group):
                    folders.append(ref)
                    walk(item, ref, depth + 1)
                elif "IGORWaveType" in item.attrs:
                    code = int(item.attrs["IGORWaveType"])
                    if code in {256, 16384}:
                        issues.append(PxpIssue(ref, "Reference waves are not imported"))
                        continue
                    if item.ndim not in {1, 2, 3, 4}:
                        issues.append(PxpIssue(ref, "Unsupported wave dimensions"))
                        continue
                    data = np.asarray(read_dataset(item, remaining))
                    if data.dtype.kind in "OS":
                        strings = [text(value) for value in data.ravel()]
                        width = max((len(value) for value in strings), default=1)
                        if len(strings) * max(1, width) * 4 > remaining:
                            raise ValueError("Expanded text wave exceeds the memory safety limit")
                        data = np.asarray(strings, dtype=str).reshape(data.shape)
                    if code & 1:
                        if data.shape[0] % 2:
                            raise ValueError("Invalid complex wave rows")
                        data = data[::2] + 1j * data[1::2]
                    remaining -= data.nbytes
                    data.setflags(write=False)
                    scale = np.asarray(
                        item.attrs.get("IGORWaveScaling", [(0., 0.), *[(1., 0.)] * data.ndim])
                    )
                    if scale.shape != (data.ndim + 1, 2):
                        raise ValueError("Invalid IGORWaveScaling dimensions")
                    scales = tuple((float(scale[i + 1, 1]), float(scale[i + 1, 0])) for i in range(data.ndim))
                    units = list(
                        np.asarray(item.attrs.get("IGORWaveUnits", [""] * (data.ndim + 1))).ravel()
                    )
                    dim_units = tuple(
                        text(units[i + 1]) if len(units) > i + 1 else "" for i in range(data.ndim)
                    )
                    label_data = np.asarray(item.attrs.get("IGORWaveDimensionLabels", np.empty((0, data.ndim))))
                    if label_data.ndim != 2 or label_data.shape[1] != data.ndim:
                        raise ValueError("Invalid IGORWaveDimensionLabels dimensions")
                    dimension_names = tuple(text(label_data[0, d]) if len(label_data) else "" for d in range(data.ndim))
                    dimension_labels = []
                    for d in range(data.ndim):
                        values = [text(v) for v in label_data[1:1 + data.shape[d], d]]
                        while values and not values[-1]:
                            values.pop()
                        dimension_labels.append(tuple(values))
                    waves.append(
                        PxpWave(
                            ref,
                            ref,
                            name,
                            data.dtype.str,
                            data.shape,
                            data,
                            scales,
                            dim_units,
                            text(units[0]) if units else "",
                            text(item.attrs.get("IGORWaveNote", "")),
                            tuple(dimension_labels), dimension_names,
                        )
                    )

        walk(packed, "root")
        if "Recreation" in handle:
            group = safe(handle, "Recreation")
            if "Recreation Procedures" in group:
                recreation = text(
                    read_dataset(safe(group, "Recreation Procedures"), MAX_TEXT)
                ).replace("\r", "\n")
        if "MDS Graph Document" in handle:
            group = safe(handle, "MDS Graph Document")
            if "State" in group:
                metadata = json.loads(text(read_dataset(safe(group, "State"), MAX_TEXT)))
    graphs, tables = parse_recreation(recreation)
    # Igor color-table waves are actual RGB(A) samples, not named presets.
    # Register immutable, content-addressed palettes for faithful native imports.
    from matplotlib import colormaps

    from materials_data_studio.tools.pxp_style import COLORMAPS, sampled_colormap

    for index, graph in enumerate(graphs):
        images = []
        for image in graph.images:
            table = next((w for w in waves if w.igor_path == image.color_table), None)
            if table is not None and table.data.ndim == 2 and table.shape[1] in {3, 4}:
                if table.data.dtype.kind not in "fiu" or not np.isfinite(table.data).all():
                    raise ValueError("Invalid image color-table wave")
                name = sampled_colormap(table.data)
                image = replace(image, color_table=name, lut_name=name,
                                color_lut=tuple(tuple(int(c) for c in row) for row in table.data),
                                lut_provenance="RGB16 wave from H5XP; exact samples")
            elif image.color_table not in COLORMAPS and image.color_table not in colormaps:
                issues.append(PxpIssue("ModifyImage", f"Unknown color table: {image.color_table}"))
            images.append(image)
        graphs = (*graphs[:index], replace(graph, images=tuple(images)), *graphs[index + 1:])
    document = PxpDocument(
        str(uuid.uuid4()),
        path.name,
        hashlib.sha256(source).hexdigest(),
        len(source),
        "9+",
        "h5py",
        h5py.__version__,
        tuple(waves),
        tuple(folders),
        graphs,
        tables,
        (),
        tuple(issues),
        "GRAPH_PARTIAL" if graphs else "DATA_ONLY" if waves else "OPEN_FAILED",
        source,
        recreation,
        parse_tables(recreation),
    )
    if (
        metadata
        and metadata.get("version") == 1
        and metadata.get("recreation_sha256") == hashlib.sha256(recreation.encode()).hexdigest()
    ):
        imported = metadata["document"]
        arrays = {}
        for i, record in enumerate(imported["waves"]):
            wave = document.wave(metadata["wave_paths"][i])
            if (
                wave is None
                or list(wave.shape) != record["shape"]
                or hashlib.sha256(wave.data.astype(record["dtype"]).tobytes()).hexdigest()
                != metadata["wave_hashes"][i]
                or ("wave_metadata_hashes" in metadata and wave_metadata_hash(wave) != metadata["wave_metadata_hashes"][i])
            ):
                break  # Igor edits invalidate the MDS-only snapshot.
            arrays[f"wave_{i}"] = wave.data.astype(record["dtype"])
        else:
            payload = io.BytesIO()
            np.savez(payload, **arrays)
            restored = decode_document(imported, payload.getvalue())
            result = EditableGraphDocument.from_pxp(
                replace(
                    restored,
                    basename=path.name,
                    source_bytes=source,
                    sha256=document.sha256,
                    size=len(source),
                )
            )
            result.current.layout = metadata.get("layout", {})
            return result
    return EditableGraphDocument.from_pxp(document)


def literal(value: str) -> str:
    return '"' + escape_igor_string(value) + '"'


def procedures(
    document: EditableGraphDocument,
    references: dict[str, str],
    palettes: dict[str, str] | None = None,
) -> str:
    """Only generate known operations from canonical state, never reuse code."""
    lines: list[str] = []

    def wave(ref: str) -> str:
        suffix = ref[-2:] if ref.endswith((".i", ".l", ".d")) else ""
        found = document.render_source().wave(ref[:-2] if suffix else ref)
        if found is None:
            raise ValueError(f"Missing wave: {ref}")
        return references[found.id] + suffix

    def rgb(value: Any) -> str:
        return "(" + ",".join(f"{v:.17g}" for v in value) + ")"

    used: set[str] = set()
    for graph in document.current.graphs:
        name = unique_igor_name(graph.name, used, fallback="Graph")
        if graph.window_kind == "Layout":
            lines += [f"{name}()", f"Window {name}() : Layout", "PauseUpdate; Silent 1", "NewLayout"]
            for item in graph.layout_items:
                lines.append("AppendLayoutObject/R=" + rgb(item.rect) + " graph " + sanitize_igor_name(item.graph))
            lines.append("EndMacro")
            continue
        lines += [f"{name}()", f"Window {name}() : Graph", "PauseUpdate; Silent 1"]
        style = graph.style
        width, height = style.window_size or (640, 480)
        started = False
        layout = document.current.layout.get(graph.name, {})
        axis_models = {axis.name: axis for axis in graph.axes}
        trace_instances: dict[str, int] = {}
        for trace in graph.traces:
            settings = layout.get("view", {}).get("traces", {}).get(trace.name, {})
            from matplotlib.colors import to_rgb

            for key, canonical in (
                ("color", "color_igor"),
                ("marker_fill", "marker_fill_igor"),
                ("marker_edge", "marker_stroke_igor"),
            ):
                if settings.get(key) and settings[key] != "none":
                    trace = replace(
                        trace, **{canonical: tuple(round(v * 65535) for v in to_rgb(settings[key]))}
                    )
            command = (
                "AppendToGraph" if started else f"Display/W=(80,60,{80 + width:g},{60 + height:g})"
            )
            flags = (
                ""
                if trace.axis == "left"
                else "/R"
                if trace.axis == "right"
                else ("/R=" if axis_models.get(trace.axis) and axis_models[trace.axis].side == "right" else "/L=") + sanitize_igor_name(trace.axis)
            )
            if trace.x_axis != "bottom":
                flags += "/T" if trace.x_axis == "top" else (
                    "/T=" if axis_models.get(trace.x_axis) and axis_models[trace.x_axis].side == "top" else "/B="
                ) + sanitize_igor_name(trace.x_axis)
            distribution = trace.box or trace.violin
            if distribution:
                if not started:
                    lines.append(f"Display/W=(80,60,{80 + width:g},{60 + height:g})")
                command = "AppendBoxPlot" if trace.box else "AppendViolinPlot"
                flags += ("/VERT" if distribution.vertical else "") + ("/CATL" if distribution.category_labels else "")
            lines.append(f"{command}{flags} " + (",".join(wave(ref) for ref in distribution.waves) if distribution else wave(trace.y_wave) + (f"[{trace.point_range[0]},{trace.point_range[1]}]" if trace.point_range else ""))
                         + (" vs " + wave(trace.x_wave) if trace.x_wave else ""))
            started = True
            y_name = wave(trace.y_wave).rsplit(":", 1)[1]
            instance = trace_instances.get(y_name, 0)
            trace_instances[y_name] = instance + 1
            if instance:
                y_name += f"#{instance}"
            values = {
                "mode": trace.mode,
                "marker": trace.marker,
                "msize": trace.marker_size,
                "lSize": trace.line_width,
                "lStyle": trace.line_style,
                "opaque": int(trace.opaque),
                "useMrkStrokeRGB": int(trace.use_marker_stroke),
                "mrkThick": trace.marker_thickness,
                "offset": trace.offset,
                "muloffset": trace.multiplier,
                "rgb": trace.color_igor,
                "mrkFillRGB": trace.marker_fill_igor,
                "mrkStrokeRGB": trace.marker_stroke_igor,
                "hideTrace": int(not settings.get("visible", True)),
            }
            parts = [
                f"{key}({y_name})=" + (
                    rgb(v).replace("(", "{").replace(")", "}")
                    if key in {"offset", "muloffset"} else rgb(v) if isinstance(v, tuple) else str(v)
                )
                for key, v in values.items()
                if v is not None
            ]
            lines.append("ModifyGraph " + ",".join(parts))
            if trace.box:
                b = trace.box
                properties = [f"trace={y_name}", f"notched={int(b.notched)}", f"quartileMethod={b.quartile_method}",
                              "whiskerMethod={" + ",".join(map(str, b.whisker_method)) + "}",
                              "outlierMethod={" + ",".join(map(str, b.outlier_method)) + "}",
                              f"showData={b.show_data}", f"showMean={int(b.show_mean)}", f"showFences={int(b.show_fences)}",
                              f"medianIsMarker={int(b.median_is_marker)}", f"jitter={b.jitter:g}", f"capWidth={b.cap_width:g}",
                              "boxColor=" + rgb(b.box_color), "medianLineColor=" + rgb(b.median_color), "whiskerColor=" + rgb(b.whisker_color)]
                if b.box_fill:
                    properties.append("boxFill=" + rgb(b.box_fill))
                if b.box_width is not None:
                    properties.append(f"boxWidth={b.box_width:g}")
                lines.append("ModifyBoxPlot " + ",".join(properties))
            if trace.violin:
                v = trace.violin
                properties = [f"trace={y_name}", f"bandwidth={v.bandwidth:g}", f"bandwidthMethod={v.bandwidth_method}",
                    f"kernel={v.kernel}", f"curveExtension={v.extension:g}", f"maxDensity={v.max_density:g}",
                    f"plotSide={v.side}", f"showData={int(v.show_data)}", f"showMean={int(v.show_mean)}",
                    f"showMedian={int(v.show_median)}", f"closeOutline={int(v.close_outline)}", f"jitter={v.jitter:g}",
                    "lineColor=" + rgb(v.line_color)]
                if v.fill_color:
                    properties.append("fillColor=" + rgb(v.fill_color))
                if v.box_width is not None:
                    properties.append(f"boxWidth={v.box_width:g}")
                lines.append("ModifyViolinPlot " + ",".join(properties))
            def bounds(value: float | None) -> str:
                return "*" if value is None else f"{value:.17g}"
            if trace.z_color:
                ref, low, high, table, reverse, color_wave = trace.z_color
                values = [wave(ref), bounds(low), bounds(high), table, str(reverse)]
                if color_wave:
                    values.append(wave(color_wave))
                lines.append(f"ModifyGraph zColor({y_name})={{" + ",".join(values) + f"}},logZColor({y_name})={int(trace.log_z_color)}")
                if trace.color_scale:
                    lines.append(f"ColorScale/C trace={y_name}")
            if trace.z_size:
                ref, low, high, lower_size, upper_size, axis = trace.z_size
                values = [wave(ref), bounds(low), bounds(high), str(lower_size), str(upper_size)]
                if axis:
                    values.append(sanitize_igor_name(axis))
                lines.append(f"ModifyGraph zmrkSize({y_name})={{" + ",".join(values) + "}")
            if trace.z_marker:
                lines.append(f"ModifyGraph zmrkNum({y_name})={{{wave(trace.z_marker)}}}")
            error_flags = f"/L={trace.error_width:g}/T={trace.error_cap_thickness:g}/Y={trace.error_cap:g}/CLIP={int(trace.error_clip)}"
            if trace.error_color:
                error_flags += "/RGB=" + rgb(trace.error_color)
            if trace.x_error_low and trace.error_low:
                lines.append(f"ErrorBars{error_flags} {y_name} XY,wave=({wave(trace.x_error_low)},{wave(trace.x_error_high or trace.x_error_low)}),wave=({wave(trace.error_low)},{wave(trace.error_high or trace.error_low)})")
            elif trace.x_error_low:
                lines.append(f"ErrorBars{error_flags} {y_name} X,wave=({wave(trace.x_error_low)},{wave(trace.x_error_high or trace.x_error_low)})")
            elif trace.error_low:
                lines.append(f"ErrorBars{error_flags} {y_name} {trace.error_axis},wave=({wave(trace.error_low)},{wave(trace.error_high or trace.error_low)})")
        for cursor in graph.cursors:
            lines.append(f"Cursor/P/C={rgb(cursor.color)} {cursor.name},{sanitize_igor_name(cursor.trace)},{cursor.point}")
        for image in graph.images:
            if not started:
                lines.append(f"Display/W=(80,60,{80 + width:g},{60 + height:g})")
                started = True
            image_ref = wave(image.wave)
            image_name = image_ref.rsplit(":", 1)[1]
            lines.append("AppendImage " + image_ref + (" vs {" + (wave(image.x_wave) if image.x_wave else "*") + "," + (wave(image.y_wave) if image.y_wave else "*") + "}" if image.x_wave or image.y_wave else ""))
            low, high = image.color_range or (None, None)
            from materials_data_studio.tools.pxp_style import COLORMAPS

            native_table = (palettes or {}).get(graph.name + ":" + image.wave) or (
                image.color_table
                if image.color_table in COLORMAPS
                else next(
                    (name for name, mapped in COLORMAPS.items() if mapped == image.color_table),
                    (palettes or {}).get(image.color_table),
                )
            )
            if native_table is None:
                raise ValueError(f"Unsupported image color table: {image.color_table}")
            lines.append(
                f"ModifyImage {image_name} ctab={{{'*' if low is None else low},{'*' if high is None else high},{native_table},{int(image.reverse)}}},log={int(image.log_color)}"
            )
            if image.colorbar_visible:
                cs = image.colorbar_style
                ca = image.colorbar_axis
                options = f"image={image_name},width={cs.width:g},height={cs.height:g},heightPct={cs.height_percent:g},vert={int(cs.vertical)},log={int(cs.log_axis)}"
                if not cs.inherit_graph_font:
                    options += f",fsize={cs.font_size:g}"
                for key, value in (("nticks", ca.tick_count), ("minor", ca.minor_enabled), ("tickThick", ca.major_thickness), ("lblMargin", ca.label_margin), ("lblRot", ca.label_rotation), ("tkLblRot", ca.tick_rotation)):
                    if value is not None:
                        options += f",{key}={int(value) if isinstance(value, bool) else value}"
                if ca.major_length is not None:
                    length = -ca.major_length-50 if ca.tick == 2 else -ca.major_length if ca.tick == 1 else ca.major_length
                    options += f",tickLen={length:g}"
                lines.append(f"ColorScale/C/N=scale0/A={cs.anchor}/X={cs.x_percent:g}/Y={cs.y_percent:g} " + options)
                lines.append(f"AppendText {literal(image.colorbar_label_raw)}")
        if not started:
            lines.append(f"Display/W=(80,60,{80 + width:g},{60 + height:g})")
        for contour in graph.contours:
            contour_ref = wave(contour.wave)
            contour_name = contour_ref.rsplit(":", 1)[-1]
            lines.append("AppendMatrixContour " + contour_ref)
            if contour.levels:
                lines.append(f"ModifyContour {contour_name},autoLevels={{0,0,0}},moreLevels={{" + ",".join(map(str, contour.levels)) + "}")
            lines.append(f"ModifyContour {contour_name},fill={int(contour.filled)},ctabLines={{*,*,Viridis,0}},ctabFill={{*,*,Viridis,0}}")
        if graph.swap_xy:
            lines.append("ModifyGraph swapXY=1")
        if graph.style.font_size is not None:
            lines.append(f"ModifyGraph gfSize={graph.style.font_size:g}")
        if graph.style.font_family:
            lines.append("ModifyGraph gFont=" + literal(graph.style.font_family))
        if graph.style.relative_font_size is not None:
            lines.append(f"ModifyGraph gfRelSize={graph.style.relative_font_size:g}")
        lines.append(f"ModifyGraph gfMult={graph.style.font_multiplier:g}")
        if graph.style.marker_size is not None:
            lines.append(f"ModifyGraph gmSize={graph.style.marker_size:g}")
        for axis in graph.axes:
            axis_name = sanitize_igor_name(axis.name)
            lines.append(f"Label {axis_name} {literal(axis.label_raw)}")
            from materials_data_studio.tools.command_recording import effective_axis

            a = effective_axis(graph, axis, layout)
            from materials_data_studio.tools.graph_geometry import native_lateral
            fields = {
                "tick": a.tick,
                "mirror": a.mirror,
                "axThick": a.thickness,
                "tlblRGB": a.tick_label_color,
                "tickRGB": a.tick_color,
                "axRGB": a.axis_color,
                "alblRGB": a.label_color,
                "btLen": a.major_length,
                "stLen": a.minor_length,
                "btThick": a.major_thickness,
                "stThick": a.minor_thickness,
                "ttLen": a.subminor_length,
                "ttThick": a.subminor_thickness,
                "tickZap": a.tick_zap,
                "dateInfo": a.date_info,
                "gridStyle": a.grid_style,
                "lblLineSpacing": a.label_line_spacing,
                "minor": None if a.minor_enabled is None else int(a.minor_enabled),
                "log": a.log,
                "grid": a.grid,
                "fSize": a.font_size,
                "fStyle": a.font_style,
                "font": a.font_family,
                "axOffset": a.axis_offset,
                "lblPos": a.label_position,
                "lblPosMode": a.label_position_mode,
                "lblLatPos": native_lateral(graph, axis, a),
                "lblMargin": a.label_margin,
                "lblRot": a.label_rotation,
                "tkLblRot": a.tick_rotation,
                "tickUnit": a.tick_unit,
                "noLabel": a.no_label,
                "manTick": a.manual_ticks,
                "manMinor": a.manual_minor,
                "standoff": None if a.standoff is None else int(a.standoff),
                "zero": a.zero_line,
                "gridHair": a.grid_hair,
                "lowTrip": a.low_trip,
                "highTrip": a.high_trip,
                "notation": a.notation,
                "ZisZ": None if a.zero_is_zero is None else int(a.zero_is_zero),
                "tickEnab": a.tick_enable,
            }
            for key, value in fields.items():
                if value is not None:
                    rendered = (
                        literal(value)
                        if isinstance(value, str)
                        else rgb(value)
                        if isinstance(value, tuple)
                        else str(value)
                    )
                    if key in {"manTick", "manMinor", "tickEnab", "tickZap"}:
                        rendered = rendered.replace("(", "{").replace(")", "}")
                    lines.append(f"ModifyGraph {key}({axis_name})={rendered}")
            if a.user_ticks:
                lines.append(
                    f"ModifyGraph userticks({axis_name})={{{wave(a.user_ticks[0])},{wave(a.user_ticks[1])}}}"
                )
            if axis.enable is not None:
                lines.append(f"ModifyGraph axisEnab({axis_name})={{{axis.enable[0]:.17g},{axis.enable[1]:.17g}}}")
            if axis.free_position is not None:
                position = axis.free_position
                rendered = (
                    f"{{{position[0]:.17g},{sanitize_igor_name(position[1])}}}"
                    if isinstance(position, tuple) else f"{position:.17g}"
                )
                lines.append(f"ModifyGraph freePos({axis_name})={rendered}")
            if axis.limits:
                limits = ",".join("*" if value is None else f"{value:.17g}" for value in axis.limits)
                lines.append(f"SetAxis {axis_name} {limits}")
        for key, value in (("width", style.width_points), ("height", style.height_points)):
            if value:
                lines.append(f"ModifyGraph {key}={value:g}")
        for side, value in style.margins:
            lines.append(f"ModifyGraph margin({sanitize_igor_name(side)})={value:g}")
        if style.plot_background:
            lines.append("ModifyGraph gbRGB=" + rgb(style.plot_background))
        for annotation in graph.annotations:
            annotation_settings = layout.get("annotations", {}).get(annotation.name, {})
            s = annotation.style
            raw = annotation.raw
            if annotation_settings.get("font"):
                raw = "\\F'" + annotation_settings["font"].replace("'", "") + "'" + raw
            for w in document.render_source().waves:
                raw = raw.replace(
                    "\\s(" + w.name + ")", "\\s(" + references[w.id].rsplit(":", 1)[1] + ")"
                )
            anchor = (
                s.anchor
                if s.anchor in {"LT", "MT", "RT", "LC", "MC", "RC", "LB", "MB", "RB"}
                else "MC"
            )
            if not s.inherit_graph_font and s.font_size > 0:
                raw = f"\\Z{int(s.font_size):02d}" + raw
            color_flag = "/G=" + rgb(s.color) if s.color else ""
            native_x = s.x_percent * (-1 if s.offset_mode != "native" and anchor.startswith("R") else 1)
            native_y = s.y_percent * (-1 if s.offset_mode != "native" and anchor.endswith("T") else 1)
            flags = f"/C/N={unique_igor_name(annotation.name, set(), fallback='text')}/V={int(annotation_settings.get('visible', True))}/A={anchor}/F={s.frame}/B={int(s.transparent)}/O={float(annotation_settings.get('rotation', s.rotation)):g}/LS={s.line_spacing:g}/X={native_x:g}/Y={native_y:g}" + color_flag
            halo = s.padding if s.padding_explicit else -1
            flags += f"/S={int(s.frame_style)}/D={{{s.frame_width / 0.5:g},{s.shadow:g},{halo:g}}}/H={s.symbol_width:g}/E={int(s.exterior)}"
            if not s.transparent:
                flags += "/B=" + (str(s.background_mode) if s.background_mode in {2, 3} else rgb(s.background_color))
            command = "Legend" if annotation.raw == graph.legend_raw and graph.legend_raw else "TextBox"
            if annotation.trace and annotation.point is not None:
                tagged = next(t for t in graph.traces if t.name == annotation.trace)
                source_wave = document.render_source().wave(tagged.y_wave)
                attach = float(source_wave.coordinate(0)[annotation.point]) if source_wave is not None else float(annotation.point)
                flags = f"/N={sanitize_igor_name(annotation.name)}/A={anchor}/F={s.frame}/X={s.x_percent:g}/Y={s.y_percent:g}/L={annotation.arrow}/B={int(s.transparent)}"
                lines.append("Tag" + flags + f" {sanitize_igor_name(annotation.trace)},{attach:.17g}," + literal(raw))
            else:
                lines.append(command + flags + " " + literal(raw))
        if graph.legend_raw and not graph.annotations:
            lines.append("Legend/C/N=legend0 " + literal(graph.legend_raw))
        for drawing in graph.drawings:
            from materials_data_studio.tools.graph_drawing import KINDS, LAYERS

            if drawing.kind not in KINDS.values() or drawing.layer not in LAYERS:
                raise ValueError("Unsupported drawing kind / layer")
            lines.append("SetDrawLayer " + drawing.layer)
            options = dict(
                xcoord=sanitize_igor_name(drawing.xcoord),
                ycoord=sanitize_igor_name(drawing.ycoord),
                arrow=drawing.arrow,
                arrowlen=drawing.arrow_length,
                arrowfat=drawing.arrow_fat,
                linethick=drawing.line_width,
                linefgc=rgb(drawing.color),
                fillfgc=rgb(drawing.fill_color),
                fillpat=drawing.fill_mode,
                dash=drawing.line_style,
                fname=literal(drawing.font_family),
                fsize=drawing.font_size,
                fstyle=drawing.font_style,
                textrgb=rgb(drawing.color),
                textrot=drawing.rotation,
            )
            lines.append(
                "SetDrawEnv " + ",".join(f"{key}={value}" for key, value in options.items())
            )
            coordinates = ",".join(f"{v:.17g}" for v in drawing.coordinates)
            if drawing.kind in {"DrawPoly", "DrawBezier"}:
                # Igor uses a path form; consult its native DrawBezier grammar.
                lines.append(f"{drawing.kind}/ABS 0,0,1,1,{{{coordinates}}}")
            else:
                lines.append(
                    drawing.kind
                    + " "
                    + coordinates
                    + ("," + literal(drawing.text) if drawing.kind == "DrawText" else "")
                )
        if graph.host and graph.host_rect:
            # A child reconstruction references its already-created host.
            begin = next(i for i in range(len(lines)-1, -1, -1) if lines[i].startswith("Window "))
            for i in range(begin+1, len(lines)):
                if lines[i].startswith("Display"):
                    lines[i] = re.sub(r"^Display(?:/W=\([^)]*\))?", "Display/HOST=" + sanitize_igor_name(graph.host) + "/N=" + sanitize_igor_name(graph.name) + "/W=" + rgb(graph.host_rect), lines[i])
                    break
        lines.append("EndMacro")
    for table in document.render_source().table_definitions:
        name = unique_igor_name(table.name, used, fallback="Table")
        lines += [
            f"{name}()",
            f"Window {name}() : Table",
            "PauseUpdate; Silent 1",
            "Edit " + ",".join(wave(c) for c in table.columns),
        ]
        for column, width in table.widths:
            reference = document.render_source().wave(column)
            if reference is not None:
                lines.append(f"ModifyTable width({references[reference.id]})={width:g}")
            elif column == "Point":
                lines.append(f"ModifyTable width(Point)={width:g}")
        for column, key, value in table.column_styles:
            target = "" if column == "*" else "(" + ("Point" if column == "Point" else wave(column)) + ")"
            parsed = json.loads(value)
            if key == "showunits":
                source_wave = document.render_source().wave(column)
                if source_wave is not None and parsed:
                    lines.append("ModifyTable title" + target + "=" + literal(source_wave.name + (" (" + source_wave.data_units + ")" if source_wave.data_units else "")))
                continue
            lines.append("ModifyTable " + key + target + "=" + (literal(parsed) if isinstance(parsed, str) else rgb(tuple(parsed)) if isinstance(parsed, list) else str(parsed)))
        lines.append("EndMacro")
    # Igor executes only initialization statements before the first macro.
    # Place all calls first; interleaving calls and definitions opens only the
    # first window in the native loader.
    invocations = [line for line in lines if re.fullmatch(r"[A-Za-z_]\w*\(\)", line)]
    definitions = [line for line in lines if line not in invocations]
    return "\n".join([*invocations, *definitions]) + "\n"


def save_h5xp(path: Path, document: EditableGraphDocument) -> None:
    path = Path(path)
    if path.exists():
        raise FileExistsError("H5XP export is save-only; choose a new filename. Editable overwrites use MDSG.")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix="." + path.name, suffix=".tmp", dir=path.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        projection = document.render_source()
        references: dict[str, str] = {}
        used: set[str] = set()
        hashes = []
        with h5py.File(temporary, "w") as handle:
            handle.attrs["IGORRequiredVersion"] = np.float64(9.0)
            handle.attrs.create("IGORPlatform", b"Windows", dtype=h5py.string_dtype("utf-8", 7))
            folder = handle.require_group("Packed Data/MDS")
            for wave in projection.waves:
                name = unique_igor_name(wave.name, used)
                references[wave.id] = "root:MDS:" + name
                data = wave.data
                code = _IGOR_NUMERIC_TYPES.get((data.dtype.kind, data.dtype.itemsize))
                if data.dtype.kind in "US":
                    code = 0
                    dataset = folder.create_dataset(
                        name, data=data.astype(object), dtype=h5py.string_dtype("utf-8")
                    )
                else:
                    if data.dtype.kind == "c":
                        code = _IGOR_NUMERIC_TYPES[("f", data.dtype.itemsize // 2)] | 1
                        data = np.stack((data.real, data.imag), axis=1).reshape(
                            (data.shape[0] * 2, *data.shape[1:])
                        )
                    if code is None:
                        raise ValueError(f"Unsupported wave type: {wave.dtype}")
                    dataset = folder.create_dataset(name, data=data)
                dataset.attrs["IGORWaveType"] = np.int32(code)
                if wave.data.dtype.kind == "c":
                    dataset.attrs["IGORComplexWave"] = np.int32(1)
                scales = np.asarray([(0., 0.), *[(delta, offset) for offset, delta in wave.dimension_scaling]])
                dataset.attrs["IGORWaveScaling"] = scales
                dataset.attrs.create(
                    "IGORWaveUnits",
                    np.asarray([wave.data_units, *wave.dimension_units], dtype=object),
                    dtype=h5py.string_dtype("utf-8"),
                )
                dataset.attrs.create(
                    "IGORWaveNote",
                    wave.wave_note.encode(),
                    dtype=h5py.string_dtype("utf-8", max(1, len(wave.wave_note.encode()))),
                )
                if any(wave.dimension_names) or any(any(v) for v in wave.dimension_labels):
                    count = max((len(v) for v in wave.dimension_labels), default=0) + 1
                    labels = np.full((count, data.ndim), "", dtype=object)
                    for dim in range(data.ndim):
                        labels[0, dim] = wave.dimension_names[dim] if dim < len(wave.dimension_names) else ""
                        if dim < len(wave.dimension_labels):
                            values = wave.dimension_labels[dim]
                            labels[1:len(values) + 1, dim] = values
                    dataset.attrs.create("IGORWaveDimensionLabels", labels, dtype=h5py.string_dtype("utf-8"))
                hashes.append(hashlib.sha256(wave.data.tobytes()).hexdigest())
            from matplotlib import colormaps

            from materials_data_studio.tools.pxp_style import COLORMAPS, has_palette
            palettes: dict[str, str] = {}
            for graph in document.current.graphs:
                for image in graph.images:
                    table = image.color_table
                    if not has_palette(image) and (table in COLORMAPS or table in COLORMAPS.values() or table in palettes):
                        continue
                    colors = np.asarray(image.color_lut, dtype=np.uint16) if has_palette(image) else np.rint(colormaps[table](np.linspace(0, 1, 256)) * 65535).astype(np.uint16)
                    name = unique_igor_name("MDS_ctab_" + table, used)
                    dataset = folder.create_dataset(name, data=colors)
                    dataset.attrs["IGORWaveType"] = np.int32(_IGOR_NUMERIC_TYPES[("u", 2)])
                    dataset.attrs["IGORWaveScaling"] = np.array([(0., 0.), (1., 0.), (1., 0.)])
                    palettes[graph.name + ":" + image.wave if has_palette(image) else table] = "root:MDS:" + name
            recreation = procedures(document, references, palettes)
            handle.require_group("Recreation").create_dataset(
                "Recreation Procedures",
                data=recreation.replace("\n", "\r"),
                dtype=h5py.string_dtype("utf-8"),
            )
            metadata, _arrays = encode_document(
                replace(projection, source_bytes=b"", procedures=(), recreation_raw=recreation), include_arrays=False
            )
            state = dict(
                version=1,
                document=metadata,
                layout=document.current.layout,
                wave_paths=list(references.values()),
                wave_hashes=hashes,
                wave_metadata_hashes=[wave_metadata_hash(w) for w in projection.waves],
                recreation_sha256=hashlib.sha256(recreation.encode()).hexdigest(),
            )
            handle.require_group("MDS Graph Document").create_dataset(
                "State",
                data=json.dumps(state, ensure_ascii=False, allow_nan=False),
                dtype=h5py.string_dtype("utf-8"),
            )
        load_h5xp(temporary)
        if os.name == "nt":
            os.rename(temporary, path)  # Windows rename atomically refuses an existing destination.
        else:
            os.link(temporary, path)  # Atomic no-clobber on POSIX; finally removes the temporary link.
    finally:
        temporary.unlink(missing_ok=True)
