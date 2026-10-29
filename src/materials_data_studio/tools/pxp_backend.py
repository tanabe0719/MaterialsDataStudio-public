"""Bounded, read-only igor2 adapter for packed experiments."""

from __future__ import annotations

import hashlib
import io
import re
import struct
from collections import Counter
from importlib.metadata import version
from pathlib import Path
from typing import Protocol
from uuid import uuid4

import numpy as np
from igor2 import packed
from igor2.record import (
    RECORD_TYPE,
    FolderEndRecord,
    FolderStartRecord,
    GetHistoryRecord,
    HistoryRecord,
    ProcedureRecord,
    RecreationRecord,
    WaveRecord,
)
from igor2.record.base import Record

from materials_data_studio.tools.pxp_model import PxpDocument, PxpIssue, PxpWave
from materials_data_studio.tools.pxp_recreation import parse_recreation

MAX_PXP_BYTES = 512 * 1024 * 1024
MAX_RECORDS = 100_000


class PxpBackend(Protocol):
    def open(self, path: Path) -> PxpDocument: ...


def _decode(value: object) -> str:
    if isinstance(value, np.ndarray):
        value = value.tobytes()
    if isinstance(value, bytes):
        raw = value.split(b"\0", 1)[0]
        for codec in ("utf-8", "cp932"):
            try:
                return raw.decode(codec)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", "replace")
    return str(value)


def _record_stream(source: bytes, issues: list[PxpIssue]) -> list[Record]:
    """Salvage valid records with igor2's typed record decoders."""
    records: list[Record] = []
    stream = io.BytesIO(source)
    byte_order = "<"
    for _ in range(MAX_RECORDS):
        header_bytes = stream.read(8)
        if not header_bytes:
            break
        if len(header_bytes) != 8:
            issues.append(PxpIssue("PXP", "Truncated record header"))
            break
        record_type, record_version, size = struct.unpack(byte_order + "Hhi", header_bytes)
        if size < 0 or size > len(source) - stream.tell():
            issues.append(PxpIssue("PXP", "Truncated or invalid record body"))
            break
        data = stream.read(size)
        record_class = RECORD_TYPE.get(record_type & packed.PACKEDRECTYPE_MASK)
        if record_class is None:
            continue
        try:
            header = {"recordType": record_type, "version": record_version, "numDataBytes": size}
            if record_class is WaveRecord and len(data) >= 2 and struct.unpack_from("<H", data)[0] == 7:
                records.append(_long_name_record(header, data))
                if any(struct.unpack_from("<4i", data, 36)):
                    issues.append(PxpIssue("wave", "Igor v7 variable-length dimension labels are not imported; use H5XP to retain them."))
            else:
                records.append(record_class(header, data, byte_order=byte_order))
        except (AssertionError, KeyError, TypeError, ValueError, IndexError, struct.error) as exc:
            issues.append(PxpIssue("record", f"Record {record_type & 0x7fff} could not be read: {type(exc).__name__}"))
    else:
        issues.append(PxpIssue("PXP", "Record count exceeds safety limit"))
    return records


def _long_name_record(header: dict[str, int], data: bytes) -> WaveRecord:
    """Adapt TN003 v7's identical 320-byte wave header for igor2's v5 decoder.

    The UTF-8 long name follows string indices; v7 dimension labels are variable
    length (not v5's fixed 32 bytes). Skip those bounded blocks, with an explicit
    import issue from the caller, rather than ask igor2 to misinterpret them.
    Layout: Igor Pro 10 TN003 / Source / IgorBin.h, BinHeader5.
    """
    if len(data) < 384:
        raise ValueError("Truncated v7 header")
    if sum(struct.unpack_from("<192h", data)) & 0xffff:
        raise ValueError("Invalid v7 header checksum")
    sizes = struct.unpack_from("<13i", data, 4)
    wfm, formula, note, units = sizes[:4]
    dimensions, labels, strings = sizes[4:8], sizes[8:12], sizes[12]
    name_size, options1, options2 = struct.unpack_from("<hhi", data, 56)
    if wfm < 320 or any(size < 0 for size in (*sizes, name_size, options1, options2)):
        raise ValueError("Invalid v7 block lengths")
    points = struct.unpack_from("<i", data, 76)[0]
    dimensions_count = struct.unpack_from("<4i", data, 132)
    shape = []
    for count in dimensions_count:
        if count == 0:
            break
        if count < 0:
            raise ValueError("Invalid v7 dimensions")
        shape.append(count)
    if points < 0 or (int(np.prod(shape, dtype=object)) if shape else 0) != points:
        raise ValueError("Inconsistent v7 point count")
    labels_start = 64 + wfm + formula + note + units + sum(dimensions)
    names_start = labels_start + sum(labels) + strings
    if not 0 < name_size <= 256 or names_start + name_size + options1 + options2 != len(data):
        raise ValueError("Invalid v7 long name or record size")
    name = data[names_start:names_start + name_size].rstrip(b"\0").decode("utf-8")
    if not name or "\0" in name:
        raise ValueError("Invalid v7 wave name")
    adapted = bytearray(data[:labels_start] + data[labels_start + sum(labels):names_start])
    struct.pack_into("<H", adapted, 0, 5)
    struct.pack_into("<4i", adapted, 36, 0, 0, 0, 0)
    struct.pack_into("<hhi", adapted, 56, 0, 0, 0)
    # Recompute the v5 header checksum after normalizing its version/blocks.
    struct.pack_into("<h", adapted, 2, 0)
    checksum = (-sum(struct.unpack_from("<192h", adapted))) & 0xffff
    struct.pack_into("<H", adapted, 2, checksum)
    record = WaveRecord(header, bytes(adapted), byte_order="<")
    record.wave["wave"]["wave_header"]["bname"] = name.encode("utf-8")
    return record


class Igor2Backend:
    """Convert igor2 records to MDS models; never expose parser objects to UI."""

    def open(self, path: Path) -> PxpDocument:
        source_id = str(uuid4())
        basename = path.name
        try:
            size = path.stat().st_size
            if size < 8 or size > MAX_PXP_BYTES:
                raise ValueError("PXP size is invalid or exceeds the 512 MiB safety limit")
            with path.open("rb") as handle:
                source = handle.read(MAX_PXP_BYTES + 1)
            if len(source) != size:
                raise ValueError("PXP changed while it was being read")
        except (OSError, ValueError) as exc:
            return PxpDocument(source_id, basename, "", 0, "", "igor2", version("igor2"),
                               issues=(PxpIssue("PXP", str(exc)),))
        sha256 = hashlib.sha256(source).hexdigest()
        issues: list[PxpIssue] = []
        try:
            records, _filesystem = packed.load(io.BytesIO(source))
        except (AssertionError, KeyError, TypeError, ValueError, IndexError, struct.error, OverflowError) as exc:
            records = _record_stream(source, issues)
            if issues or not records:
                issues.insert(0, PxpIssue("PXP", f"igor2 full parse failed: {type(exc).__name__}; valid records were salvaged"))
        if not records:
            return PxpDocument(source_id, basename, sha256, size, "", "igor2", version("igor2"),
                               issues=tuple(issues or [PxpIssue("PXP", "No readable records")]),
                               source_bytes=source)

        folder = ["root"]
        folders = ["root"]
        waves: list[PxpWave] = []
        procedures: list[str] = []
        recreation = ""
        command_history: list[str] = []
        igor_version = ""
        for record in records:
            if isinstance(record, FolderStartRecord):
                folder.append(_decode(record.null_terminated_text))
                folders.append(":".join(folder))
            elif isinstance(record, FolderEndRecord):
                if len(folder) > 1:
                    folder.pop()
            elif isinstance(record, WaveRecord):
                try:
                    body = record.wave["wave"]
                    header = body["wave_header"]
                    name = _decode(header["bname"])
                    data = np.asarray(body["wData"])
                    if data.dtype.kind == "S":
                        # igor2 reshapes text strings in C order, but Igor stores
                        # dimensions in column-major order, just like numeric waves.
                        if data.ndim > 1:
                            data = data.ravel(order="C").reshape(data.shape, order="F")
                        data = np.vectorize(_decode, otypes=[str])(data)
                    if data.dtype.hasobject:
                        data = np.vectorize(_decode, otypes=[str])(data)
                    data.setflags(write=False)
                    offsets = np.atleast_1d(header.get("sfB", [0.0] * data.ndim))
                    deltas = np.atleast_1d(header.get("sfA", [1.0] * data.ndim))
                    scales = tuple((float(offsets[min(i, len(offsets) - 1)]),
                                    float(deltas[min(i, len(deltas) - 1)]))
                                   for i in range(data.ndim))
                    units = header.get("dimUnits")
                    dim_units = tuple(_decode(units[i]) if units is not None else ""
                                      for i in range(data.ndim))
                    wave_path = ":".join([*folder, name])
                    labels = body.get("labels", ())
                    dim_names = tuple(_decode(labels[i][0]) if i < len(labels) and labels[i] else "" for i in range(data.ndim))
                    dim_labels = tuple(tuple(_decode(v) for v in labels[i][1:]) if i < len(labels) and labels[i] else () for i in range(data.ndim))
                    waves.append(PxpWave(wave_path, wave_path, name, data.dtype.str,
                                         tuple(data.shape), data, scales, dim_units,
                                         _decode(header.get("dataUnits", b"")), _decode(body.get("note", b"")), dim_labels, dim_names))
                except (KeyError, TypeError, ValueError, IndexError) as exc:
                    issues.append(PxpIssue("wave", f"Wave metadata could not be read: {type(exc).__name__}"))
            elif isinstance(record, RecreationRecord):
                recreation += record.text.decode("utf-8", "replace").replace("\r", "\n") + "\n"
            elif isinstance(record, ProcedureRecord):
                procedures.append(record.text.decode("utf-8", "replace").replace("\r", "\n"))
            elif isinstance(record, (HistoryRecord, GetHistoryRecord)):
                command_history.extend(_decode(record.text).replace("\r", "\n").splitlines())
        match = re.search(r"IGORVersion=([0-9.]+)", recreation)
        if match:
            igor_version = match[1]
        try:
            graphs, tables = parse_recreation(recreation)
            from materials_data_studio.tools.pxp_style import bind_palette_waves
            graphs = bind_palette_waves(waves, graphs)
        except (TypeError, ValueError, IndexError) as exc:
            issues.append(PxpIssue("graphs", f"Window recreation could not be read: {type(exc).__name__}"))
            graphs, tables = (), ()
        wave_paths = {wave.igor_path for wave in waves}
        wave_names = Counter(wave.name for wave in waves)
        def present(reference: str) -> bool:
            reference = reference.strip().strip("'")
            return wave_names[reference] == 1 or (reference if reference.startswith("root:") else f"root:{reference}") in wave_paths
        for graph in graphs:
            for trace in graph.traces:
                if not present(trace.y_wave):
                    issues.append(PxpIssue(graph.name, f"Missing wave: {trace.y_wave}"))
                if trace.x_wave and not present(trace.x_wave):
                    issues.append(PxpIssue(graph.name, f"Missing X wave: {trace.x_wave}"))
            for image in graph.images:
                if not present(image.wave):
                    issues.append(PxpIssue(graph.name, f"Missing image wave: {image.wave}"))
        status: str = "FULL" if graphs and not issues and all(
            graph.status == "Fully supported" for graph in graphs
        ) else "GRAPH_PARTIAL" if graphs else "DATA_ONLY" if waves or procedures or command_history else "OPEN_FAILED"
        if issues and waves and not graphs:
            status = "PARTIAL"
        from materials_data_studio.tools.pxp_tables import parse_tables
        return PxpDocument(source_id, basename, sha256, size, igor_version, "igor2",
                           version("igor2"), tuple(waves), tuple(folders), graphs, tables,
                           tuple(procedures), tuple(issues), status, source, recreation, parse_tables(recreation), tuple(command_history))
