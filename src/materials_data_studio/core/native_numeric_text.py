"""Optional Rust kernels for bulk numeric text input and output."""

from __future__ import annotations

import ctypes
from collections.abc import Sequence
from functools import lru_cache
from threading import Event
from typing import TextIO, overload

import numpy as np
from numpy.typing import NDArray

# Small files do not benefit from the native call or staging buffers.
MIN_NATIVE_CELLS = 16_384
CHUNK_CELLS = 32_768
_STOP = ctypes.CFUNCTYPE(ctypes.c_bool)


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


@lru_cache(maxsize=1)
def _row_library() -> ctypes.CDLL | None:
    lib = _library()
    if lib is None:
        return None
    size, pointer = ctypes.c_size_t, ctypes.c_void_p
    try:
        lib.mds_index_numeric_rows.argtypes = [
            ctypes.c_char_p, size, ctypes.c_uint8, ctypes.c_uint8, pointer, size, pointer, size, pointer, _STOP,
        ]
        lib.mds_index_numeric_rows.restype = ctypes.c_bool
        lib.mds_extract_numeric_rows.argtypes = [
            ctypes.c_char_p, size, pointer, size, ctypes.c_uint8, ctypes.c_bool, pointer, size, size, size, pointer,
        ]
        lib.mds_extract_numeric_rows.restype = None
    except AttributeError:
        return None
    return lib


class IndexedNumericRows(Sequence[list[str]]):
    """Compact row boundaries; decode cells only when a caller needs text."""

    def __init__(self, data: bytes, offsets: NDArray[np.uint32], delimiter: str, width: int,
                 start: int, numeric: list[bool], fortran_exponents: bool) -> None:
        self.data, self.offsets, self.delimiter = data, offsets, delimiter
        self.width, self.start, self.numeric = width, start, numeric
        self.fortran_exponents = fortran_exponents

    def __len__(self) -> int:
        return len(self.offsets) - 1

    @overload
    def __getitem__(self, index: int) -> list[str]: ...

    @overload
    def __getitem__(self, index: slice) -> list[list[str]]: ...

    def __getitem__(self, index: int | slice) -> list[str] | list[list[str]]:
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self)))]
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        row = self.data[int(self.offsets[index]):int(self.offsets[index + 1])].decode("ascii").rstrip("\r\n")
        return row.split() if self.delimiter == " " else [cell.strip() for cell in row.split(self.delimiter)]

    def numbers(self, start: int, columns: list[int], cancelled: Event | None = None) -> NDArray[np.float64]:
        lib = _row_library()
        assert lib is not None
        indices = np.asarray(columns, dtype=np.uint32)
        count = len(self) - start
        values = np.empty((len(indices), count), dtype=np.float64)
        if not len(indices):
            return values
        rows_per_chunk = max(1, CHUNK_CELLS // self.width)
        for offset in range(0, count, rows_per_chunk):
            if cancelled is not None and cancelled.is_set():
                raise InterruptedError("Text parsing stopped")
            begin = start + offset
            length = min(rows_per_chunk, count - offset)
            lib.mds_extract_numeric_rows(
                self.data, len(self.data), self.offsets.ctypes.data + begin * self.offsets.itemsize,
                length, ord(self.delimiter), self.fortran_exponents, indices.ctypes.data, len(indices),
                count, offset, values.ctypes.data,
            )
        if cancelled is not None and cancelled.is_set():
            raise InterruptedError("Text parsing stopped")
        return values


def index_numeric_rows(text: str, delimiter: str, max_columns: int, *, graph: bool = True,
                       cancelled: Event | None = None) -> IndexedNumericRows | None:
    if cancelled is not None and cancelled.is_set():
        raise InterruptedError("Text parsing stopped")
    if len(text) < 128 * 1024 or len(text) > 256 * 1024**2 or not text.isascii() or (lib := _row_library()) is None:
        return None
    data = text.encode("ascii")
    count = data.count(b"\n") + (not data.endswith(b"\n"))
    if not 1 <= count <= 2_500_000:
        return None
    offsets = np.empty(count + 1, dtype=np.uint32)
    kinds = np.ones(max_columns, dtype=np.uint8)
    result = np.zeros(2, dtype=np.uint32)
    stop = _STOP(cancelled.is_set if cancelled is not None else lambda: False)
    ok = lib.mds_index_numeric_rows(data, len(data), ord(delimiter), int(not graph),
                                    offsets.ctypes.data, len(offsets), kinds.ctypes.data, max_columns,
                                    result.ctypes.data, stop)
    if cancelled is not None and cancelled.is_set():
        raise InterruptedError("Text parsing stopped")
    if not ok:
        return None
    width, start = map(int, result)
    return IndexedNumericRows(data, offsets, delimiter, width, start,
                              [bool(kind) for kind in kinds[:width]], graph)
