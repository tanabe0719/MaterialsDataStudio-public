"""Numerical export formats shared by analysis modules."""

from __future__ import annotations

import csv
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np
from numpy.typing import ArrayLike, NDArray

from materials_data_studio.core.exceptions import AnalysisError

_ASCII_NAME = re.compile(r"[^A-Za-z0-9_]+")


def sanitize_column_name(name: str) -> str:
    """Convert a heading to an Igor- and ASCII-friendly identifier."""
    sanitized = _ASCII_NAME.sub("_", name.encode("ascii", "ignore").decode()).strip("_")
    if not sanitized:
        raise ValueError("A column name became empty after ASCII sanitization.")
    if sanitized[0].isdigit():
        sanitized = f"C_{sanitized}"
    return sanitized


def aligned_columns(columns: Mapping[str, ArrayLike]) -> tuple[list[str], NDArray[np.float64]]:
    """Validate equal-length numeric columns and return a dense matrix."""
    if not columns:
        raise AnalysisError("There are no columns to export.")
    names = [sanitize_column_name(name) for name in columns]
    arrays = [np.asarray(values, dtype=float).reshape(-1) for values in columns.values()]
    lengths = {len(array) for array in arrays}
    if len(lengths) != 1:
        raise AnalysisError("Combined export requires columns with equal lengths.")
    return names, np.column_stack(arrays)


def export_table(
    path: Path,
    columns: Mapping[str, ArrayLike],
    *,
    metadata: Sequence[str] = (),
) -> None:
    """Export CSV or tab-separated DAT/TSV using decimal points."""
    names, matrix = aligned_columns(columns)
    suffix = path.suffix.lower()
    if suffix not in {".csv", ".dat", ".tsv"}:
        raise AnalysisError("Table export supports .csv, .dat, and .tsv files.")
    delimiter = "," if suffix == ".csv" else "\t"
    with path.open("w", encoding="utf-8", newline="") as handle:
        for line in metadata:
            handle.write(f"# {line}\n")
        writer = csv.writer(handle, delimiter=delimiter, lineterminator="\n")
        writer.writerow(names)
        writer.writerows(matrix)


def export_itx(
    path: Path,
    columns: Mapping[str, ArrayLike],
    *,
    metadata: Sequence[str] = (),
) -> None:
    """Write an Igor Pro Text Wave with one numeric wave per column."""
    names, matrix = aligned_columns(columns)
    with path.open("w", encoding="ascii", newline="\n") as handle:
        handle.write("IGOR\n")
        if metadata:
            escaped = (
                " | ".join(metadata)
                .replace('"', "'")
                .encode("ascii", errors="replace")
                .decode("ascii")
            )
            handle.write(f'X Note="Materials Data Studio: {escaped}"\n')
        handle.write("WAVES/D " + " ".join(names) + "\n")
        handle.write("BEGIN\n")
        np.savetxt(handle, matrix, delimiter="\t", fmt="%.15g")
        handle.write("END\n")


def export_numeric(
    path: Path,
    columns: Mapping[str, ArrayLike],
    *,
    metadata: Sequence[str] = (),
) -> None:
    """Dispatch numerical export from the selected filename extension."""
    if path.suffix.lower() == ".itx":
        export_itx(path, columns, metadata=metadata)
    else:
        export_table(path, columns, metadata=metadata)
