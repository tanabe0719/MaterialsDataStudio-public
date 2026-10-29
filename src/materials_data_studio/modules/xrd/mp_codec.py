"""Bounded, lossless float64/hkl storage for separately distributed MP indexes."""
from __future__ import annotations

import json
import struct
import zlib

import numpy as np

MAGIC = b"MDSMP2\0\0"
HEADER = struct.Struct("<IIII")
MAX_DECODED = 64 * 1024**2


def inflate(blob: bytes, limit: int) -> bytes:
    inflater = zlib.decompressobj()
    value = inflater.decompress(blob, limit + 1)
    if len(value) > limit or not inflater.eof or inflater.unused_data:
        raise ValueError("MP reference is corrupt or exceeds its decoded size limit")
    return value


def _put(number: int, target: bytearray) -> None:
    if not isinstance(number, int) or not 0 <= number < 2**63:
        raise ValueError("Unsupported hkl integer")
    while number >= 128:
        target.append((number & 127) | 128)
        number >>= 7
    target.append(number)


def _get(source) -> int:
    number = 0
    try:
        for shift in range(0, 63, 7):
            value = next(source)
            number |= (value & 127) << shift
            if value < 128:
                return number
    except StopIteration as exc:
        raise ValueError("Truncated MP hkl data") from exc
    raise ValueError("Invalid MP hkl integer")


def encode_pattern(pattern: dict) -> bytes:
    """Preserve every float64 bit and every hkl group; retain unknown schemas as JSON."""
    try:
        if set(pattern) != {"d", "intensities", "hkl"}:
            raise ValueError("Extended pattern schema")
        arrays = np.asarray([pattern["d"], pattern["intensities"]], dtype="<f8")
        count = len(pattern["d"])
        if arrays.shape != (2, count) or not 0 < count <= 20000:
            raise ValueError("Invalid reference shape")
        hkls = pattern["hkl"]
        if len(hkls) != count:
            raise ValueError("Extended hkl schema")
        packed = bytearray()
        for groups in hkls:
            _put(len(groups), packed)
            for group in groups:
                if set(group) != {"hkl", "multiplicity"} or not 1 <= len(group["hkl"]) <= 16:
                    raise ValueError("Extended hkl schema")
                _put(len(group["hkl"]), packed)
                for number in group["hkl"]:
                    _put(2 * number if number >= 0 else -2 * number - 1, packed)
                _put(group["multiplicity"], packed)
        shuffled = arrays.view("u1").reshape(-1, 8).T.copy().tobytes()
        numbers = zlib.compress(shuffled, 6)
        hkls = zlib.compress(packed, 6)
        return MAGIC + HEADER.pack(count, len(numbers), len(hkls), len(packed)) + numbers + hkls
    except (ValueError, TypeError, KeyError, IndexError):
        # A future/custom hkl field is never silently discarded by compaction.
        return zlib.compress(json.dumps(pattern, allow_nan=False).encode(), 6)


def decode_pattern(value, *, include_hkl: bool = True, validate_hkl: bool = False) -> dict:
    if not isinstance(value, bytes) or not value.startswith(MAGIC):
        raw = inflate(value, MAX_DECODED) if isinstance(value, bytes) else value
        if len(raw) > MAX_DECODED:
            raise ValueError("MP reference exceeds its decoded size limit")
        return json.loads(raw)
    if len(value) < len(MAGIC) + HEADER.size:
        raise ValueError("Truncated MP pattern header")
    count, numeric_size, hkl_size, hkl_raw_size = HEADER.unpack_from(value, len(MAGIC))
    offset = len(MAGIC) + HEADER.size
    if not 0 < count <= 20000 or offset + numeric_size + hkl_size != len(value) or hkl_raw_size > MAX_DECODED:
        raise ValueError("Invalid MP pattern lengths")
    numeric = inflate(value[offset:offset + numeric_size], count * 16)
    if len(numeric) != count * 16:
        raise ValueError("Invalid MP numeric block")
    arrays = np.frombuffer(numeric, dtype="u1").reshape(8, -1).T.copy().view("<f8").reshape(2, count)
    if not include_hkl:
        if validate_hkl:
            raw = inflate(value[offset + numeric_size:], hkl_raw_size)
            if len(raw) != hkl_raw_size:
                raise ValueError("Invalid MP hkl block")
        return {"d": arrays[0], "intensities": arrays[1]}
    raw = inflate(value[offset + numeric_size:], hkl_raw_size)
    if len(raw) != hkl_raw_size:
        raise ValueError("Invalid MP hkl block")
    source = iter(raw)
    result = []
    groups_seen = 0
    for _ in range(count):
        size = _get(source)
        groups_seen += size
        if groups_seen > MAX_DECODED // 4:
            raise ValueError("Excessive MP hkl groups")
        groups = []
        for _ in range(size):
            dimension = _get(source)
            if not 1 <= dimension <= 16:
                raise ValueError("Invalid MP hkl dimension")
            indices = []
            for _ in range(dimension):
                number = _get(source)
                indices.append(number // 2 if number % 2 == 0 else -(number + 1) // 2)
            groups.append({"hkl": indices, "multiplicity": _get(source)})
        result.append(groups)
    if next(source, None) is not None:
        raise ValueError("Unexpected trailing MP hkl data")
    return {"d": arrays[0].tolist(), "intensities": arrays[1].tolist(), "hkl": result}
