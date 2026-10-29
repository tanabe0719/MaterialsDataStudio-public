"""Bounded numeric-block parsing for Igor's LoadWave/G semantics.

Unlike delimited-text import, consecutive commas, tabs, and spaces are one
separator. Missing values therefore require an explicit NaN token. Each pair
of adjacent numeric rows with equal width starts a block; a width change or a
nonnumeric line ends it. This parser never opens a host path.
"""
from __future__ import annotations

import io
import re
from collections.abc import Iterator, Sequence

import numpy as np

from materials_data_studio.tools.graph_waves import (
    MAX_PARSED_COLUMNS,
    MAX_TEXT_CELLS,
    MAX_TEXT_LINE,
)

_NUMBER = re.compile(
    r"[+-]?(?:(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eEdD][+-]?[0-9]+)?|inf(?:inity)?|nan)",
    re.IGNORECASE,
)
_SEPARATORS = re.compile(r"[,\t ]+")


def _numeric_rows(text: str, first_line: int) -> Iterator[list[float] | None]:
    cells = 0
    source = io.StringIO(text, newline="")
    index = 0
    while line := source.readline(MAX_TEXT_LINE + 1):
        if len(line) > MAX_TEXT_LINE:
            raise ValueError("A general-text line exceeds the 4 MiB safety limit.")
        if index < first_line:
            index += 1
            continue
        index += 1
        if index == 1:
            line = line.removeprefix("\ufeff")
        content = line.strip(",\t \r\n")
        if not content:
            yield None
            continue
        # Bound splitting itself, not only the retained result of a huge row.
        tokens = _SEPARATORS.split(content, maxsplit=MAX_PARSED_COLUMNS)
        if len(tokens) > MAX_PARSED_COLUMNS:
            raise ValueError(f"A general-text record exceeds {MAX_PARSED_COLUMNS} fields.")
        cells += len(tokens)
        if cells > MAX_TEXT_CELLS:
            raise ValueError("General-text import exceeds the 5 million-cell safety limit.")
        if any(_NUMBER.fullmatch(token) is None for token in tokens):
            yield None
            continue
        yield [float(token.replace("D", "E").replace("d", "e")) for token in tokens]


def parse_general_text(
    text: str, tweaks: Sequence[int] = (0, 0, 0, 0, 0),
) -> list[np.ndarray]:
    """Return selected float64 matrices, one per numeric block in file order.

    ``tweaks`` is LoadWave/L's nameLine, firstLine, numLines, firstColumn,
    numColumns. Names are not read here. firstLine is where block *search*
    begins; zero means auto. An explicit numLines stops the entire import at
    that many rows or at the end of the first block, whichever comes first.
    Zero numColumns loads from firstColumn through the end of each block.
    """
    if len(tweaks) != 5 or any(
        isinstance(value, bool) or not isinstance(value, (int, np.integer)) or value < 0
        for value in tweaks
    ):
        raise ValueError("LoadWave/L needs five nonnegative integers")
    _, first_line, line_count, first_column, column_count = map(int, tweaks)
    blocks: list[np.ndarray] = []
    pending: list[float] | None = None
    block: list[list[float]] = []
    width = 0
    stop_column = 0

    def finish() -> None:
        if block:
            blocks.append(np.asarray(block, dtype=np.float64))

    for row in _numeric_rows(text, first_line):
        if block:
            if row is not None and len(row) == width:
                block.append(row[first_column:stop_column])
                if line_count and len(block) == line_count:
                    finish()
                    return blocks
                continue
            finish()
            if line_count:
                return blocks
            block = []
            pending = row
            continue
        if row is None:
            pending = None
            continue
        if pending is None or len(pending) != len(row):
            pending = row
            continue
        width = len(row)
        stop_column = first_column + column_count if column_count else width
        if first_column >= width or stop_column > width:
            raise ValueError("LoadWave requested columns outside the numeric block")
        block = [pending[first_column:stop_column]]
        pending = None
        if line_count == 1:
            finish()
            return blocks
        block.append(row[first_column:stop_column])
        if line_count == 2:
            finish()
            return blocks
    finish()
    if not blocks:
        raise ValueError("LoadWave found no numeric text block at or after the requested line")
    return blocks
