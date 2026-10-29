"""Native, bounded Quick Fit models for editable graph documents (no Igor code)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import curve_fit
from scipy.special import expit, voigt_profile

from materials_data_studio.tools.graph_document import EditableGraphDocument

MODELS = ("line", "gauss", "lor", "Voigt", "exp_XOffset", "dblexp_XOffset", "exp", "dblexp",
          "dblexp_peak", "sin", "HillEquation", "Sigmoid", "Power", "LogNormal", "Log")


def cursor_limits(document: EditableGraphDocument, graph_name: str, trace_name: str) -> tuple[float, float] | None:
    """Use A/B only when both belong to the fitted trace (inclusive endpoints)."""
    graph = document.graph(graph_name)
    cursors = {c.name: c for c in graph.cursors if c.trace == trace_name}
    if not {"A", "B"} <= cursors.keys():
        return None
    trace = next(t for t in graph.traces if t.name == trace_name)
    source = document.render_source()
    wave = source.wave((trace.x_wave or trace.y_wave) if graph.swap_xy else trace.y_wave)
    if wave is None:
        return None
    first, last = sorted([cursors["A"].point, cursors["B"].point])
    start, end = trace.point_range or (0, wave.data.size-1)
    first, last = max(start, first), min(end, last)
    if first >= last:
        raise ValueError("Cursors A and B must enclose at least two points")
    x, _y, _sigma = trace_values(document, graph_name, trace_name)
    # X bounds are also used by the preview dialog. Random-order X would select
    # extra points, so diagnose it rather than silently changing the fitted set.
    if not (np.all(np.diff(x) >= 0) or np.all(np.diff(x) <= 0)):
        raise ValueError("Cursor fitting requires monotonic X; use Curve Fitting with explicit X limits otherwise")
    lo, hi = sorted((float(x[first-start]), float(x[last-start])))
    return lo, hi


@dataclass(frozen=True)
class FitResult:
    model: str
    parameters: tuple[str, ...]
    values: tuple[float, ...]
    errors: tuple[float, ...]
    x: NDArray[np.float64]
    calculated: NDArray[np.float64]
    data_x: NDArray[np.float64]
    residual: NDArray[np.float64]
    rss: float
    count: int
    x_offset: float = 0

    def summary(self) -> str:
        lines = [f"Quick Fit: {self.model} (n={self.count}, RSS={self.rss:.6g})"]
        lines.extend(f"{name} = {value:.7g} ± {error:.3g}" for name, value, error in
                     zip(self.parameters, self.values, self.errors, strict=True))
        if self.x_offset:
            lines.append(f"Fixed X offset = {self.x_offset:.7g}")
        return "\n".join(lines)


def trace_values(document: EditableGraphDocument, graph_name: str, trace_name: str) -> tuple[Any, Any, Any]:
    graph = document.graph(graph_name)
    trace = next(t for t in graph.traces if t.name == trace_name)
    source = document.render_source()
    y_wave = source.wave(trace.y_wave)
    if y_wave is None:
        raise ValueError("Y wave not found.")
    x_wave = source.wave(trace.x_wave) if trace.x_wave else None
    x = np.asarray(x_wave.data if x_wave else y_wave.coordinate(0), dtype=float)
    y = np.asarray(y_wave.data, dtype=float)
    x = x * (trace.multiplier[0] or 1) + trace.offset[0]
    y = y * (trace.multiplier[1] or 1) + trace.offset[1]
    error = None
    displayed_error_axis = trace.error_axis.upper()
    if graph.swap_xy:
        displayed_error_axis = "Y" if displayed_error_axis == "X" else "X"
    if trace.error_low and displayed_error_axis == "Y":
        lo, hi = source.wave(trace.error_low), source.wave(trace.error_high or trace.error_low)
        if lo is not None and hi is not None:
            scale = trace.multiplier[0 if trace.error_axis.upper() == "X" else 1] or 1
            error = (np.asarray(lo.data, dtype=float) + np.asarray(hi.data, dtype=float)) / 2 * abs(scale)
    if graph.swap_xy and trace.x_error_low:
        lo, hi = source.wave(trace.x_error_low), source.wave(trace.x_error_high or trace.x_error_low)
        if lo is not None and hi is not None:
            error = (np.asarray(lo.data, float) + np.asarray(hi.data, float)) / 2 * abs(trace.multiplier[0] or 1)
    if trace.point_range:
        first, last = trace.point_range
        x, y = x[first:last+1], y[first:last+1]
        if error is not None:
            error = error[first:last+1]
    if graph.swap_xy:
        # Y error bars become X errors. They cannot be used as residual weights.
        x, y = y, x
    return x, y, error


def quick_fit(x: Any, y: Any, model: str, lower: float | None = None, upper: float | None = None,
              sigma: Any = None, cancel: Any = None, *, initial_values: Any = None,
              held: tuple[int, ...] = (), selected: Any = None,
              curve_range: tuple[float, float] | None = None) -> FitResult:
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if x.ndim != 1 or x.shape != y.shape:
        raise ValueError("Quick Fit requires matching 1D X and Y waves.")
    if lower is not None and upper is not None and lower >= upper:
        raise ValueError("Fit minimum must be less than maximum.")
    mask = np.isfinite(x) & np.isfinite(y)
    if selected is not None:
        selected = np.asarray(selected)
        if selected.shape != x.shape or selected.dtype.kind not in "biuf":
            raise ValueError("Fit mask must be a matching real 1D wave (nonzero=include)")
        mask &= np.isfinite(selected) & (selected != 0)
    if lower is not None:
        mask &= x >= lower
    if upper is not None:
        mask &= x <= upper
    if sigma is not None:
        sigma = np.asarray(sigma, dtype=float)
        if sigma.shape != x.shape:
            raise ValueError("Weight wave length must match.")
        mask &= np.isfinite(sigma) & (sigma > 0)
        sigma = sigma[mask]
    x, y = x[mask], y[mask]
    if len(x) < 3 or np.ptp(x) <= 0:
        raise ValueError("Select at least three finite points with distinct X values.")
    span = float(np.ptp(x))
    amplitude, base = float(np.ptp(y)), float(np.min(y))
    center, width = float(x[np.argmax(y)]), span / 5
    offset = float(np.min(x)) if model.endswith("_XOffset") else 0.0
    t = x - offset
    def clipped_exp(value):
        return np.exp(np.clip(value, -700, 700))
    names: list[str]
    bounds: Any = (-np.inf, np.inf)
    if model == "line" or model.startswith("poly"):
        degree = 1 if model == "line" else int(model.split(":")[-1])
        if not 1 <= degree <= 10 or len(x) <= degree + 1:
            raise ValueError("Polynomial order must be 1–10 with more points than coefficients.")
        # Center polynomial basis to avoid poor conditioning of raw powers.
        offset = float(np.mean(x)) if "XOffset" in model else 0.0
        t = x - offset
        def polynomial(values, *coefficients):
            return np.polynomial.polynomial.polyval(values, coefficients)
        function = polynomial
        initial = np.polynomial.polynomial.polyfit(t, y, degree)
        names = [f"c{i}" for i in range(degree + 1)]
    elif model in {"gauss", "lor", "Voigt", "LogNormal"}:
        if model == "gauss":
            def gaussian(values, b, a, c, w):
                return b + a * clipped_exp(-((values - c) / w)**2)
            function = gaussian
            names, initial = ["baseline", "amplitude", "center", "width"], [base, amplitude, center, width]
        elif model == "lor":
            def lorentzian(values, b, a, c, w):
                return b + a / (1 + ((values - c) / w)**2)
            function = lorentzian
            names, initial = ["baseline", "amplitude", "center", "width"], [base, amplitude, center, width]
        elif model == "Voigt":
            def voigt(values, b, a, c, s, g):
                return b + a * voigt_profile(values - c, s, g)
            function = voigt
            names, initial = ["baseline", "area", "center", "sigma", "gamma"], [base, amplitude * width, center, width, width]
        else:
            if np.any(x <= 0):
                raise ValueError("LogNormal requires positive X.")
            def log_normal(values, b, a, c, w):
                return b + a * clipped_exp(-0.5 * (np.log(values / c) / w)**2)
            function = log_normal
            names, initial = ["baseline", "amplitude", "center", "log_width"], [base, amplitude, max(center, 1e-12), 0.5]
        lows = [-np.inf, -np.inf, 1e-12 if model == "LogNormal" else -np.inf, 1e-12]
        if model == "Voigt":
            lows += [1e-12]
        bounds = (lows, [np.inf] * len(initial))
    elif model in {"exp", "exp_XOffset", "dblexp", "dblexp_XOffset", "dblexp_peak"}:
        def exponential(values, b, a, tau):
            return b + a * clipped_exp(-values / tau)
        function = exponential
        names, initial = ["baseline", "amplitude", "tau"], [base, float(y[0] - base), span / 2]
        bounds = ([-np.inf, -np.inf, 1e-12], [np.inf] * 3)
        if model.startswith("dblexp"):
            def double_exponential(values, b, a, tau, a2, tau2):
                return b + a * clipped_exp(-values / tau) + a2 * clipped_exp(-values / tau2)
            function = double_exponential
            names, initial = ["baseline", "amplitude1", "tau1", "amplitude2", "tau2"], [base, amplitude, span / 5, amplitude / 2, span]
            bounds = ([-np.inf, -np.inf, 1e-12, -np.inf, 1e-12], [np.inf] * 5)
            if model == "dblexp_peak":
                def double_peak(values, b, a, tau, tau2, c):
                    return b + a * (clipped_exp(-np.maximum(values - c, 0) / tau) - clipped_exp(-np.maximum(values - c, 0) / tau2))
                function = double_peak
                names, initial = ["baseline", "amplitude", "decay_tau", "rise_tau", "onset"], [base, amplitude * 2, span / 3, span / 20, float(np.min(x))]
                bounds = ([-np.inf, -np.inf, 1e-12, 1e-12, float(np.min(x) - span)], [np.inf, np.inf, np.inf, np.inf, float(np.max(x))])
    elif model == "sin":
        order = np.argsort(x)
        uniform = np.linspace(x.min(), x.max(), len(x))
        spectrum = np.abs(np.fft.rfft(np.interp(uniform, x[order], y[order]) - np.mean(y)))
        frequency = 2 * math.pi * max(1, int(np.argmax(spectrum[1:]) + 1)) / span
        def sinusoid(values, b, a, w, phase):
            return b + a * np.sin(w * values + phase)
        function = sinusoid
        names, initial = ["baseline", "amplitude", "angular_frequency", "phase"], [float(np.mean(y)), amplitude / 2, frequency, 0]
    elif model in {"Sigmoid", "HillEquation"}:
        if model == "HillEquation" and np.any(x <= 0):
            raise ValueError("HillEquation requires positive X.")
        def sigmoid(values, b, a, c, w):
            return b + a * expit((values - c) / w)
        function = sigmoid
        names, initial = ["baseline", "amplitude", "center", "width"], [base, amplitude, float(np.median(x)), span / 10]
        bounds = ([-np.inf, -np.inf, -np.inf, 1e-12], [np.inf] * 4)
        if model == "HillEquation":
            def hill(values, b, a, c, n):
                return b + a * expit(n * (np.log(values) - np.log(c)))
            function = hill
            names, initial = ["baseline", "amplitude", "half_maximum", "hill_exponent"], [base, amplitude, float(np.median(x)), 1]
            bounds = ([-np.inf, -np.inf, 1e-12, -np.inf], [np.inf] * 4)
    elif model in {"Power", "Log"}:
        if np.any(x <= 0):
            raise ValueError(f"{model} requires positive X.")
        def power(values, b, a, p):
            return b + a * clipped_exp(p * np.log(values))
        function = power
        names, initial = ["baseline", "amplitude", "power"], [base, amplitude / max(float(x.max()), 1e-12), 1]
        if model == "Log":
            def logarithm(values, b, a):
                return b + a * np.log(values)
            function = logarithm
            names, initial = ["baseline", "amplitude"], [base, amplitude]
    else:
        raise ValueError("Unknown Quick Fit model.")
    if len(x) <= len(initial):
        raise ValueError("More finite data points than free parameters are required.")
    if initial_values is not None:
        if len(initial_values) != len(initial) or not np.isfinite(initial_values).all():
            raise ValueError(f"Provide {len(initial)} finite initial coefficients in model order")
        initial = np.asarray(initial_values, float)
    if len(set(held)) != len(held) or any(int(i) != i or not 0 <= i < len(initial) for i in held):
        raise ValueError("Held coefficient indexes must be unique, zero-based model coefficients")
    initial = np.asarray(initial, float)
    low, high = (np.broadcast_to(np.asarray(b, float), initial.shape) for b in bounds)
    if np.any(initial < low) or np.any(initial > high):
        raise ValueError("Initial coefficients violate model bounds (widths and time constants must be positive)")
    free = [i for i in range(len(initial)) if i not in held]
    def expanded(parameters: Any) -> Any:
        result = initial.copy()
        result[free] = parameters
        return result
    def checked(values: Any, *parameters: Any) -> Any:
        if cancel and cancel():
            raise InterruptedError("Quick Fit stopped.")
        return function(values, *expanded(parameters))
    covariance = np.zeros((len(initial), len(initial)))
    if free:
        fitted, free_covariance = curve_fit(checked, t, y, p0=initial[free], bounds=(low[free], high[free]), sigma=sigma,
                                          absolute_sigma=sigma is not None, maxfev=15000,
                                          method="trf", x_scale="jac")
        values = expanded(fitted)
        covariance[np.ix_(free, free)] = free_covariance
    else:
        values = initial
    if not np.all(np.isfinite(values)):
        raise ValueError("Fit failed to converge to finite parameters.")
    residual = y - function(t, *values)
    extent = curve_range or (float(x.min()), float(x.max()))
    if not np.isfinite(extent).all() or extent[0] >= extent[1]:
        raise ValueError("Output curve range must have finite increasing bounds")
    smooth = np.linspace(*extent, max(300, min(2000, len(x) * 3)))
    calculated = function(smooth - offset, *values)
    if not np.all(np.isfinite(calculated)):
        raise ValueError("The fitted curve contains non-finite values.")
    errors = np.sqrt(np.maximum(0, np.diag(covariance)))
    return FitResult(model, tuple(names), tuple(float(v) for v in values), tuple(float(v) for v in errors),
                     smooth, calculated, x, residual, float(np.dot(residual, residual)), len(x), offset)
