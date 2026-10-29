"""Bounded wave combination, metadata and resampling without GUI dependencies."""
from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
from typing import Any, cast
from uuid import uuid4

import numpy as np

from materials_data_studio.tools.graph_waves import make_wave
from materials_data_studio.tools.igor_context import checked_shape
from materials_data_studio.tools.pxp_model import PxpWave


def set_dimension_label(wave: PxpWave, dimension: int, index: int, label: str) -> PxpWave:
    """Set one element/dimension name while retaining all other metadata."""
    if not 0 <= dimension < wave.data.ndim or not -1 <= index < wave.shape[dimension]:
        raise ValueError("Dimension/label index is outside the wave")
    if len(label.encode("utf-8")) > 255 or any(c in label for c in "\"':;\r\n\0"):
        raise ValueError("Dimension labels require at most 255 UTF-8 bytes without quote/colon/control characters")
    labels = [list(wave.dimension_labels[d] if d < len(wave.dimension_labels) else ()) for d in range(wave.data.ndim)]
    names = list(wave.dimension_names) + [""] * (wave.data.ndim - len(wave.dimension_names))
    if index == -1:
        names[dimension] = label
    else:
        labels[dimension] += [""] * (index + 1 - len(labels[dimension]))
        if label and label in labels[dimension] and labels[dimension][index] != label:
            raise ValueError("Element labels must be unique within a dimension")
        labels[dimension][index] = label
    return replace(wave, dimension_labels=tuple(tuple(v) for v in labels), dimension_names=tuple(names))


def dimension_label(wave: PxpWave, dimension: int, index: int) -> str:
    """Return an element or dimension label; unset entries are empty."""
    if not 0 <= dimension < wave.data.ndim or not -1 <= index < wave.shape[dimension]:
        raise ValueError("Dimension/label index is outside the wave")
    if index == -1:
        return wave.dimension_names[dimension] if dimension < len(wave.dimension_names) else ""
    labels = wave.dimension_labels[dimension] if dimension < len(wave.dimension_labels) else ()
    return labels[index] if index < len(labels) else ""


def concatenate_waves(waves: list[PxpWave], name: str, *, dimension: int | None = None,
                      promote: bool = True, labels: bool = False) -> PxpWave:
    """Promote equal shapes or concatenate along an explicit existing dimension."""
    if not waves or len(waves) > 100:
        raise ValueError("Concatenate requires 1..100 source waves")
    if any((w.data.dtype.kind in "SU") != (waves[0].data.dtype.kind in "SU") for w in waves):
        raise ValueError("Concatenate requires compatible wave types")
    dtype = np.result_type(*(w.data.dtype for w in waves)) if waves[0].data.dtype.kind in "SU" else waves[0].data.dtype
    shape = waves[0].shape
    if len(waves) == 1:
        return replace(make_wave(name, waves[0].data, waves[0]), id=uuid4().hex)
    common = 0
    while common < min(w.data.ndim for w in waves) and all(w.shape[common] == shape[common] for w in waves):
        common += 1
    if promote and dimension is None:
        axis = min(common, 3)
        prefix = shape[:axis]
        sizes = [w.data.size // int(np.prod(prefix) if prefix else 1) for w in waves]
        arrays = [w.data.reshape((*prefix, n), order="F") for w, n in zip(waves, sizes, strict=True)]
    else:
        axis = max(w.data.ndim for w in waves) - 1 if dimension is None else dimension
        if not 0 <= axis < 4:
            raise ValueError("Concatenation dimension must be 0..3")
        rank = max(axis + 1, *(w.data.ndim for w in waves))
        arrays = [w.data.reshape((*w.shape, *(1 for _ in range(rank - w.data.ndim)))) for w in waves]
        if any(any(a != b for i, (a, b) in enumerate(zip(arrays[0].shape, array.shape, strict=True)) if i != axis) for array in arrays):
            raise ValueError("All dimensions except the concatenation dimension must match")
    out_shape = tuple(sum(a.shape[i] for a in arrays) if i == axis else n for i, n in enumerate(arrays[0].shape))
    checked_shape(out_shape, dtype)
    values = np.concatenate(arrays, axis=axis).astype(dtype)
    result = make_wave(name, values, waves[0])
    if labels:
        if axis >= len(shape):
            for i, w in enumerate(waves):
                result = set_dimension_label(result, axis, i, w.name)
        else:
            combined = tuple(dimension_label(w, axis, i) if axis < w.data.ndim else w.name for w in waves for i in range(w.shape[axis] if axis < w.data.ndim else 1))
            existing = list(result.dimension_labels)
            existing[axis] = combined
            result = replace(result, dimension_labels=tuple(existing))
    return replace(result, id=uuid4().hex)


def split_wave(wave: PxpWave, names: list[str], *, dimensions: int | None = None,
               copy_scaling: bool = False, copy_note: bool = False) -> list[PxpWave]:
    """Split high dimensions in Igor's column-major order."""
    dim = wave.data.ndim - 1 if dimensions is None else dimensions
    if not 1 <= dim < wave.data.ndim:
        raise ValueError("SplitWave output dimensionality must be 1..source.ndim-1")
    count = int(np.prod(wave.shape[dim:]))
    if count > 10000 or len(names) != count or len(set(names)) != count:
        raise ValueError("SplitWave requires unique names for every output (maximum 10000)")
    outputs = []
    for index, name in enumerate(names):
        tail = np.unravel_index(index, wave.shape[dim:], order="F")
        result = make_wave(name, wave.data[(slice(None),) * dim + tail], wave if copy_scaling else None)
        result = replace(result, id=uuid4().hex, data_units=wave.data_units,
                         wave_note=wave.wave_note if copy_note else "")
        outputs.append(result)
    return outputs


def resample_wave(wave: PxpWave, *, up: int = 1, down: int = 1, dimension: int = 0,
                  samples: int = 21, window: str = "Hanning", end: int = 2,
                  rate: float | None = None, same_delta: float | None = None,
                  coefficients: Any = None) -> PxpWave:
    """Resample a real wave with a finite reconstruction filter and explicit edges."""
    from scipy.signal import upfirdn

    if wave.data.dtype.kind not in "f" or not np.isfinite(wave.data).all():
        raise ValueError("Resample requires finite single/double precision data; remove NaNs first")
    if not 0 <= dimension < wave.data.ndim:
        raise ValueError("Resample dimension is outside the wave")
    delta = wave.dimension_scaling[dimension][1]
    if rate is not None or same_delta is not None:
        ratio = delta * rate if rate is not None else delta / cast(float, same_delta)
        if not np.isfinite(ratio) or ratio <= 0:
            raise ValueError("Resample rate ratio must be finite and positive")
        frac = Fraction(float(ratio)).limit_denominator(10000)
        up, down = frac.numerator, frac.denominator
    if up != int(up) or down != int(down) or not 1 <= up <= 10000 or not 1 <= down <= 10000:
        raise ValueError("Resample UP/DOWN must be integers in 1..10000")
    up, down = int(up), int(down)
    if end not in range(4) or samples != int(samples) or samples < 3 or samples % 2 != 1:
        raise ValueError("Resample needs an odd reconstruction length >=3 and E=0..3")
    if coefficients is not None:
        coefficients = np.asarray(coefficients, dtype=float)
        if coefficients.ndim != 1 or not np.isfinite(coefficients).all() or coefficients.size % 2 != 1 or (coefficients.size - 1) % (2 * up):
            raise ValueError("Resample COEF needs finite 1D odd length 1+2*k*UP")
        samples = 1 + (len(coefficients) - 1) // up
    if wave.shape[dimension] < samples:
        raise ValueError("Input wave is shorter than the reconstruction coefficient wave")
    count = ((wave.shape[dimension] - 1) * up) // down + 1
    shape = list(wave.shape)
    shape[dimension] = count
    checked_shape(tuple(shape), wave.data.dtype)
    if coefficients is None and samples == 3 and window.casefold() == "none" and down == 1:
        coords = np.arange(count) / up
        data = np.apply_along_axis(lambda y: np.interp(coords, np.arange(len(y)), y), dimension, wave.data)
    else:
        if coefficients is None:
            coefficients = reconstruction_filter(up, down, int(samples), window)
        half = (len(coefficients) - 1) // 2
        pad = half // up + 1
        widths = [(0, 0)] * wave.data.ndim
        widths[dimension] = (pad, 0)
        left = np.pad(wave.data, widths, mode=cast(Any, {0: "reflect", 1: "wrap", 2: "constant", 3: "edge"}[end]))
        widths[dimension] = (0, pad)
        # Native bounce repeats the last endpoint on the high side; the low
        # side reflects without repeating the first endpoint.
        right = np.pad(wave.data, widths, mode=cast(Any, {0: "symmetric", 1: "wrap", 2: "constant", 3: "edge"}[end]))
        right = np.take(right, np.arange(wave.shape[dimension], wave.shape[dimension] + pad), axis=dimension)
        work = np.concatenate((left, right), axis=dimension)
        phase = (-pad * up - half) % down
        kernel = np.pad(coefficients, (phase, 0))
        filtered = upfirdn(kernel, work, up, down, axis=dimension)
        start = (pad * up + half + phase) // down
        data = np.take(filtered, np.arange(start, start + count), axis=dimension)
    result = make_wave(wave.igor_path, data.astype(wave.data.dtype), wave)
    scaling = list(result.dimension_scaling)
    scaling[dimension] = (scaling[dimension][0], delta * down / up)
    labels = list(result.dimension_labels)
    labels[dimension] = ()  # Resampled indices do not retain element identity.
    return replace(result, dimension_scaling=tuple(scaling), dimension_labels=tuple(labels))


def reconstruction_filter(up: int, down: int, samples: int, window: str) -> Any:
    """Windowed sinc normalized independently for every interpolation phase."""
    from scipy.signal.windows import get_window
    windows = {"hanning": "hann", "hamming": "hamming", "none": "boxcar", "bartlett": "bartlett", "blackman": "blackman"}
    if window.casefold() not in windows:
        raise ValueError("Supported reconstruction windows: Hanning, Hamming, None, Bartlett, Blackman")
    size = (samples - 1) * up + 1
    if size > 1_000_001:
        raise ValueError("Reconstruction filter is limited to one million coefficients")
    k = np.arange(size) - (size - 1) / 2
    coefficients = np.sinc(k / max(up, down)) * get_window(windows[window.casefold()], size, fftbins=False)
    coefficients[np.abs(coefficients) < 1e-15] = 0
    for phase in range(up):
        total = np.sum(coefficients[phase::up])
        if abs(total) < 1e-15:
            raise ValueError("Degenerate reconstruction filter; increase N")
        coefficients[phase::up] /= total
    return coefficients
