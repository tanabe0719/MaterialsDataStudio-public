"""Validate nested NPZ allocation sizes before NumPy allocates any arrays."""

from __future__ import annotations

import io
import math
import zipfile

import numpy as np


def validate_npz(
    payload: bytes, *, maximum_bytes: int, maximum_member_bytes: int | None = None
) -> int:
    maximum_member_bytes = maximum_member_bytes or maximum_bytes
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
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
            with archive.open(item) as stream:
                version = np.lib.format.read_magic(stream)
                if version == (1, 0):
                    shape, _order, dtype = np.lib.format.read_array_header_1_0(stream)
                elif version == (2, 0):
                    shape, _order, dtype = np.lib.format.read_array_header_2_0(stream)
                else:
                    raise ValueError("Unsupported scientific array header")
                size = math.prod(shape) * dtype.itemsize
                if (
                    dtype.hasobject
                    or len(shape) > 32
                    or any(n < 0 for n in shape)
                    or size > maximum_member_bytes
                    or stream.tell() + size != item.file_size
                ):
                    raise ValueError("Unsafe or truncated scientific array")
        return total
