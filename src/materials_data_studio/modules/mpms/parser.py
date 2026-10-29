"""Quantum Design MPMS dc.dat parsing with legacy fallback columns."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from materials_data_studio.core.exceptions import DataImportError


@dataclass(slots=True)
class MpmsParseResult:
    """Parsed MPMS arrays and import diagnostics."""

    time_s: NDArray[np.float64]
    field_oe: NDArray[np.float64]
    temperature_k: NDArray[np.float64]
    long_moment_emu: NDArray[np.float64]
    sample_weight_mg: float
    warnings: list[str]
    valid_rows: int
    invalid_rows: int
    used_legacy_columns: bool


def _normalized_heading(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def find_mpms_header(path: Path) -> tuple[float, int]:
    """Return sample weight and the zero-based column-header line index."""
    sample_weight: float | None = None
    data_marker: int | None = None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for index, line in enumerate(handle):
                if "INFO, WEIGHT" in line:
                    parts = next(csv.reader([line]))
                    if len(parts) >= 3:
                        try:
                            sample_weight = float(parts[2].strip())
                        except ValueError as exc:
                            raise DataImportError(
                                "The INFO, WEIGHT value is not numeric."
                            ) from exc
                if line.strip().casefold() == "[data]":
                    data_marker = index
                    break
    except OSError as exc:
        raise DataImportError(f"Could not read '{path.name}': {exc}") from exc
    if sample_weight is None:
        raise DataImportError("Could not read sample weight from INFO, WEIGHT.")
    if sample_weight <= 0:
        raise DataImportError("Sample weight must be positive.")
    if data_marker is None:
        raise DataImportError("Could not find the [Data] section.")
    return sample_weight, data_marker + 1


def _column_indices(headings: list[str]) -> tuple[tuple[int, int, int, int], bool]:
    normalized = [_normalized_heading(value) for value in headings]

    def locate(required: tuple[str, ...]) -> int | None:
        for index, heading in enumerate(normalized):
            if all(token in heading for token in required):
                return index
        return None

    detected = (
        locate(("time",)),
        locate(("magnetic", "field")),
        locate(("temperature",)),
        locate(("long", "moment")),
    )
    if all(index is not None for index in detected):
        return tuple(int(index) for index in detected), False  # type: ignore[arg-type]
    if len(headings) < 5:
        raise DataImportError(
            "MPMS columns could not be identified and legacy columns 0, 2, 3, 4 are unavailable."
        )
    return (0, 2, 3, 4), True


def parse_mpms_dc(path: Path) -> MpmsParseResult:
    """Parse required measurement columns and report every rejected row."""
    sample_weight, header_index = find_mpms_header(path)
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise DataImportError(f"Could not read '{path.name}': {exc}") from exc
    if header_index >= len(lines):
        raise DataImportError("The [Data] section has no column header.")
    headings = next(csv.reader([lines[header_index]]))
    indices, legacy = _column_indices(headings)
    values: tuple[list[float], list[float], list[float], list[float]] = ([], [], [], [])
    warnings: list[str] = []
    invalid = 0
    for line_number, line in enumerate(lines[header_index + 1 :], start=header_index + 2):
        if not line.strip():
            continue
        row = next(csv.reader([line]))
        if max(indices) >= len(row):
            invalid += 1
            warnings.append(f"Line {line_number}: required columns are missing.")
            continue
        try:
            parsed = [float(row[index].strip()) for index in indices]
        except ValueError:
            invalid += 1
            warnings.append(f"Line {line_number}: a required value is not numeric.")
            continue
        if not np.all(np.isfinite(parsed)):
            invalid += 1
            warnings.append(f"Line {line_number}: a required value is not finite.")
            continue
        for target, value in zip(values, parsed, strict=True):
            target.append(value)
    if not values[0]:
        raise DataImportError("No valid MPMS measurement rows were found.")
    return MpmsParseResult(
        time_s=np.asarray(values[0], dtype=float),
        field_oe=np.asarray(values[1], dtype=float),
        temperature_k=np.asarray(values[2], dtype=float),
        long_moment_emu=np.asarray(values[3], dtype=float),
        sample_weight_mg=sample_weight,
        warnings=warnings,
        valid_rows=len(values[0]),
        invalid_rows=invalid,
        used_legacy_columns=legacy,
    )

