"""Bounded signal-processing workflows with explicit source/destination roles."""
from __future__ import annotations

from typing import Any

import numpy as np
from scipy import fft as fft_module
from scipy import interpolate, signal

from materials_data_studio.tools.graph_spectral_analysis import SPECTRAL, spectral
from materials_data_studio.tools.graph_waves import make_wave
from materials_data_studio.tools.pxp_model import PxpWave

PROCESSING = {
    "poly2d": ("poly2D", ("Total degree 1", "Total degree 2", "Total degree 3", "Total degree 4")),
    "gauss2d": ("Gauss2D", ("Rotated elliptical Gaussian",)),
    **SPECTRAL,
    "convolve": ("Convolve", ("Linear", "Acausal", "Circular")),
    "correlate": ("Correlate", ("Linear", "Circular", "Autocorrelation normalized")),
    "interpolate": ("Interpolate", ("Linear", "Cubic spline", "PCHIP")),
    "filter": ("Filter", ("FIR coefficients", "IIR numerator / denominator")),
    "ifft": ("Inverse FFT", ("Complex spectrum (rotated)", "Complex spectrum (unrotated)", "Real half-spectrum")),
}
fft: Any = fft_module


def process(kind: str, waves: list[PxpWave], destination: str, *, method: int = 0,
            count: int = 201, remove_dc: bool = False) -> PxpWave:
    if kind not in PROCESSING or not 0 <= method < len(PROCESSING[kind][1]):
        raise ValueError("Unknown processing method")
    if not waves or any(w.data.ndim != 1 or w.data.dtype.kind not in "biufc" or not w.data.size or not np.isfinite(w.data).all() for w in waves):
        raise ValueError("Select nonempty finite numeric 1D waves")
    if sum(w.data.size for w in waves) > 10_000_000:
        raise ValueError("Processing is limited to 10 million total points")
    if kind in SPECTRAL:
        return spectral(kind, waves, destination, method, count)
    source = waves[0]
    a: Any = np.asarray(source.data)
    offset, delta = source.dimension_scaling[0]
    if not np.isfinite(delta) or delta == 0:
        raise ValueError("Wave scaling must have a finite nonzero delta")
    values: Any
    if kind in {"convolve", "correlate"}:
        if len(waves) != 2:
            raise ValueError("Select source/kernel first and signal second")
        b: Any = np.asarray(waves[1].data)
        source = waves[1]
        offset, delta = source.dimension_scaling[0]
        if np.iscomplexobj(a) != np.iscomplexobj(b):
            raise ValueError("Both waves must be real, or both complex")
        if remove_dc:
            a, b = a-a.mean(), b-b.mean()
        if kind == "convolve":
            if method == 2:
                if len(a) > len(b):
                    raise ValueError("Circular kernel must not be longer than signal")
                values = fft.ifft(fft.fft(a, len(b))*fft.fft(b))
                if not np.iscomplexobj(a):
                    values = values.real
            else:
                values = signal.fftconvolve(b, a, mode="full")
                if method == 1:
                    values = values[(len(a)-1)//2:(len(a)-1)//2+len(b)]
        else:
            if np.iscomplexobj(a):
                raise ValueError("This correlation workflow supports real waves")
            if method == 1:
                if len(a) != len(b):
                    raise ValueError("Circular correlation requires equal lengths")
                values = fft.ifft(np.conj(fft.fft(a))*fft.fft(b)).real
            else:
                values = signal.correlate(b, a, mode="full", method="fft")
                offset = -(len(a)-1)*delta
                if method == 2:
                    if len(a) != len(b) or not np.array_equal(waves[0].data, waves[1].data):
                        raise ValueError("Autocorrelation requires the same source wave twice")
                    center = values[len(a)-1]
                    if center <= 0:
                        raise ValueError("Autocorrelation is undefined for zero energy")
                    values /= center
    elif kind == "interpolate":
        if len(waves) not in {1, 2} or np.iscomplexobj(a):
            raise ValueError("Choose Y and optional X wave")
        x = waves[1].data if len(waves) == 2 else source.coordinate(0)
        if np.shape(x) != a.shape or len(a) < 2 or np.iscomplexobj(x):
            raise ValueError("X and Y must have equal lengths >=2")
        x = np.asarray(x, float)
        if np.all(np.diff(x) < 0):
            x, a = x[::-1], a[::-1]
        if np.any(np.diff(x) <= 0):
            raise ValueError("Interpolation requires strictly monotonic X")
        if int(count) != count or not 2 <= count <= 1_000_000:
            raise ValueError("Output length must be 2..1000000")
        output_x = np.linspace(x[0], x[-1], int(count))
        values = np.interp(output_x, x, a) if method == 0 else (interpolate.CubicSpline if method == 1 else interpolate.PchipInterpolator)(x, a)(output_x)
        offset, delta = float(output_x[0]), float(output_x[1]-output_x[0])
    elif kind == "filter":
        if len(waves) != (2 if method == 0 else 3):
            raise ValueError("Choose signal, numerator coefficients and (IIR only) denominator coefficients")
        numerator = waves[1].data
        denominator = np.array([1.]) if method == 0 else waves[2].data
        if denominator[0] == 0:
            raise ValueError("Leading denominator coefficient must be nonzero")
        if len(numerator) > 10000 or len(denominator) > 10000:
            raise ValueError("Filter is limited to 10000 coefficients")
        values = signal.lfilter(numerator, denominator, a)
    else:
        if len(waves) != 1 or not np.iscomplexobj(a):
            raise ValueError("Inverse FFT requires one complex spectrum")
        if method == 2:
            if len(a) < 3:
                raise ValueError("Real half-spectrum needs at least three bins")
            values = fft.irfft(np.conj(a), n=2*(len(a)-1))
        else:
            values = fft.fft(fft.ifftshift(a) if method == 0 else a)/len(a)
        offset, delta = 0., 1/(len(values)*delta)
    if not np.isfinite(values).all():
        raise ValueError("Processing produced nonfinite results; check filter stability")
    from dataclasses import replace
    return replace(make_wave(destination, values, source), dimension_scaling=((offset, delta),),
                   wave_note=f"{PROCESSING[kind][0]}: inputs={[w.igor_path for w in waves]}; method={method}; remove_dc={remove_dc}")
