"""Small, explicit Igor FFT/histogram/linear-fit contracts (not Quick Fit)."""
from __future__ import annotations

from typing import Any

import numpy as np
from scipy import fft


def forward_fft(data: Any, delta: float, *, rotate: bool = True, output: int = 1) -> tuple[Any, float, float]:
    data = np.asarray(data)
    n = data.size
    if data.ndim != 1 or data.dtype.kind not in "iufc" or n < 4 or n % 2 or n > 10_000_000 or not np.isfinite(data).all():
        raise ValueError("FFT subset needs an even, finite numeric 1D wave of 4..10000000 points")
    if not np.isfinite(delta) or delta == 0 or output not in {1, 2, 3, 4, 5}:
        raise ValueError("Invalid FFT scale or unsupported /OUT (supported: 1..5)")
    double = data.dtype in (np.dtype("f8"), np.dtype("c16"))
    complex_input = data.dtype.kind == "c"
    values = data.astype((np.complex128 if double else np.complex64) if complex_input else (np.float64 if double else np.float32))
    # Igor's forward exponent has a positive sign, with no amplitude normalization.
    transformed: Any = fft.ifft(values) if complex_input else fft.rfft(values)
    spectrum = transformed * n if complex_input else np.conj(transformed)
    step = 1 / (n * delta)
    start = -n // 2 * step if complex_input and rotate else 0.0
    if complex_input and rotate:
        spectrum = fft.fftshift(spectrum)
    spectrum = {1: lambda: spectrum, 2: lambda: spectrum.real, 3: lambda: np.abs(spectrum),
                4: lambda: np.abs(spectrum)**2, 5: lambda: np.angle(spectrum)}[output]()
    return spectrum, start, step


def histogram(data: Any, start: float, width: float, count: int, *, probability: bool = False,
              cumulative: bool = False, weights: Any = None, double: bool = False) -> Any:
    data = np.asarray(data)
    if data.ndim != 1 or data.dtype.kind not in "iuf" or not np.isfinite(start) or not np.isfinite(width) or width <= 0:
        raise ValueError("Histogram subset requires real 1D data and finite positive bin width")
    if int(count) != count or not 1 <= count <= 1_000_000:
        raise ValueError("Histogram bin count must be 1..1000000")
    count = int(count)
    if weights is not None and (np.shape(weights) != data.shape or cumulative):
        raise ValueError("Weights must match input; weighted cumulative histograms are unsupported")
    if weights is not None and np.asarray(weights).dtype.kind not in "biuf":
        raise ValueError("This histogram subset supports real numeric weights only")
    finite = np.isfinite(data)
    values = data[finite].astype(float)
    # All intervals are half open, including the last bin (unlike numpy.histogram).
    ordinal = np.floor((values - start) / width)
    inside = (ordinal >= 0) & (ordinal < count)
    selected_weights = np.asarray(weights, dtype=float)[finite][inside] if weights is not None else None
    if selected_weights is not None and not np.isfinite(selected_weights).all():
        raise ValueError("Selected histogram weights must be finite")
    result = np.bincount(ordinal[inside].astype(int), weights=selected_weights, minlength=count)
    if probability:
        # Native /P uses all finite source observations, including observations
        # outside explicit bins: a truncated PDF need not integrate to one.
        total = int(np.count_nonzero(finite))
        result = result / (total * width) if total else result.astype(float)
    if cumulative:
        result = np.cumsum(result)
        if probability:
            result *= width
    return result.astype(np.float64 if double or weights is not None else np.float32)


def linear_fit(x: Any, y: Any, *, sigma: Any = None, direct_weights: bool = False,
               degree: int = 1, selected: Any = None) -> tuple[Any, Any, Any, Any, float]:
    x, y = np.asarray(x, float), np.asarray(y, float)
    if x.ndim != 1 or x.shape != y.shape or not 1 <= degree <= 10 or len(x) > 1_000_000:
        raise ValueError("CurveFit subset needs matching 1D waves; polynomial order 1..10")
    mask = np.isfinite(x) & np.isfinite(y)
    if selected is not None:
        if np.shape(selected) != y.shape:
            raise ValueError("Fit mask must match data")
        mask &= np.isfinite(selected) & (np.asarray(selected) != 0)
    factor = np.ones_like(y)
    if sigma is not None:
        if np.asarray(sigma).dtype.kind not in "biuf":
            raise ValueError("CurveFit weights must be real numeric")
        weights = np.asarray(sigma, float)
        if weights.shape != y.shape:
            raise ValueError("CurveFit weights must match data")
        mask &= np.isfinite(weights) & (weights > 0)
        factor = weights if direct_weights else 1 / weights
    if np.count_nonzero(mask) <= degree + 1:
        raise ValueError("CurveFit requires more finite points than coefficients")
    basis = np.polynomial.polynomial.polyvander(x[mask], degree)
    design = basis * factor[mask, None]
    coef, _, rank, _ = np.linalg.lstsq(design, y[mask] * factor[mask], rcond=None)
    if rank != degree + 1:
        raise ValueError("CurveFit design is singular")
    predicted = np.polynomial.polynomial.polyval(x, coef)
    residual = y - predicted
    chi = float(np.sum((residual[mask] * factor[mask])**2))
    covariance = np.linalg.inv(design.T @ design)
    if sigma is None:
        covariance *= chi / (np.count_nonzero(mask) - degree - 1)
    residual[~mask] = np.nan
    return coef, np.sqrt(np.diag(covariance)), covariance, residual, chi
