"""Bounded wave coordinates, dtype contracts and semantic state readback."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

import numpy as np

from materials_data_studio.tools.pxp_model import PxpWave

MAX_ELEMENTS = 10_000_000
MAX_BYTES = 256 * 1024**2


def assignment_values(values: Any, dtype: Any) -> Any:
    """Validated storage conversion; float64 upper bounds must stay exclusive."""
    values, dtype = np.asarray(values), np.dtype(dtype)
    if dtype.kind in "iu":
        if values.dtype.kind not in "biuf" or not np.isfinite(values).all():
            raise ValueError("Integer assignment must be finite, real and in dtype range")
        limits = np.iinfo(dtype)
        if values.size:
            if values.dtype.kind == "f":
                invalid = np.any(values < limits.min) or np.any(values >= int(limits.max) + 1)
            else:
                invalid = int(values.min()) < limits.min or int(values.max()) > limits.max
            if invalid:
                raise ValueError("Integer assignment must be finite and in dtype range")
        return values.astype(dtype)
    return values.astype(dtype, casting="same_kind")


def wave_dtype(flags: set[str], current: Any = None) -> np.dtype:
    kinds = flags & {"D", "S", "I", "L", "B", "W", "T"}
    if len(kinds) > 1 or ("T" in kinds and flags & {"C", "U"}) or flags >= {"R", "C"}:
        raise ValueError("Conflicting wave type flags")
    base = next(iter(kinds), "")
    if base == "T":
        return np.dtype("U1")
    if "U" in flags and base not in {"I", "L", "B", "W"}:
        raise ValueError("/U requires an integer type")
    dtype = {"D":"f8", "S":"f4", "I":"i4", "L":"i8", "B":"i1", "W":"i2"}.get(base)
    result = np.dtype(dtype if dtype else current if current is not None else "f4")
    if "U" in flags:
        result = np.dtype(result.str.replace("i", "u"))
    if "C" in flags or (current is not None and np.dtype(current).kind == "c" and "R" not in flags):
        if result.kind not in {"f","c"}:
            raise ValueError("Complex integer/text waves are not supported")
        if result.kind == "f":
            result = np.dtype("c16" if result.itemsize == 8 else "c8")
    elif "R" in flags and result.kind == "c":
        result = np.dtype("f8" if result.itemsize == 16 else "f4")
    return result


def checked_shape(value: Any, dtype: Any) -> tuple[int, ...]:
    shape = value if isinstance(value, tuple) else (value,)
    if not 1 <= len(shape) <= 4:
        raise ValueError("Use 1D through 4D dimensions")
    while len(shape) > 1 and shape[-1] == 0:
        shape = shape[:-1]
    if any(not np.isfinite(n) or int(n) != n or n <= 0 for n in shape):
        raise ValueError("Use positive integer dimensions (1D through 4D)")
    shape = tuple(map(int, shape))
    size = 1
    for count in shape:
        size *= count
    if size > MAX_ELEMENTS or size * np.dtype(dtype).itemsize > MAX_BYTES:
        raise ValueError("Wave exceeds 10 million elements / 256 MiB")
    return shape


def coordinates(wave: PxpWave) -> dict[str, Any]:
    result = {}
    for dim, (point, coordinate) in enumerate(zip("pqrs", "xyzt", strict=True)):
        if dim < wave.data.ndim:
            shape = tuple(wave.shape[dim] if i == dim else 1 for i in range(wave.data.ndim))
            result[point] = np.arange(wave.shape[dim], dtype=float).reshape(shape)
            offset, delta = wave.dimension_scaling[dim]
            result[coordinate] = offset + delta * result[point]
    return result


def axis_info(graph: Any, name: str) -> dict[str, Any]:
    axis = next((a for a in graph.axes if a.name == name), None)
    if axis is None:
        raise ValueError(f"Unknown axis: {name}")
    return asdict(axis)


def trace_info(graph: Any, name: str) -> dict[str, Any]:
    trace = next((t for t in graph.traces if t.name == name), None)
    if trace is None:
        raise ValueError(f"Unknown trace: {name}")
    return asdict(trace)
