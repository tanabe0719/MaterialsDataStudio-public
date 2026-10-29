"""Versioned, JSON/NumPy-only snapshot codec for PXP viewer sessions."""

from __future__ import annotations

import io
from dataclasses import asdict
from typing import Any

import numpy as np

from materials_data_studio.tools.pxp_model import (
    PXP_SCHEMA_VERSION,
    PxpAnnotation,
    PxpAnnotationStyle,
    PxpAxis,
    PxpAxisStyle,
    PxpBoxPlot,
    PxpContour,
    PxpCursor,
    PxpDocument,
    PxpDrawing,
    PxpGraph,
    PxpGraphStyle,
    PxpImage,
    PxpIssue,
    PxpLayoutItem,
    PxpTable,
    PxpTrace,
    PxpViolinPlot,
    PxpWave,
)


def _tuple_values(record: dict[str, Any]) -> dict[str, Any]:
    """JSON arrays for canonical vectors/colors are restored as tuples."""
    return {key: tuple(value) if isinstance(value, list) else value for key, value in record.items()}


def encode_document(document: PxpDocument, *, include_arrays: bool = True) -> tuple[dict[str, Any], bytes]:
    """Serialize canonical state independently of the original PXP parser."""
    graphs = [asdict(graph) for graph in document.graphs]
    for graph in graphs:
        for style in [graph["style"]["axis_defaults"], *(axis["style"] for axis in graph["axes"])]:
            bounds = style.get("tick_enable")
            if bounds is not None:
                if len(bounds) != 2 or any(np.isnan(value) for value in bounds) or bounds[0] > bounds[1]:
                    raise ValueError("Invalid tick-enable bounds")
                # JSON must remain strict: use strings for intentional open
                # bounds, never nonstandard Infinity/NaN numeric literals.
                style["tick_enable"] = tuple(str(value) if np.isinf(value) else value for value in bounds)
    metadata: dict[str, Any] = {
        "pxp_viewer_schema_version": PXP_SCHEMA_VERSION,
        "source_id": document.source_id,
        "basename": document.basename,
        "sha256": document.sha256,
        "size": document.size,
        "igor_version": document.igor_version,
        "backend": document.backend,
        "backend_version": document.backend_version,
        "folders": list(document.folders),
        "tables": list(document.tables),
        "table_definitions": [asdict(table) for table in document.table_definitions],
        "procedures": list(document.procedures),
        "issues": [asdict(issue) for issue in document.issues],
        "status": document.status,
        "recreation_raw": document.recreation_raw,
        "command_history": list(document.command_history),
        "graphs": graphs,
        "waves": [
            {key: getattr(wave, key) for key in (
                "id", "igor_path", "name", "dtype", "shape", "dimension_scaling",
                "dimension_units", "data_units", "wave_note", "dimension_labels", "dimension_names",
            )}
            for wave in document.waves
        ],
    }
    arrays = {f"wave_{index}": wave.data for index, wave in enumerate(document.waves)}
    if not include_arrays:
        return metadata, b""
    buffer = io.BytesIO()
    np.savez_compressed(buffer, **arrays)  # pyright: ignore[reportArgumentType]
    return metadata, buffer.getvalue()


def decode_document(metadata: dict[str, Any], payload: bytes, source: bytes = b"") -> PxpDocument:
    """Restore the snapshot without invoking igor2 or executing procedures."""
    version = metadata.get("pxp_viewer_schema_version")
    if version not in {*range(1, PXP_SCHEMA_VERSION + 1)}:
        raise ValueError(f"Unsupported PXP viewer schema version: {version}")
    waves: list[PxpWave] = []
    with np.load(io.BytesIO(payload), allow_pickle=False) as archive:
        for index, record in enumerate(metadata["waves"]):
            data = np.array(archive[f"wave_{index}"], copy=True)
            if tuple(data.shape) != tuple(record["shape"]) or data.dtype.str != record["dtype"]:
                raise ValueError(f"PXP wave {index} does not match its metadata")
            data.setflags(write=False)
            waves.append(PxpWave(
                record["id"], record["igor_path"], record["name"], record["dtype"],
                tuple(record["shape"]), data,
                tuple(tuple(pair) for pair in record["dimension_scaling"]),
                tuple(record["dimension_units"]), record["data_units"], record["wave_note"],
                tuple(tuple(v) for v in record.get("dimension_labels", ())),
                tuple(record.get("dimension_names", ())),
            ))
    graphs = []
    def annotation_style(record: dict[str, Any]) -> PxpAnnotationStyle:
        values = _tuple_values(record)
        if version < 10 and values.get("offset_mode", "legacy") == "legacy" and values.get("frame", 1) == 1:
            values["frame"] = 2
        return PxpAnnotationStyle(**values)
    def axis_style(record: dict[str, Any]) -> PxpAxisStyle:
        values = _tuple_values(record)
        bounds = values.get("tick_enable")
        if bounds is not None:
            parsed = tuple(float(value) for value in bounds)
            if len(parsed) != 2 or any(np.isnan(value) for value in parsed) or parsed[0] > parsed[1]:
                raise ValueError("Invalid tick-enable bounds")
            values["tick_enable"] = parsed
        return PxpAxisStyle(**values)
    for graph in metadata["graphs"]:
        axes = []
        for axis in graph["axes"]:
            axes.append(PxpAxis(**{**_tuple_values(axis), "style": axis_style(axis.get("style", {}))}))
        graph_style = graph.get("style", {})
        default_style = axis_style(graph_style.get("axis_defaults", {}))
        graph_style = {**graph_style, "axis_defaults": default_style,
                       "margins": tuple(tuple(pair) for pair in graph_style.get("margins", ())),
                       "dash_patterns": tuple((int(p[0]), tuple(p[1])) for p in graph_style.get("dash_patterns", ()))}
        graphs.append(PxpGraph(
            graph["name"], tuple(PxpTrace(**{**_tuple_values(trace), "box": PxpBoxPlot(**_tuple_values(trace["box"])) if trace.get("box") else None,
                "violin": PxpViolinPlot(**_tuple_values(trace["violin"])) if trace.get("violin") else None}) for trace in graph["traces"]),
            tuple(axes), tuple(PxpImage(**{**_tuple_values(image), "color_lut": tuple(tuple(row) for row in image.get("color_lut", ())), "colorbar_style":
                PxpAnnotationStyle(**_tuple_values(image.get("colorbar_style", {}))), "colorbar_axis": axis_style(image.get("colorbar_axis", {}))}) for image in graph["images"]),
            graph["legend_raw"], graph["swap_xy"],
            tuple(PxpIssue(**issue) for issue in graph["issues"]), graph["status"],
            PxpGraphStyle(**_tuple_values(graph_style)),
            annotation_style(graph.get("legend_style", {})),
            tuple(PxpAnnotation(a["name"], a["raw"], annotation_style(a["style"]), a.get("trace", ""), a.get("point"), a.get("arrow", False))
                  for a in graph.get("annotations", ())),
            tuple(PxpDrawing(**_tuple_values(d)) for d in graph.get("drawings", ())),
            graph.get("recreation_raw", ""),
            tuple(PxpCursor(**_tuple_values(c)) for c in graph.get("cursors", ())),
            tuple(PxpContour(**_tuple_values(c)) for c in graph.get("contours", ())),
            tuple(PxpLayoutItem(**_tuple_values(c)) for c in graph.get("layout_items", ())),
            graph.get("window_kind", "Graph"),
            graph.get("host", ""),
            tuple(graph["host_rect"]) if graph.get("host_rect") else None,
        ))
    from materials_data_studio.tools.pxp_style import restore_sampled_colormaps

    restore_sampled_colormaps(waves, graphs)
    return PxpDocument(
        metadata["source_id"], metadata["basename"], metadata["sha256"],
        metadata["size"], metadata["igor_version"], metadata["backend"],
        metadata["backend_version"], tuple(waves), tuple(metadata["folders"]),
        tuple(graphs), tuple(metadata["tables"]), tuple(metadata["procedures"]),
        tuple(PxpIssue(**issue) for issue in metadata["issues"]), metadata["status"], source,
        metadata.get("recreation_raw", ""),
        tuple(PxpTable(record["name"], tuple(record.get("columns", ())),
                       tuple(tuple(pair) for pair in record.get("widths", ())),
                       tuple(tuple(style) for style in record.get("column_styles", ())))
              for record in metadata.get("table_definitions", ())),
        tuple(metadata.get("command_history", ())),
    )
