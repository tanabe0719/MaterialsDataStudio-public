"""Tolerant XRD text parser with explicit warning accounting."""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from materials_data_studio.core.exceptions import DataImportError

_AUTO_SPLIT = re.compile(r"[\s,;]+")
_RASX_PROFILE = "data0/profile0.txt"
_MAX_RASX_PROFILE_BYTES = 128 * 1024 * 1024


@dataclass(slots=True)
class XrdParseResult:
    """Parsed arrays and a complete import accounting summary."""

    x: NDArray[np.float64]
    intensity: NDArray[np.float64]
    warnings: list[str]
    valid_rows: int
    invalid_rows: int
    detected_columns: int
    ambiguous: bool
    intensity_error: NDArray[np.float64] | None = None


def _split_line(line: str, delimiter: str | None) -> list[str]:
    """Split a line with either an explicit or automatically detected delimiter."""
    if delimiter is None or delimiter == "auto":
        return [part for part in _AUTO_SPLIT.split(line.strip()) if part]
    if delimiter == "whitespace":
        return line.split()
    return [part.strip() for part in line.split(delimiter)]


def _parse_xrd_lines(
    lines: list[str],
    *,
    delimiter: str | None = None,
    x_column: int = 0,
    y_column: int = 1,
    sigma_column: int | None = None,
    force_two_columns: bool = False,
) -> XrdParseResult:
    """Parse diffraction rows that have already been decoded."""
    if x_column < 0 or y_column < 0 or x_column == y_column:
        raise DataImportError("Select two different non-negative column indices.")
    warnings: list[str] = []
    x_values: list[float] = []
    y_values: list[float] = []
    error_values: list[float] = []
    invalid_rows = 0
    max_columns = 0

    for line_number, raw_line in enumerate(lines, start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith(("#", "*")):
            continue
        parts = _split_line(stripped, delimiter)
        if max(x_column, y_column) >= len(parts):
            invalid_rows += 1
            warnings.append(
                f"Line {line_number}: expected column {max(x_column, y_column) + 1}, "
                f"found {len(parts)}."
            )
            continue
        try:
            x_value = float(parts[x_column])
            y_value = float(parts[y_column])
            sigma = float(parts[sigma_column]) if sigma_column is not None else None
        except (ValueError, IndexError):
            invalid_rows += 1
            warnings.append(f"Line {line_number}: selected columns are not numeric.")
            continue
        if not np.isfinite(x_value) or not np.isfinite(y_value):
            invalid_rows += 1
            warnings.append(f"Line {line_number}: selected values are not finite.")
            continue
        if sigma is not None and (not np.isfinite(sigma) or sigma <= 0):
            invalid_rows += 1
            warnings.append(f"Line {line_number}: sigma must be finite and positive.")
            continue
        max_columns = max(max_columns, len(parts))
        x_values.append(x_value)
        y_values.append(y_value)
        if sigma is not None:
            error_values.append(sigma)

    if not x_values:
        raise DataImportError("No valid numerical XRD rows were found.")
    return XrdParseResult(
        x=np.asarray(x_values, dtype=float),
        intensity=np.asarray(y_values, dtype=float),
        warnings=warnings,
        valid_rows=len(x_values),
        invalid_rows=invalid_rows,
        detected_columns=max_columns,
        ambiguous=max_columns > 2 and not force_two_columns,
        intensity_error=np.asarray(error_values) if sigma_column is not None else None,
    )


def parse_xrd_text(
    path: Path,
    *,
    delimiter: str | None = None,
    x_column: int = 0,
    y_column: int = 1,
    sigma_column: int | None = None,
) -> XrdParseResult:
    """Parse common two-column diffraction files without silently dropping rows."""
    try:
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError as exc:
        raise DataImportError(f"Could not read '{path.name}': {exc}") from exc
    return _parse_xrd_lines(
        lines,
        delimiter=delimiter,
        x_column=x_column,
        y_column=y_column,
        sigma_column=sigma_column,
    )


def parse_xrd_rasx(path: Path) -> XrdParseResult:
    """Read Rigaku-style RASX Profile0.txt data without extracting the ZIP."""
    try:
        with zipfile.ZipFile(path, mode="r") as archive:
            matches = [
                info
                for info in archive.infolist()
                if info.filename.replace("\\", "/").casefold() == _RASX_PROFILE
            ]
            if len(matches) != 1:
                raise DataImportError(
                    "The RASX archive must contain exactly one "
                    "'Data0/Profile0.txt' member."
                )
            profile = matches[0]
            if profile.flag_bits & 0x1:
                raise DataImportError("Encrypted RASX profiles are not supported.")
            if profile.file_size > _MAX_RASX_PROFILE_BYTES:
                raise DataImportError(
                    "The RASX Profile0.txt member exceeds the 128 MiB safety limit."
                )
            payload = archive.read(profile)
    except DataImportError:
        raise
    except (OSError, KeyError, RuntimeError, zipfile.BadZipFile) as exc:
        raise DataImportError(f"Could not read RASX archive '{path.name}': {exc}") from exc
    lines = payload.decode("utf-8-sig", errors="replace").splitlines()
    return _parse_xrd_lines(
        lines,
        x_column=0,
        y_column=1,
        force_two_columns=True,
    )
