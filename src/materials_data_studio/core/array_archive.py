"""Validate bounded ZIP members containing scientific NumPy arrays."""

from __future__ import annotations

import io
import math
import zipfile
from typing import NamedTuple, Protocol, cast

import numpy as np
from numpy.typing import NDArray


class _NpyStream(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...
    def tell(self) -> int: ...


class ArrayHeader(NamedTuple):
    shape: tuple[int, ...]
    fortran: bool
    dtype: np.dtype[np.generic]


def _members(archive: zipfile.ZipFile, maximum_bytes: int, maximum_member_bytes: int) -> tuple[list[zipfile.ZipInfo], int]:
    members = archive.infolist()
    names = [item.filename for item in members]
    if len(members) > 20_000 or len(set(names)) != len(names):
        raise ValueError("Too many or duplicate array archive entries")
    total = sum(item.file_size for item in members)
    if total > maximum_bytes:
        raise ValueError("Expanded scientific arrays exceed the memory safety limit")
    for item in members:
        if (
            item.flag_bits & 1
            or not item.filename.endswith(".npy")
            or any(value in item.filename for value in ("/", "\\", ":"))
            or item.file_size > maximum_member_bytes
        ):
            raise ValueError("Unsafe scientific array archive entry")
    return members, total


def _header(stream: _NpyStream, file_size: int, maximum_bytes: int) -> ArrayHeader:
    version = np.lib.format.read_magic(stream)
    if version == (1, 0):
        shape, fortran, dtype = np.lib.format.read_array_header_1_0(stream)
    elif version == (2, 0):
        shape, fortran, dtype = np.lib.format.read_array_header_2_0(stream)
    else:
        raise ValueError("Unsupported scientific array header")
    size = math.prod(shape) * dtype.itemsize
    if (
        dtype.hasobject
        or len(shape) > 32
        or any(n < 0 for n in shape)
        or size > maximum_bytes
        or stream.tell() + size != file_size
    ):
        raise ValueError("Unsafe or truncated scientific array")
    return ArrayHeader(shape, fortran, dtype)


def inspect_npz(
    payload: bytes, *, maximum_bytes: int, maximum_member_bytes: int | None = None, verify_crc: bool = False,
) -> tuple[int, dict[str, ArrayHeader]]:
    """Inspect NPY headers and optionally check all member CRCs."""
    limit = maximum_member_bytes if maximum_member_bytes is not None else maximum_bytes
    headers: dict[str, ArrayHeader] = {}
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members, total = _members(archive, maximum_bytes, limit)
        for item in members:
            with archive.open(item) as stream:
                headers[item.filename.removesuffix(".npy")] = _header(stream, item.file_size, limit)
                if verify_crc:
                    while stream.read(1024 * 1024):
                        pass
    return total, headers


def validate_npz(
    payload: bytes, *, maximum_bytes: int, maximum_member_bytes: int | None = None, verify_crc: bool = False,
) -> int:
    total, _ = inspect_npz(payload, maximum_bytes=maximum_bytes, maximum_member_bytes=maximum_member_bytes,
                           verify_crc=verify_crc)
    return total


def load_npz_arrays(
    payload: bytes, *, maximum_bytes: int, maximum_member_bytes: int,
) -> tuple[int, dict[str, NDArray[np.generic]]]:
    """Validate sizes and read eagerly, using a single stream per NPY member."""
    arrays: dict[str, NDArray[np.generic]] = {}
    chunk_bytes = 64 * 1024
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members, total = _members(archive, maximum_bytes, maximum_member_bytes)
        for item in members:
            with cast(zipfile.ZipExtFile, archive.open(item)) as stream:
                shape, fortran, dtype = _header(stream, item.file_size, maximum_member_bytes)
                array = np.empty(shape, dtype=dtype, order="F" if fortran else "C")
                raw = memoryview(array.ravel(order="K").view(np.uint8))
                for start in range(0, len(raw), chunk_bytes):
                    block = raw[start:start + chunk_bytes]
                    if stream.readinto(block) != len(block):
                        raise ValueError("Truncated scientific array")
                arrays[item.filename.removesuffix(".npy")] = array
    return total, arrays
