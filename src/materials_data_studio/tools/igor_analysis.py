"""Bounded, explicit Igor analysis subset shared by Command and converted IPF.

Algorithms follow the Igor 10 operation references. No UI, process, network or
filesystem access. Unsupported flags are rejected by CommandEngine before edits.
"""

from __future__ import annotations

from itertools import pairwise

import numpy as np
from scipy.ndimage import convolve1d
from scipy.signal import savgol_filter

from materials_data_studio.tools.pxp_model import PxpWave


def integer(value: float, low: int, high: int, description: str) -> int:
    if not np.isfinite(value) or int(value) != value or not low <= value <= high:
        raise ValueError(f"{description} must be an integer in [{low},{high}]")
    return int(value)


def selected_points(wave: PxpWave, range_value: str | None, evaluate) -> np.ndarray:
    """Inclusive 1D index sequence; reversed endpoints reverse search direction."""
    from materials_data_studio.tools.command_language import expression, split

    if not range_value:
        return np.arange(wave.shape[0])
    value = range_value.strip()
    if value[0] not in "[(" or value[-1] != ("]" if value[0] == "[" else ")"):
        raise ValueError("Range must be [startPoint,endPoint] or (startX,endX)")
    parts = split(value[1:-1])
    if not 1 <= len(parts) <= 2:
        raise ValueError("Range needs one or two endpoints")
    bounds = [None if p == "*" else float(evaluate(expression(p))) for p in parts]
    bounds += [None] * (2 - len(bounds))
    if any(v is not None and not np.isfinite(v) for v in bounds):
        raise ValueError("Range endpoints must be finite or *")
    if value[0] == "[":
        first = (
            integer(bounds[0], 0, wave.shape[0] - 1, "Start point") if bounds[0] is not None else 0
        )
        last = (
            integer(bounds[1], 0, wave.shape[0] - 1, "End point")
            if bounds[1] is not None
            else wave.shape[0] - 1
        )
        direction = 1 if last >= first else -1
    else:
        offset, delta = wave.dimension_scaling[0]
        if delta == 0 or not np.isfinite(delta):
            raise ValueError("FindLevel/WaveStats require nonzero finite X scaling")
        first_x = offset if bounds[0] is None else bounds[0]
        last_x = offset + delta * (wave.shape[0] - 1) if bounds[1] is None else bounds[1]
        direction = 1 if (last_x - first_x) / delta >= 0 else -1
        a, b = (first_x - offset) / delta, (last_x - offset) / delta
        first = int(np.ceil(a) if direction > 0 else np.floor(a))
        last = int(np.floor(b) if direction > 0 else np.ceil(b))
        if min(first, last) < 0 or max(first, last) >= wave.shape[0]:
            raise ValueError("Range lies outside the wave")
        if direction * (last - first) < 0:
            return np.array([], dtype=int)
    return np.arange(first, last + direction, direction)


def smooth(
    data: np.ndarray,
    number: float,
    *,
    box: float | None = None,
    order: float | None = None,
    end: float = 0,
) -> np.ndarray:
    """Binomial passes by default, bounce endpoints; explicit boxcar/SG subset."""
    modes = {0: "mirror", 1: "wrap", 2: "constant", 3: "nearest"}
    mode = modes[integer(end, 0, 3, "End effect")]
    n = integer(number, 1, 32767, "Smoothing count/window")
    if box is not None and order is not None:
        raise ValueError("Use /B or /S, not both")
    if data.ndim != 1 or data.dtype.kind not in "fc":
        raise ValueError(
            "Smooth currently supports floating-point/complex 1D waves; integer smoothing is not implemented"
        )
    values = np.array(data, dtype=np.complex128 if np.iscomplexobj(data) else np.float64)
    passes = integer(box, 1, 32767, "Boxcar passes") if box is not None else n
    if order is not None:
        degree = integer(order, 2, 4, "SG order")
        if degree not in {2, 4} or not 5 <= n <= 25 or n % 2 == 0:
            raise ValueError("Smooth/S requires order 2 or 4 and an odd window 5..25")
        if np.iscomplexobj(values):
            return np.asarray(savgol_filter(values.real, n, degree, mode=mode)) + 1j * np.asarray(
                savgol_filter(values.imag, n, degree, mode=mode)
            )
        return np.asarray(savgol_filter(values, n, degree, mode=mode))
    if box is not None and n % 2 == 0:
        n += 1
    if passes * max(len(values), 1) * (n if box is not None else 3) > 100_000_000:
        raise ValueError(
            "Smoothing exceeds the interactive work limit; use fewer passes or a smaller wave"
        )
    kernel = np.ones(n) if box is not None else np.array([0.25, 0.5, 0.25])
    for _ in range(passes):
        if box is None:
            values = convolve1d(values, kernel, mode=mode)
        else:
            valid = ~np.isnan(values)
            weights = convolve1d(
                valid.astype(float), kernel, mode=mode, cval=1 if mode == "constant" else 0
            )
            total = convolve1d(np.where(valid, values, 0), kernel, mode=mode)
            values = np.divide(total, weights, out=np.full_like(values, np.nan), where=weights > 0)
    return values


def find_level(
    wave: PxpWave, points: np.ndarray, level: float, edge: float = 0, point_units: bool = False
) -> dict[str, float]:
    edge = integer(edge, 0, 2, "Crossing edge")
    if not np.isfinite(level):
        raise ValueError("FindLevel requires a finite level")
    data = np.asarray(wave.data, dtype=float)
    valid = points[np.isfinite(data[points])]
    for a, b in pairwise(valid):
        ya, yb = data[a], data[b]
        # Exclude flat segments: this locates transitions, not exact matches.
        rising = data[max(a, b)] > data[min(a, b)]
        if ya == yb or not min(ya, yb) <= level <= max(ya, yb) or (edge and rising != (edge == 1)):
            continue
        p = float(a + (level - ya) * (b - a) / (yb - ya))
        x = p if point_units else wave.dimension_scaling[0][0] + p * wave.dimension_scaling[0][1]
        return dict(V_flag=0, V_LevelX=x, V_rising=int(rising))
    return dict(V_flag=1, V_LevelX=np.nan, V_rising=np.nan)


def wave_stats(wave: PxpWave, points: np.ndarray) -> dict[str, float]:
    data = np.asarray(wave.data.real, dtype=float)[points]
    valid = np.isfinite(data)
    values = data[valid]
    n = len(values)
    mean = float(values.mean()) if n else np.nan
    sdev = float(values.std(ddof=1)) if n > 1 else np.nan
    row_min = int(points[valid][np.argmin(values)]) if n else np.nan
    row_max = int(points[valid][np.argmax(values)]) if n else np.nan
    offset, delta = wave.dimension_scaling[0]
    scaled = (values - mean) / sdev if sdev > 0 else np.full(n, np.nan)
    return dict(
        V_npnts=n,
        V_numNaNs=int(np.isnan(data).sum()),
        V_numINFs=int(np.isinf(data).sum()),
        V_avg=mean,
        V_sum=float(values.sum()),
        V_sdev=sdev,
        V_sem=sdev / np.sqrt(n) if n else np.nan,
        V_rms=float(np.sqrt(np.mean(values**2))) if n else np.nan,
        V_adev=float(np.mean(np.abs(values - mean))) if n else np.nan,
        V_skew=float(np.mean(scaled**3)) if n else np.nan,
        V_kurt=float(np.mean(scaled**4) - 3) if n else np.nan,
        V_min=float(values.min()) if n else np.nan,
        V_max=float(values.max()) if n else np.nan,
        V_minloc=offset + delta * row_min,
        V_maxloc=offset + delta * row_max,
        V_minRowLoc=row_min,
        V_maxRowLoc=row_max,
        V_startRow=int(min(points)) if len(points) else np.nan,
        V_endRow=int(max(points)) if len(points) else np.nan,
    )
