"""Shared semantic parsing helpers for Quantum Design PPMS text files."""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable

from materials_data_studio.core.exceptions import DataImportError


def normalize_ppms_name(value: str) -> str:
    """Normalize PPMS labels across encodings, punctuation, and plural forms."""
    text = (
        value.replace("µ", "u")
        .replace("μ", "u")
        .replace("�", "u")
        .replace("Ω", "ohm")
        .lower()
    )
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def csv_rows(lines: Iterable[str]) -> list[list[str]]:
    """Parse CSV records while retaining short informational rows."""
    return [[cell.strip() for cell in row] for row in csv.reader(lines)]


def split_ppms_sections(text: str) -> tuple[list[list[str]], list[str], list[list[str]]]:
    """Return header rows, data headings, and all data records."""
    lines = text.lstrip("\ufeff").splitlines()
    header_index = next(
        (index for index, line in enumerate(lines) if line.strip().lower() == "[header]"),
        None,
    )
    data_index = next(
        (index for index, line in enumerate(lines) if line.strip().lower() == "[data]"),
        None,
    )
    if data_index is None:
        raise DataImportError("The PPMS file does not contain a [Data] section.")
    if header_index is None:
        raise DataImportError("The PPMS file does not contain a [Header] section.")
    if header_index >= data_index:
        raise DataImportError("The [Header] section must precede [Data].")
    header_rows = csv_rows(lines[header_index + 1 : data_index])
    remaining = [line for line in lines[data_index + 1 :] if line.strip()]
    if not remaining:
        raise DataImportError("The [Data] section is empty.")
    headings = next(csv.reader([remaining[0]]))
    return header_rows, [heading.strip() for heading in headings], csv_rows(remaining[1:])


def parse_float(value: str) -> float | None:
    """Parse a finite-looking number without raising for status text."""
    try:
        number = float(value.strip())
    except (TypeError, ValueError):
        return None
    return number


def find_heading(headings: list[str], *semantic_parts: str) -> int | None:
    """Find the first column whose normalized label contains every part."""
    normalized_parts = tuple(normalize_ppms_name(part) for part in semantic_parts)
    for index, heading in enumerate(headings):
        normalized = normalize_ppms_name(heading)
        if all(part in normalized for part in normalized_parts):
            return index
    return None


def row_value(row: list[str], index: int | None) -> str:
    """Safely read a possibly missing field from a short PPMS row."""
    return row[index].strip() if index is not None and index < len(row) else ""
