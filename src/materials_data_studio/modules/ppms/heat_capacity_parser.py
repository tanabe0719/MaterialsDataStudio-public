"""Parser for Quantum Design PPMS Heat Capacity DAT files."""

from __future__ import annotations

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
class HeatCapacityMetadata:
    """Semantically extracted PPMS header values."""

    sample_mass_mg: float | None = None
    sample_mass_error_mg: float | None = None
    molar_mass_g_mol: float | None = None
    atoms_per_formula_unit: float | None = None
    title: str = ""
    application: str = ""


@dataclass(slots=True)
class HeatCapacityParseResult:
    """Immutable-aligned numerical arrays and preserved import messages."""

    timestamp_s: NDArray[np.float64]
    field_oe: NDArray[np.float64]
    temperature_k: NDArray[np.float64]
    heat_capacity_uj_k: NDArray[np.float64]
    heat_capacity_error_uj_k: NDArray[np.float64]
    source_row_index: NDArray[np.int64]
    fit_deviation: NDArray[np.float64]
    sample_coupling: NDArray[np.float64]
    system_status: NDArray[np.float64]
    comments: list[str]
    metadata: HeatCapacityMetadata
    messages: list[str] = field(default_factory=list)
    skipped_rows: int = 0


def _header_metadata(rows: list[list[str]]) -> HeatCapacityMetadata:
    result = HeatCapacityMetadata()
    for row in rows:
        joined = normalize_ppms_name(" ".join(row))
        values = [parse_float(cell) for cell in row]
        number = next((value for value in values[1:] if value is not None), None)
        if "sample mass error" in joined and number is not None:
            result.sample_mass_error_mg = number
        elif "sample mass" in joined and number is not None:
            result.sample_mass_mg = number
        elif ("formula weight" in joined or "molwght" in joined) and number is not None:
            result.molar_mass_g_mol = number
        elif "atoms per formula unit" in joined and number is not None:
            result.atoms_per_formula_unit = number
        elif "title" in joined:
            result.title = next((cell for cell in row[1:] if cell.strip()), result.title)
        elif "application" in joined or joined.startswith("appname"):
            result.application = " ".join(cell for cell in row[1:] if cell.strip())
    return result


def parse_heat_capacity_text(text: str) -> HeatCapacityParseResult:
    """Parse a PPMS Heat Capacity export without fixed line assumptions."""
    header_rows, headings, rows = split_ppms_sections(text)
    field_col = find_heading(headings, "field")
    temp_col = find_heading(headings, "sample temp")
    cp_col = next(
        (
            index
            for index, heading in enumerate(headings)
            if "samp hc" in normalize_ppms_name(heading)
            and "err" not in normalize_ppms_name(heading)
        ),
        None,
    )
    if field_col is None or temp_col is None or cp_col is None:
        raise DataImportError(
            "Heat Capacity data requires Field, Sample Temp, and Samp HC columns."
        )
    error_col = find_heading(headings, "samp hc", "err")
    timestamp_col = find_heading(headings, "time stamp")
    deviation_col = find_heading(headings, "fit deviation")
    coupling_col = find_heading(headings, "sample coupling")
    status_col = find_heading(headings, "system status")
    comment_col = find_heading(headings, "comment")

    values: dict[str, list[float]] = {
        "timestamp": [],
        "field": [],
        "temperature": [],
        "cp": [],
        "error": [],
        "deviation": [],
        "coupling": [],
        "status": [],
    }
    source_rows: list[int] = []
    comments: list[str] = []
    messages: list[str] = []
    skipped = 0
    for source_index, row in enumerate(rows, start=1):
        required = (
            parse_float(row_value(row, field_col)),
            parse_float(row_value(row, temp_col)),
            parse_float(row_value(row, cp_col)),
        )
        if any(value is None for value in required):
            skipped += 1
            message = ", ".join(cell for cell in row if cell).strip()
            messages.append(message or f"Skipped empty data row {source_index}.")
            continue
        field_value, temperature, cp = required
        assert field_value is not None and temperature is not None and cp is not None
        optional = (
            (timestamp_col, "timestamp"),
            (error_col, "error"),
            (deviation_col, "deviation"),
            (coupling_col, "coupling"),
            (status_col, "status"),
        )
        values["field"].append(field_value)
        values["temperature"].append(temperature)
        values["cp"].append(cp)
        for column, key in optional:
            parsed = parse_float(row_value(row, column))
            values[key].append(np.nan if parsed is None else parsed)
        source_rows.append(source_index)
        comments.append(row_value(row, comment_col))
    if not source_rows:
        raise DataImportError("The Heat Capacity file contains no valid measurement rows.")
    return HeatCapacityParseResult(
        timestamp_s=np.asarray(values["timestamp"], dtype=float),
        field_oe=np.asarray(values["field"], dtype=float),
        temperature_k=np.asarray(values["temperature"], dtype=float),
        heat_capacity_uj_k=np.asarray(values["cp"], dtype=float),
        heat_capacity_error_uj_k=np.asarray(values["error"], dtype=float),
        source_row_index=np.asarray(source_rows, dtype=np.int64),
        fit_deviation=np.asarray(values["deviation"], dtype=float),
        sample_coupling=np.asarray(values["coupling"], dtype=float),
        system_status=np.asarray(values["status"], dtype=float),
        comments=comments,
        metadata=_header_metadata(header_rows),
        messages=messages,
        skipped_rows=skipped,
    )
