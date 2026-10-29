"""Parser for Quantum Design PPMS Electrical Transport DAT files."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from materials_data_studio.core.exceptions import DataImportError
from materials_data_studio.modules.ppms.parser_common import (
    find_heading,
    normalize_ppms_name,
    parse_float,
    row_value,
    split_ppms_sections,
)


@dataclass(slots=True)
class BridgeMetadata:
    bridge: int
    sample_name: str = ""
    cross_section: float | None = None
    length: float | None = None
    configured_unit: str = ""
    available: bool = False
    source_columns: list[str] = field(default_factory=list)
    selected_source: str = ""
    selected_kind: str = ""
    selected_unit: str = ""


@dataclass(slots=True)
class ResistivityParseResult:
    timestamp_s: NDArray[np.float64]
    temperature_k: NDArray[np.float64]
    field_oe: NDArray[np.float64]
    measurement_order: NDArray[np.int64]
    bridge_data: dict[int, dict[str, NDArray[np.float64]]]
    bridges: dict[int, BridgeMetadata]
    messages: list[str] = field(default_factory=list)
    skipped_rows: int = 0


def _header_bridges(rows: list[list[str]]) -> dict[int, BridgeMetadata]:
    bridges = {number: BridgeMetadata(number) for number in range(1, 5)}
    for row in rows:
        joined = normalize_ppms_name(" ".join(row))
        match = re.search(r"sample\s*([1-4])\s*(name|cross section|length|units?)", joined)
        if match is None:
            continue
        bridge = bridges[int(match.group(1))]
        label = match.group(2)
        candidate = row[1].strip() if len(row) > 1 else ""
        if label == "name":
            bridge.sample_name = candidate
        elif label == "cross section":
            bridge.cross_section = parse_float(candidate)
        elif label == "length":
            bridge.length = parse_float(candidate)
        else:
            bridge.configured_unit = candidate
    return bridges


def _column_unit(heading: str) -> str:
    normalized = normalize_ppms_name(heading)
    if "mohm cm" in normalized:
        return "mohm_cm"
    if "ohm m" in normalized:
        return "ohm_m"
    if "ohm cm" in normalized:
        return "ohm_cm"
    if "ohm" in normalized:
        return "ohm"
    return "unknown"


def _bridge_columns(headings: list[str], bridge: int) -> dict[str, tuple[int, str]]:
    found: dict[str, tuple[int, str]] = {}
    prefix = f"bridge {bridge}"
    for index, heading in enumerate(headings):
        normalized = normalize_ppms_name(heading)
        if prefix not in normalized:
            continue
        if "resistance" in normalized and "resistivity" not in normalized:
            found["resistance"] = (index, "ohm")
        elif "resistivity" in normalized:
            unit = _column_unit(heading)
            found[f"resistivity_{unit}"] = (index, unit)
        elif "std" in normalized and "dev" in normalized:
            found["standard_deviation"] = (index, _column_unit(heading))
        elif "excitation" in normalized:
            found["excitation"] = (index, "")
    return found


def _choose_source(
    columns: dict[str, tuple[int, str]], configured_unit: str
) -> tuple[str, str, str]:
    if "resistance" in columns:
        return "resistance", "resistance", "ohm"
    for unit in ("ohm_cm", "ohm_m", "mohm_cm"):
        key = f"resistivity_{unit}"
        if key in columns:
            return key, "resistivity", unit
    if (
        "resistivity_ohm" in columns
        and normalize_ppms_name(configured_unit) in {"ohm", "ohms"}
    ):
        return "resistivity_ohm", "resistance", "ohm"
    return "", "", ""


def parse_resistivity_text(text: str) -> ResistivityParseResult:
    """Parse PPMS bridge measurements while preserving source distinctions."""
    header_rows, headings, rows = split_ppms_sections(text)
    temperature_col = find_heading(headings, "temperature")
    field_col = find_heading(headings, "magnetic field")
    if temperature_col is None or field_col is None:
        raise DataImportError("Resistivity data requires Temperature and Magnetic Field columns.")
    timestamp_col = find_heading(headings, "time stamp")
    bridges = _header_bridges(header_rows)
    column_maps = {number: _bridge_columns(headings, number) for number in range(1, 5)}
    if not any(column_maps.values()):
        raise DataImportError("The file contains no Bridge 1–4 resistance or resistivity columns.")

    temperatures: list[float] = []
    fields: list[float] = []
    timestamps: list[float] = []
    orders: list[int] = []
    skipped = 0
    messages: list[str] = []
    bridge_values: dict[int, dict[str, list[float]]] = {
        number: {key: [] for key in columns}
        for number, columns in column_maps.items()
    }
    for source_index, row in enumerate(rows, start=1):
        temperature = parse_float(row_value(row, temperature_col))
        field_value = parse_float(row_value(row, field_col))
        if temperature is None or field_value is None:
            skipped += 1
            message = ", ".join(cell for cell in row if cell).strip()
            messages.append(message or f"Skipped empty data row {source_index}.")
            continue
        temperatures.append(temperature)
        fields.append(field_value)
        timestamp = parse_float(row_value(row, timestamp_col))
        timestamps.append(np.nan if timestamp is None else timestamp)
        orders.append(source_index)
        for bridge, columns in column_maps.items():
            for key, (column, _unit) in columns.items():
                value = parse_float(row_value(row, column))
                bridge_values[bridge][key].append(np.nan if value is None else value)

    if not temperatures:
        raise DataImportError("The Resistivity file contains no valid temperature/field rows.")
    arrays: dict[int, dict[str, NDArray[np.float64]]] = {}
    for bridge, values in bridge_values.items():
        arrays[bridge] = {
            key: np.asarray(column_values, dtype=float)
            for key, column_values in values.items()
        }
        metadata = bridges[bridge]
        metadata.source_columns = list(values)
        source, kind, unit = _choose_source(column_maps[bridge], metadata.configured_unit)
        metadata.selected_source = source
        metadata.selected_kind = kind
        metadata.selected_unit = unit
        metadata.available = bool(
            source
            and source in arrays[bridge]
            and np.any(np.isfinite(arrays[bridge][source]))
        )
        if source and source in arrays[bridge]:
            arrays[bridge]["validity_flag"] = np.isfinite(
                arrays[bridge][source]
            ).astype(float)
    if not any(bridge.available for bridge in bridges.values()):
        raise DataImportError("No bridge contains a valid selectable measurement source.")
    return ResistivityParseResult(
        np.asarray(timestamps, dtype=float),
        np.asarray(temperatures, dtype=float),
        np.asarray(fields, dtype=float),
        np.asarray(orders, dtype=np.int64),
        arrays,
        bridges,
        messages,
        skipped,
    )
