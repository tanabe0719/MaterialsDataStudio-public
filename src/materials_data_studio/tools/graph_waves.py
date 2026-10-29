"""Copy-on-write working waves and general-text import; never mutate a PXP."""

from __future__ import annotations

import csv
import io
import re
import uuid
from collections import Counter, deque
from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import Any

import numpy as np

from materials_data_studio.tools.graph_document import EditableGraphDocument, GraphState
from materials_data_studio.tools.pxp_model import PxpDocument, PxpGraph, PxpWave

MAX_TEXT_COLUMNS = 512
MAX_PARSED_COLUMNS = 4096
MAX_TEXT_CELLS = 5_000_000
MAX_TEXT_LINE = 4 * 1024**2


def empty_document() -> EditableGraphDocument:
    source = PxpDocument(uuid.uuid4().hex, "Untitled.mdsg", "", 0, "", "MDS", "1", status="DATA_ONLY")
    return EditableGraphDocument.from_pxp(source)


def make_wave(name: str, data: Any, template: PxpWave | None = None) -> PxpWave:
    path = name if name.startswith("root:") else "root:" + name
    if not re.fullmatch(r"root:(?:[^:\r\n]+:)*[^:\r\n]+", path):
        raise ValueError("Provide a non-empty wave name (optionally root:folder:name).")
    array = np.array(data, copy=True)
    if array.dtype.hasobject or not 1 <= array.ndim <= 4 or 0 in array.shape:
        raise ValueError("Waves must be non-empty 1D through 4D numeric/text arrays, not objects.")
    array.setflags(write=False)
    scaling = tuple(template.dimension_scaling[i] if template and i < len(template.dimension_scaling) else (0.0, 1.0) for i in range(array.ndim))
    units = tuple(template.dimension_units[i] if template and i < len(template.dimension_units) else "" for i in range(array.ndim))
    return PxpWave(template.id if template else uuid.uuid4().hex, path, path.rsplit(":", 1)[-1],
                   array.dtype.str, array.shape, array, scaling, units,
                   template.data_units if template else "", template.wave_note if template else "",
                   tuple(tuple((template.dimension_labels[i] if template and i < len(template.dimension_labels) else ())[:n])
                         for i, n in enumerate(array.shape)),
                   tuple(template.dimension_names[i] if template and i < len(template.dimension_names) else "" for i in range(array.ndim)))


def put_waves(document: EditableGraphDocument, waves: list[PxpWave], *, overwrite: bool = False) -> GraphState:
    result = document.current.clone()
    catalog = list(document.render_source().waves)
    paths = [w.igor_path for w in waves]
    if len(set(paths)) != len(paths):
        raise ValueError("Imported wave names must be unique.")
    for wave in waves:
        index = next((i for i, w in enumerate(catalog) if w.igor_path == wave.igor_path), None)
        if index is not None:
            if not overwrite:
                raise ValueError(f"Wave already exists: {wave.igor_path}. Enable overwrite explicitly.")
            catalog[index] = replace(wave, id=catalog[index].id)
        else:
            catalog.append(wave)
    result.waves = tuple(catalog)
    validate_references(document, result)
    return result


def validate_references(document: EditableGraphDocument, state: GraphState) -> None:
    source = replace(document.source, waves=document.source.waves if state.waves is None else state.waves)
    for graph in state.graphs:
        if graph.host and not any(g.name == graph.host and not g.host and g.window_kind == "Graph" for g in state.graphs):
            raise ValueError("Subwindow host does not exist")
        for axis in graph.axes:
            if axis.style.user_ticks:
                values, labels = (source.wave(ref) for ref in axis.style.user_ticks)
                if (values is None or labels is None or values.data.ndim != 1 or values.data.dtype.kind not in "biuf"
                        or labels.data.ndim not in {1, 2} or labels.data.dtype.kind not in "SU"
                        or values.shape[0] != labels.shape[0]):
                    raise ValueError(f"Axis {axis.name}: user tick values and text labels must have matching lengths.")
        for trace in graph.traces:
            y = source.wave(trace.y_wave)
            distribution = trace.box or trace.violin
            if distribution is not None:
                found = [source.wave(ref) for ref in distribution.waves]
                if not found or any(w is None or w.data.dtype.kind not in "biuf" for w in found):
                    raise ValueError(f"Box Plot {trace.name} requires real numeric source waves")
                sources = [w for w in found if w is not None]
                if not (all(w.data.ndim == 1 for w in sources) or (len(sources) == 1 and sources[0].data.ndim == 2)):
                    raise ValueError("Box Plot accepts 1D datasets or one 2D matrix, not a mixture")
                count = len(sources) if sources[0].data.ndim == 1 else sources[0].shape[1]
                if trace.x_wave:
                    x = source.wave(trace.x_wave)
                    if x is None or x.data.ndim != 1 or len(x.data) < count:
                        raise ValueError("Box Plot X wave must contain at least one coordinate/label per dataset")
                if distribution.category_labels and sources[0].data.ndim != 2:
                    raise ValueError("Box Plot /CATL requires a matrix with column labels")
                continue
            if y is None or y.data.ndim != 1 or y.data.dtype.kind not in "biuf":
                raise ValueError(f"Trace {trace.name} requires a real numeric 1D Y wave.")
            for ref in (trace.x_wave, trace.error_low, trace.error_high, trace.x_error_low, trace.x_error_high):
                if ref:
                    w = source.wave(ref)
                    if w is None or w.data.ndim != 1 or w.data.dtype.kind not in ("biufSU" if ref == trace.x_wave else "biuf") or w.shape != y.shape:
                        raise ValueError(f"Trace {trace.name}: X/Y/error wave lengths must match.")
            if trace.point_range is not None and not 0 <= trace.point_range[0] <= trace.point_range[1] < len(y.data):
                raise ValueError("Trace point range is outside the wave")
            if trace.z_color or trace.z_size or trace.z_marker:
                if trace.mode not in {None, 0, 2, 3, 4}:
                    raise ValueError("Wave-driven appearance supports lines, dots, markers and lines+markers")
                refs = [r for r in (trace.z_color[0] if trace.z_color else None, trace.z_size[0] if trace.z_size else None, trace.z_marker) if r]
                for ref in refs:
                    w = source.wave(ref)
                    direct = trace.z_color and ref == trace.z_color[0] and trace.z_color[3].casefold() == "directrgb"
                    if w is None or w.data.dtype.kind not in "biuf" or w.shape[0] != y.shape[0] or (not direct and w.data.ndim != 1) or (direct and (w.data.ndim != 2 or w.shape[1] not in {3, 4})):
                        raise ValueError("Color/size/marker wave must match the trace point count (directRGB: Nx3/4)")
                if trace.z_size and trace.z_size[5] and not any(a.name == trace.z_size[5] for a in graph.axes):
                    raise ValueError("Marker size axis does not exist")
                if trace.z_color:
                    from matplotlib import colormaps

                    from materials_data_studio.tools.igor_trace_channels import channel_range
                    from materials_data_studio.tools.pxp_style import COLORMAPS
                    table, ref = trace.z_color[3], trace.z_color[5]
                    color_wave = source.wave(trace.z_color[0])
                    assert color_wave is not None
                    if table.casefold() != "directrgb":
                        channel_range(color_wave.data, trace.z_color[1], trace.z_color[2], trace.log_z_color)
                    if table.casefold() in {"ctablergb", "cindexrgb"}:
                        w = source.wave(ref)
                        if w is None or w.data.ndim != 2 or w.shape[1] not in {3, 4} or w.data.dtype.kind not in "biuf" or not np.isfinite(w.data).all():
                            raise ValueError("zColor requires a real RGB/RGBA table wave")
                        if table.casefold() == "cindexrgb" and (w.shape[0] < 2 or w.dimension_scaling[0][1] == 0):
                            raise ValueError("cindexRGB needs at least two colors and nonzero index scaling")
                    elif table.casefold() != "directrgb" and table not in COLORMAPS and table not in colormaps:
                        raise ValueError(f"Unknown color table: {table}")
                if trace.color_scale and (trace.z_color is None or trace.z_color[3].casefold() == "directrgb"):
                    raise ValueError("A scalar color wave is required for ColorScale")
        for cursor in graph.cursors:
            target = next((t for t in graph.traces if t.name == cursor.trace), None)
            values = source.wave(target.y_wave) if target else None
            if values is None or not 0 <= cursor.point < len(values.data):
                raise ValueError("Cursor references a missing trace or point")
        for contour in graph.contours:
            matrix = source.wave(contour.wave)
            if matrix is None or matrix.data.ndim != 2 or matrix.data.dtype.kind not in "biuf":
                raise ValueError("Contour requires a real numeric matrix")
        for item in graph.layout_items:
            if not any(g.name == item.graph and g.window_kind == "Graph" for g in state.graphs):
                raise ValueError("Layout references a missing graph")
        for image in graph.images:
            w = source.wave(image.wave)
            if w is None or (w.data.ndim != 2 and not (w.data.ndim == 3 and w.shape[2] in {3, 4})) or w.data.dtype.kind not in "biuf":
                raise ValueError("Image graphs require a real matrix or RGB/RGBA wave.")
            for dimension, ref in enumerate((image.x_wave, image.y_wave)):
                if ref:
                    edges = source.wave(ref)
                    if edges is None or edges.data.ndim != 1 or len(edges.data) != w.shape[dimension] + 1 or edges.data.dtype.kind not in "biuf" or not np.isfinite(edges.data).all() or not (np.all(np.diff(edges.data) > 0) or np.all(np.diff(edges.data) < 0)):
                        raise ValueError("Image edge coordinates must be finite, strictly monotonic and one longer than the matrix dimension")


def rename_wave(document: EditableGraphDocument, old: str, new: str) -> GraphState:
    source = document.render_source()
    wave = source.wave(old)
    if wave is None:
        raise ValueError("Wave not found.")
    renamed = make_wave(new, wave.data, wave)
    if source.wave(renamed.igor_path) is not None and renamed.igor_path != wave.igor_path:
        raise ValueError("This wave name is already used.")
    state = document.current.clone()
    state.waves = tuple(renamed if w.id == wave.id else w for w in source.waves)
    state.tables = tuple(replace(t, columns=tuple(renamed.igor_path + (ref[-2:] if ref.endswith((".i", ".l", ".d")) else "") if source.wave(ref[:-2] if ref.endswith((".i", ".l", ".d")) else ref) is wave else ref for ref in t.columns),
                                 widths=tuple((renamed.igor_path + (ref[-2:] if ref.endswith((".i", ".l", ".d")) else "") if source.wave(ref[:-2] if ref.endswith((".i", ".l", ".d")) else ref) is wave else ref, v) for ref, v in t.widths),
                                 column_styles=tuple((renamed.igor_path + (ref[-2:] if ref.endswith((".i", ".l")) else "") if source.wave(ref[:-2] if ref.endswith((".i", ".l", ".d")) else ref) is wave else ref, key, value) for ref, key, value in t.column_styles))
                         for t in source.table_definitions)
    def ref(value: str | None) -> str | None:
        return renamed.igor_path if value and source.wave(value) is wave else value
    state.graphs = tuple(replace(g, traces=tuple(replace(t, y_wave=ref(t.y_wave) or "", x_wave=ref(t.x_wave),
        error_low=ref(t.error_low), error_high=ref(t.error_high),
        z_color=(ref(t.z_color[0]) or "", *t.z_color[1:5], ref(t.z_color[5]) or "") if t.z_color else None,
        z_size=(ref(t.z_size[0]) or "", *t.z_size[1:]) if t.z_size else None,
        z_marker=ref(t.z_marker), box=replace(t.box, waves=tuple(ref(r) or "" for r in t.box.waves)) if t.box else None,
        violin=replace(t.violin, waves=tuple(ref(r) or "" for r in t.violin.waves)) if t.violin else None,
        x_error_low=ref(t.x_error_low), x_error_high=ref(t.x_error_high)) for t in g.traces),
        contours=tuple(replace(c, wave=ref(c.wave) or "") for c in g.contours),
        images=tuple(replace(i, wave=ref(i.wave) or "", x_wave=ref(i.x_wave), y_wave=ref(i.y_wave)) for i in g.images),
        axes=tuple(replace(a, style=replace(a.style, user_ticks=tuple(ref(r) or "" for r in a.style.user_ticks)))
                   if a.style.user_ticks else a for a in g.axes)) for g in state.graphs)
    for graph in state.graphs:
        layout = state.layout.get(graph.name, {})
        for section in (layout.get("view", {}).get("images", {}), layout.get("colorbars", {})):
            for key in list(section):
                if source.wave(key) is wave:
                    section[renamed.igor_path] = section.pop(key)
    return state


def wave_references(document: EditableGraphDocument, path: str) -> list[str]:
    source = document.render_source()
    wave = source.wave(path)
    graphs = [g.name for g in document.current.graphs if any(
        source.wave(ref) is wave for ref in
        [*(r for t in g.traces for r in trace_wave_references(t) if r), *(r for i in g.images for r in (i.wave, i.x_wave, i.y_wave) if r), *(c.wave for c in g.contours),
         *(r for a in g.axes for r in (a.style.user_ticks or ()))])]
    return graphs + [t.name for t in source.table_definitions if any(source.wave(ref[:-2] if ref.endswith((".i", ".l", ".d")) else ref) is wave for ref in t.columns)]


def delete_wave(document: EditableGraphDocument, path: str, *, cascade: bool = False) -> GraphState:
    source = document.render_source()
    wave = source.wave(path)
    if wave is None:
        raise ValueError("Wave not found.")
    if wave_references(document, path) and not cascade:
        raise ValueError("This wave is used by graphs or tables. Confirm removal of dependent references first.")
    state = document.current.clone()
    state.waves = tuple(w for w in source.waves if w.id != wave.id)
    state.tables = tuple(replace(t, columns=tuple(ref for ref in t.columns if source.wave(ref[:-2] if ref.endswith((".i", ".l", ".d")) else ref) is not wave))
                         for t in source.table_definitions)
    def used(ref: str | None) -> bool:
        return bool(ref) and source.wave(ref or "") is wave
    state.graphs = tuple(replace(g,
        traces=tuple(t for t in g.traces if not any(used(r) for r in trace_wave_references(t))),
        images=tuple(i for i in g.images if not any(used(r) for r in (i.wave, i.x_wave, i.y_wave))),
        contours=tuple(c for c in g.contours if not used(c.wave)),
        cursors=tuple(c for c in g.cursors if any(t.name == c.trace and not any(used(r) for r in trace_wave_references(t)) for t in g.traces)),
        axes=tuple(replace(a, style=replace(a.style, user_ticks=None))
                   if a.style.user_ticks and any(used(r) for r in a.style.user_ticks) else a for a in g.axes)) for g in state.graphs)
    return state


def trace_wave_references(trace: Any) -> tuple[str | None, ...]:
    """All data references, including auxiliary appearance and distributions."""
    return (trace.y_wave, trace.x_wave, trace.error_low, trace.error_high,
            trace.z_color[0] if trace.z_color else None, trace.z_color[5] if trace.z_color else None,
            trace.z_size[0] if trace.z_size else None, trace.z_marker,
            *(trace.box.waves if trace.box else ()), *(trace.violin.waves if trace.violin else ()), trace.x_error_low, trace.x_error_high)


def resize_wave(wave: PxpWave, count: int, index: int | None = None, delete: bool = False) -> PxpWave:
    if wave.data.ndim != 1 or count <= 0:
        raise ValueError("Point operations require a 1D wave and a positive count.")
    data = wave.data
    fill: Any = "" if data.dtype.kind in "SU" else (np.nan if data.dtype.kind in "fc" else 0)
    if index is None:
        result = np.full(count, fill, dtype=data.dtype)
        result[:min(count, len(data))] = data[:min(count, len(data))]
    elif not 0 <= index <= len(data):
        raise ValueError("Point index is outside the wave.")
    elif delete:
        result = np.concatenate((data[:index], data[index + count:]))
    else:
        result = np.concatenate((data[:index], np.full(count, fill, dtype=data.dtype), data[index:]))
    return make_wave(wave.igor_path, result, wave)


def _check_cancelled(cancelled: Event | None) -> None:
    if cancelled is not None and cancelled.is_set():
        raise InterruptedError("General-text import stopped")


def read_text(path: Path, *, cancelled: Event | None = None) -> str:
    _check_cancelled(cancelled)
    if path.stat().st_size > 256 * 1024**2:
        raise ValueError("General-text import is limited to 256 MiB.")
    data = bytearray()
    with path.open("rb") as stream:
        while block := stream.read(1024**2):
            _check_cancelled(cancelled)
            data.extend(block)
            if len(data) > 256 * 1024**2:
                raise ValueError("General-text import is limited to 256 MiB.")
    _check_cancelled(cancelled)
    encodings = ("utf-16",) if data.startswith((b"\xff\xfe", b"\xfe\xff")) else ("utf-8-sig", "cp932")
    for encoding in encodings:
        try:
            return data.decode(encoding)
        except UnicodeError:
            continue
    raise ValueError("Text encoding could not be recognized (UTF-8 / UTF-16 / CP932).")


def text_rows(text: str, delimiter: str = "Auto", *, cancelled: Event | None = None) -> list[list[str]]:
    _check_cancelled(cancelled)

    def bounded_lines():
        source = io.StringIO(text, newline="")
        while line := source.readline(MAX_TEXT_LINE + 1):
            _check_cancelled(cancelled)
            if len(line) > MAX_TEXT_LINE:
                raise ValueError("A general-text line exceeds the 4 MiB safety limit.")
            yield line

    if delimiter == "Auto":
        # Sniffer fails on mixed instrument headers and rectangular data.
        # Score repeated multi-column rows, ignoring arbitrary leading text.
        # Keep only the sample instead of another full-file list of strings.
        tail: deque[str] = deque(maxlen=100)
        sample: list[str] = []
        section = False
        for index, line in enumerate(bounded_lines()):
            if index % 1024 == 0:
                _check_cancelled(cancelled)
            if section:
                sample.append(line)
                if len(sample) == 100:
                    break
            elif line.strip().casefold() == "[data]":
                section = True
            else:
                tail.append(line)
        if not section:
            sample = list(tail)
        scores: dict[str, tuple[int, int]] = {}
        for candidate in ("\t", ",", ";", "Whitespace"):
            parsed = ((re.split(r"\s+", line.strip()) for line in sample if line.strip())
                      if candidate == "Whitespace" else csv.reader(sample, delimiter=candidate))
            counts: Counter[int] = Counter()
            numeric_rows = 0
            for row in parsed:
                if len(row) > MAX_PARSED_COLUMNS:
                    numeric_rows, counts = -1, Counter()
                    break  # Reject this candidate before retaining a wide sample.
                if len(row) <= 1:
                    continue
                counts[len(row)] += 1
                present = sum(bool(cell.strip()) for cell in row)
                numeric = sum(_numeric(cell) for cell in row if cell.strip())
                numeric_rows += int(numeric >= max(1, (present + 1) // 2))
            # Prefer repeated measurement rows, not delimiter-heavy metadata.
            # Width must not outweigh an ordinary long two-column data block.
            scores[candidate] = (numeric_rows, max(counts.values(), default=0))
        delimiter = max(scores, key=lambda key: scores[key])

    if delimiter == "Whitespace":
        records = (re.split(r"\s+", line.strip()) if line.strip() else [] for line in bounded_lines())
    else:
        delimiter = {"Comma": ",", "Tab": "\t", "Semicolon": ";"}.get(delimiter, delimiter)
        # csv.reader must receive the original newlines: splitlines() silently
        # removed line breaks inside quoted, multiline text cells.
        records = csv.reader(bounded_lines(), delimiter=delimiter)
    rows: list[list[str]] = []
    cells = 0
    for index, row in enumerate(records):
        if index % 1024 == 0:
            _check_cancelled(cancelled)
        if len(row) > MAX_PARSED_COLUMNS:
            raise ValueError(f"A general-text record exceeds {MAX_PARSED_COLUMNS} fields. Choose the correct delimiter or split the file.")
        cells += len(row)
        if cells > MAX_TEXT_CELLS:
            raise ValueError("General-text import exceeds the 5 million-cell safety limit. Split the file into smaller inputs.")
        rows.append([cell.strip() for cell in row])
    _check_cancelled(cancelled)
    return rows


def detect_start(rows: list[list[str]], *, cancelled: Event | None = None) -> int:
    _check_cancelled(cancelled)
    section = None
    for index, row in enumerate(rows):
        if index % 1024 == 0:
            _check_cancelled(cancelled)
        if len(row) == 1 and row[0].strip().casefold() == "[data]":
            section = index
            break
    if section is not None:
        # Quantum Design: [Data], column-name row, then measurement rows.
        # The first numeric cell need not be in column 0; most columns may be blank.
        names = next((i for i in range(section + 1, len(rows)) if rows[i] and any(c.strip() for c in rows[i])), None)
        if names is not None:
            return next((i for i in range(names + 1, len(rows)) if rows[i] and any(_numeric(c) for c in rows[i])), min(names + 1, len(rows) - 1))
    # Metadata may itself contain numbers. Prefer the longest rectangular
    # numeric block, not the first header line with one numeric token.
    best: tuple[tuple[int, int, int], int] | None = None
    previous: list[str] = []
    for index, row in enumerate(rows):
        if index % 1024 == 0:
            _check_cancelled(cancelled)
        if not row:
            # Blank lines do not interrupt a numeric block below. They must
            # not create a new, full-file candidate scan on every other row.
            continue
        preceding, previous = previous, row
        numeric = {col for col, cell in enumerate(row) if _numeric(cell)}
        present = sum(bool(cell.strip()) for cell in row)
        if len(numeric) < max(1, (present + 1) // 2):
            continue
        if len(preceding) == len(row) and all(
                not preceding[col] or _numeric(preceding[col]) for col in numeric):
            continue
        length = 1
        # Slicing the entire remainder for each short candidate copied O(N²)
        # references in mixed numeric/text tables, even when the next row broke
        # the candidate immediately.
        for following_index in range(index + 1, len(rows)):
            if (following_index - index) % 1024 == 1:
                _check_cancelled(cancelled)
            following = rows[following_index]
            if not following:
                continue
            if len(following) != len(row) or any(following[c] and not _numeric(following[c]) for c in numeric):
                break
            length += 1
        candidate = ((int(0 in numeric), length, len(row)), index)
        if best is None or candidate[0] > best[0]:
            best = candidate
    return best[1] if best else 0


def column_names(rows: list[list[str]], start: int) -> list[str]:
    if start <= 0 or not rows[start]:
        return []
    previous = next((rows[i] for i in range(start - 1, -1, -1) if rows[i] and any(c.strip() for c in rows[i])), [])
    sectioned = any(rows[i] and rows[i][0].strip().casefold() == "[data]" for i in range(start))
    if (not sectioned and len(previous) != len(rows[start])) or any(_numeric(c) for c in previous):
        return []
    if previous[0].lstrip().startswith("["):
        return []
    return [c.strip().lstrip("#").strip() for c in previous]


def _numeric(value: str) -> bool:
    try:
        float(value.replace("D", "E").replace("d", "e"))
        return True
    except ValueError:
        return False


def import_columns(rows: list[list[str]], start: int, names: list[str], kinds: list[str], *, cancelled: Event | None = None) -> list[PxpWave]:
    _check_cancelled(cancelled)
    if not 0 <= start < len(rows) or not names or len(names) != len(kinds):
        raise ValueError("Choose a valid first data row and column names.")
    records = [row for row in rows[start:] if row]
    waves = []
    for col, (name, kind) in enumerate(zip(names, kinds, strict=True)):
        _check_cancelled(cancelled)
        if kind == "Skip":
            continue
        values = [row[col] if col < len(row) else "" for row in records]
        if kind == "Text":
            data = np.array(values, dtype=str)
        else:
            numeric = []
            for offset, cell in enumerate(values):
                if offset % 1024 == 0:
                    _check_cancelled(cancelled)
                try:
                    numeric.append(float(cell.replace("D", "E").replace("d", "e")) if cell else np.nan)
                except ValueError as exc:
                    raise ValueError(f"Row {start + offset + 1}, column {col + 1}: not a number. Select Text or Skip.") from exc
            data = np.array(numeric, dtype=np.float64)
        waves.append(make_wave(name, data))
    _check_cancelled(cancelled)
    if not waves:
        raise ValueError("Select at least one column to import.")
    return waves


def numeric_waves(document: EditableGraphDocument, dimension: int = 1) -> list[PxpWave]:
    return [w for w in document.render_source().waves if w.data.ndim == dimension and w.data.dtype.kind in "biuf"]


def reorder_traces(graph: PxpGraph, names: list[str]) -> PxpGraph:
    if len(names) != len(graph.traces) or set(names) != {t.name for t in graph.traces}:
        raise ValueError("Reordering must contain every trace exactly once.")
    catalog = {t.name: t for t in graph.traces}
    return replace(graph, traces=tuple(catalog[name] for name in names))
