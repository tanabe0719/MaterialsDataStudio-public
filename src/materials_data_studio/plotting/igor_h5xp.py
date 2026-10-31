"""WaveMetrics-compatible HDF5 packed-experiment writer for XY graphs."""

from __future__ import annotations

import os
import re
import unicodedata
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import h5py
import numpy as np
from numpy.typing import NDArray

from materials_data_studio.core.models import PlotStyle
from materials_data_studio.plotting.igor_export import (
    GraphExportState,
    GraphExportTrace,
    label_anchor,
)
from materials_data_studio.version import __version__

IGOR_REQUIRED_VERSION = 9.0
IGOR_PLATFORM = "Windows"
_MAX_OBJECT_NAME = 31
_VALID_NAME = re.compile(r"[^A-Za-z0-9_]+")
_IGOR_NUMERIC_TYPES: dict[tuple[str, int], int] = {
    ("f", 4): 0x02,
    ("f", 8): 0x04,
    ("i", 1): 0x08,
    ("i", 2): 0x10,
    ("i", 4): 0x20,
    ("i", 8): 0x80,
    ("u", 1): 0x08 | 0x40,
    ("u", 2): 0x10 | 0x40,
    ("u", 4): 0x20 | 0x40,
    ("u", 8): 0x80 | 0x40,
}


class IgorExportError(RuntimeError):
    """Raised when an experiment cannot be produced or validated."""


@dataclass(slots=True)
class IgorExportResult:
    """Result of a completed, validated packed-experiment export."""

    success: bool
    destination: Path
    warnings: list[str] = field(default_factory=list)
    wave_count: int = 0
    graph_count: int = 0


def sanitize_igor_name(value: str, *, fallback: str = "MDSObject") -> str:
    """Create a conservative classic Igor object name from untrusted text."""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = _VALID_NAME.sub("_", ascii_value).strip("_")
    if not cleaned:
        cleaned = fallback
    if cleaned[0].isdigit():
        cleaned = f"w_{cleaned}"
    return cleaned[:_MAX_OBJECT_NAME]


def unique_igor_name(value: str, used: set[str], *, fallback: str = "wave") -> str:
    """Allocate a case-insensitively unique Igor name."""
    base = sanitize_igor_name(value, fallback=fallback)
    candidate = base
    suffix = 2
    while candidate.casefold() in used:
        tail = f"_{suffix}"
        candidate = f"{base[: _MAX_OBJECT_NAME - len(tail)]}{tail}"
        suffix += 1
    used.add(candidate.casefold())
    return candidate


def escape_igor_string(value: str) -> str:
    """Escape user-controlled UTF-8 text for an Igor literal string."""
    cleaned = value.replace("\x00", "")
    return (
        cleaned.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )


def igor_rgb(color: str) -> tuple[int, int, int]:
    """Convert #RGB/#RRGGBB to Igor's 16-bit RGB components."""
    text = color.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(character * 2 for character in text)
    if len(text) != 6 or any(character not in "0123456789abcdefABCDEF" for character in text):
        raise ValueError(f"Unsupported color value: {color!r}")
    return (
        int(text[0:2], 16) * 257,
        int(text[2:4], 16) * 257,
        int(text[4:6], 16) * 257,
    )


class IgorStyleMapper:
    """Central mapping from Materials Data Studio styles to ModifyGraph commands."""

    _LINE_STYLES: ClassVar[dict[str, int]] = {"-": 0, "--": 3, ":": 1, "-.": 4}
    _MARKERS: ClassVar[dict[str, int]] = {
        ".": 2,
        "o": 19,
        "s": 16,
        "t": 17,
        "^": 17,
        "v": 18,
        "d": 15,
        "+": 0,
        "x": 1,
        "star": 14,
        "*": 14,
        "|": 10,
    }

    @classmethod
    def commands(cls, trace_name: str, style: PlotStyle, warnings: list[str]) -> list[str]:
        line_visible = style.line_style.lower() != "none" and style.line_width > 0
        marker_visible = style.marker.lower() != "none" and style.marker_size > 0
        if line_visible and marker_visible:
            mode = 4
        elif marker_visible:
            mode = 3
        else:
            mode = 0
        try:
            color = igor_rgb(style.color)
        except ValueError:
            color = (0, 0, 0)
            warnings.append(f"Trace {trace_name}: invalid color {style.color!r}; black was used.")
        modifiers = [
            f"mode({trace_name})={mode}",
            f"lSize({trace_name})={max(0.0, float(style.line_width)) if line_visible else 0:.6g}",
            f"rgb({trace_name})=({color[0]},{color[1]},{color[2]})",
        ]
        if line_visible:
            line_style = cls._LINE_STYLES.get(style.line_style)
            if line_style is None:
                line_style = 0
                warnings.append(
                    f"Trace {trace_name}: line style {style.line_style!r} was mapped to solid."
                )
            modifiers.append(f"lStyle({trace_name})={line_style}")
        if marker_visible:
            marker = cls._MARKERS.get(style.marker)
            if marker is None:
                marker = 19
                warnings.append(
                    f"Trace {trace_name}: marker {style.marker!r} was mapped to a filled circle."
                )
            modifiers.extend(
                [f"marker({trace_name})={marker}", f"msize({trace_name})={style.marker_size:.6g}", f"mrkThick({trace_name})={style.marker_edge_width:.6g}"]
            )
            if style.marker_edge_color:
                try:
                    edge = igor_rgb(style.marker_edge_color)
                    modifiers.append(f"mrkStrokeRGB({trace_name})=({edge[0]},{edge[1]},{edge[2]})")
                except ValueError:
                    warnings.append(f"Trace {trace_name}: invalid marker edge color was ignored.")
        return ["ModifyGraph " + ",".join(modifiers)]


class IgorH5xpExporter:
    """Write one XY graph as an atomic, validated HDF5 packed experiment."""

    def export_graph(self, graph_state: GraphExportState, destination: Path) -> IgorExportResult:
        target = Path(destination)
        if target.suffix.lower() != ".h5xp":
            raise IgorExportError("Igor packed experiment output must use the .h5xp extension.")
        if not graph_state.traces:
            raise IgorExportError("The current graph has no visible traces to send.")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f"{target.name}.tmp")
        warnings: list[str] = []
        try:
            wave_count = self._write(temporary, graph_state, warnings)
            self.validate(temporary, expected_wave_count=wave_count)
            os.replace(temporary, target)
        except Exception as exc:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
            if isinstance(exc, IgorExportError):
                raise
            raise IgorExportError(f"Could not create Igor experiment: {exc}") from exc
        return IgorExportResult(True, target, warnings, wave_count, 1)

    def _write(self, path: Path, graph_state: GraphExportState, warnings: list[str]) -> int:
        used_folders: set[str] = set()
        module_name = unique_igor_name(graph_state.module, used_folders, fallback="Module")
        graph_folder = unique_igor_name(graph_state.title, set(), fallback="Graph")
        graph_name = sanitize_igor_name(f"MDS_{module_name}", fallback="MDSGraph")
        used_waves: set[str] = set()
        wave_pairs: list[tuple[str, str, GraphExportTrace]] = []
        with h5py.File(path, "w") as handle:
            handle.attrs["IGORRequiredVersion"] = np.float64(IGOR_REQUIRED_VERSION)
            platform = IGOR_PLATFORM.encode("utf-8")
            handle.attrs.create(
                "IGORPlatform",
                platform,
                dtype=h5py.string_dtype(encoding="utf-8", length=len(platform)),
            )
            folder = handle.require_group(f"Packed Data/MDS/{module_name}/{graph_folder}")
            for index, trace in enumerate(graph_state.traces, start=1):
                base = unique_igor_name(trace.name, used_waves, fallback=f"Trace{index}")
                x_name = unique_igor_name(f"{base}_X", used_waves, fallback=f"X{index}")
                y_name = unique_igor_name(f"{base}_Y", used_waves, fallback=f"Y{index}")
                note = f"Materials Data Studio {__version__}\rTrace={trace.name}\rRole={trace.role}"
                self._write_wave(
                    folder, x_name, trace.x, trace.x_unit or graph_state.x_unit, note
                )
                self._write_wave(
                    folder, y_name, trace.y, trace.y_unit or graph_state.y_unit, note
                )
                wave_pairs.append((x_name, y_name, trace))
            procedures = self._recreation_procedures(
                graph_state,
                module_name,
                graph_folder,
                graph_name,
                wave_pairs,
                warnings,
            )
            recreation = handle.require_group("Recreation")
            recreation.create_dataset(
                "Recreation Procedures",
                data=procedures,
                dtype=h5py.string_dtype(encoding="utf-8"),
            )
        return len(wave_pairs) * 2

    @staticmethod
    def _write_wave(
        group: h5py.Group,
        name: str,
        values: NDArray[np.generic],
        unit: str,
        note: str,
    ) -> None:
        array = np.asarray(values)
        if array.ndim != 1:
            raise IgorExportError(f"Wave {name} is not one-dimensional.")
        if array.dtype.byteorder == ">":
            array = array.astype(array.dtype.newbyteorder("<"), copy=False)
        type_code = _IGOR_NUMERIC_TYPES.get((array.dtype.kind, array.dtype.itemsize))
        if type_code is None:
            raise IgorExportError(f"Wave {name} has unsupported dtype {array.dtype}.")
        dataset = group.create_dataset(name, data=array)
        dataset.attrs["IGORWaveType"] = np.int32(type_code)
        dataset.attrs["IGORWaveScaling"] = np.asarray(((0.0, 0.0), (1.0, 0.0)), dtype=np.float64)
        if unit:
            text_type = h5py.string_dtype(encoding="utf-8")
            dataset.attrs.create("IGORWaveUnits", np.asarray((unit, ""), dtype=text_type))
        safe_note = note.replace("\x00", "")
        if safe_note:
            encoded = safe_note.encode("utf-8")
            note_type = h5py.string_dtype(encoding="utf-8", length=len(encoded))
            dataset.attrs.create("IGORWaveNote", encoded, dtype=note_type)

    @staticmethod
    def _recreation_procedures(
        state: GraphExportState,
        module_name: str,
        graph_folder: str,
        graph_name: str,
        wave_pairs: list[tuple[str, str, GraphExportTrace]],
        warnings: list[str],
    ) -> str:
        prefix = f"root:MDS:{module_name}:{graph_folder}"
        first_x, first_y, _first_trace = wave_pairs[0]
        lines = [
            f"{graph_name}()",
            f"Window {graph_name}() : Graph",
            "\tPauseUpdate; Silent 1",
            f"\tDisplay /W=(80,60,800,560) {prefix}:{first_y} vs {prefix}:{first_x}",
        ]
        for x_name, y_name, _trace in wave_pairs[1:]:
            lines.append(f"\tAppendToGraph {prefix}:{y_name} vs {prefix}:{x_name}")
        for _x_name, y_name, trace in wave_pairs:
            lines.extend(
                f"\t{command}"
                for command in IgorStyleMapper.commands(y_name, trace.style, warnings)
            )
        # Keep Igor's remaining graph defaults, while making the transferred
        # graph match the application's four-sided, inward-tick presentation.
        lines.append("\tModifyGraph tick=2,mirror=1,tickUnit=1")
        lines.append(f'\tLabel bottom "{escape_igor_string(state.x_label)}"')
        lines.append(f'\tLabel left "{escape_igor_string(state.y_label)}"')
        if state.x_log_scale:
            lines.append("\tModifyGraph log(bottom)=1")
        if state.y_log_scale:
            lines.append("\tModifyGraph log(left)=1")
        if state.x_range is not None:
            lines.append(f"\tSetAxis bottom {state.x_range[0]:.17g},{state.x_range[1]:.17g}")
        if state.y_range is not None:
            lines.append(f"\tSetAxis left {state.y_range[0]:.17g},{state.y_range[1]:.17g}")
        if state.title:
            lines.append(f'\tDoWindow/T {graph_name},"{escape_igor_string(state.title)}"')
        for index, text in enumerate(state.texts):
            if not 0 <= text.trace < len(wave_pairs):
                raise IgorExportError("Text refers to an unavailable trace")
            _x, y_name, trace = wave_pairs[text.trace]
            if not 0 <= text.point < len(trace.y):
                raise IgorExportError("Text refers to an unavailable point")
            font = re.sub(r"['\\\r\n]", "", text.font)
            raw = f"\\F'{font}'\\Z{round(text.size):02d}" + text.text.replace("\\", "\\\\")
            color = igor_rgb(text.color)
            offset = text.gap / state.figure_height_points * 100
            lines.append(f'\tTag/N=MDSIndex{index}/A={label_anchor(text.angle)}/F=0/B=1/L=0/O={round(text.angle)}/G=({color[0]},{color[1]},{color[2]})/X=0/Y={offset:.9g} {y_name},{text.point},"{escape_igor_string(raw)}"')
        if state.legend_enabled:
            entries = []
            for _x_name, y_name, trace in wave_pairs:
                human_name = escape_igor_string(trace.name)
                entries.append(f"\\\\s({y_name}) {human_name}")
            legend = "\\r".join(entries)
            lines.append(f'\tLegend/C/N=legend0/J "{legend}"')
        lines.append("EndMacro")
        return "\r".join(lines) + "\r"

    @staticmethod
    def validate(path: Path, *, expected_wave_count: int | None = None) -> None:
        """Check the documented minimum structure before Igor is launched."""
        try:
            with h5py.File(path, "r") as handle:
                if "IGORRequiredVersion" not in handle.attrs:
                    raise IgorExportError("IGORRequiredVersion is missing.")
                if "IGORPlatform" not in handle.attrs:
                    raise IgorExportError("IGORPlatform is missing.")
                platform = handle.attrs["IGORPlatform"]
                if isinstance(platform, bytes):
                    platform = platform.decode("utf-8")
                if platform != IGOR_PLATFORM:
                    raise IgorExportError(f"Unsupported IGORPlatform value: {platform!r}.")
                packed_data = handle.get("Packed Data")
                if not isinstance(packed_data, h5py.Group) or "MDS" not in packed_data:
                    raise IgorExportError("Packed Data/MDS is missing.")
                if "Recreation/Recreation Procedures" not in handle:
                    raise IgorExportError("Recreation Procedures are missing.")
                wave_count = 0

                def count_wave(_name: str, item: h5py.Dataset | h5py.Group) -> None:
                    nonlocal wave_count
                    if isinstance(item, h5py.Dataset) and "IGORWaveType" in item.attrs:
                        wave_count += 1

                mds_group = handle.get("Packed Data/MDS")
                if not isinstance(mds_group, h5py.Group):
                    raise IgorExportError("Packed Data/MDS is not an HDF5 group.")
                mds_group.visititems(count_wave)
                if wave_count == 0:
                    raise IgorExportError("The experiment contains no Igor waves.")
                if expected_wave_count is not None and wave_count != expected_wave_count:
                    raise IgorExportError(
                        f"Expected {expected_wave_count} waves but validated {wave_count}."
                    )
        except OSError as exc:
            raise IgorExportError(f"The generated file is not valid HDF5: {exc}") from exc
