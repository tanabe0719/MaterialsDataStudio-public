"""Optional Rust kernels for bulk, already-tokenized numeric text."""

from __future__ import annotations

import ctypes
from collections.abc import Sequence
from functools import lru_cache
from typing import TextIO

import numpy as np
from numpy.typing import NDArray

# Small files do not benefit from the native call or staging buffers.
MIN_NATIVE_CELLS = 16_384
CHUNK_CELLS = 32_768


@lru_cache(maxsize=1)
def _library() -> ctypes.CDLL | None:
    from materials_data_studio.modules.xrd.native_xrd import library

    lib = library()
    if lib is None:
        return None
    size, pointer = ctypes.c_size_t, ctypes.c_void_p
    try:
        lib.mds_parse_numeric_cells.argtypes = [ctypes.c_char_p, size, pointer, size]
        lib.mds_parse_numeric_cells.restype = ctypes.c_bool
        lib.mds_write_numeric_rows.argtypes = [pointer, size, size, ctypes.c_uint8, pointer, size]
        lib.mds_write_numeric_rows.restype = size
    except AttributeError:
        return None  # A DLL from an earlier application build.
    return lib


def parse_cells(cells: Sequence[str]) -> NDArray[np.float64] | None:
    lib = _library()
    if lib is None:
        return None
    # Python float accepts Unicode digits/whitespace and numeric underscores.
    # Keep these and embedded delimiters on the general Python path.
    text = "\0".join(cells)
    if not text.isascii() or "_" in text or text.count("\0") != len(cells) - 1:
        return None
    payload = text.encode("ascii")
    values = np.empty(len(cells), dtype=np.float64)
    if lib.mds_parse_numeric_cells(payload, len(payload), values.ctypes.data, len(values)):
        return values
    return None


def write_rows(handle: TextIO, matrix: NDArray[np.float64], delimiter: str) -> bool:
    if matrix.size < MIN_NATIVE_CELLS or (lib := _library()) is None:
        return False
    rows_per_chunk = max(1, CHUNK_CELLS // matrix.shape[1])
    capacity = min(rows_per_chunk, len(matrix)) * matrix.shape[1] * 32
    buffer = ctypes.create_string_buffer(capacity)
    for first in range(0, len(matrix), rows_per_chunk):
        values = np.ascontiguousarray(matrix[first:first + rows_per_chunk])
        used = lib.mds_write_numeric_rows(values.ctypes.data, values.size, values.shape[1], ord(delimiter), buffer, capacity)
        if not used:
            raise OSError("Numeric export exceeded its output buffer.")
        handle.write(ctypes.string_at(buffer, used).decode("ascii"))
    return True
