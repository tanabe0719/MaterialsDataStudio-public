"""Noise-aware, background-corrected Gaussian/Cu-doublet peak fitting."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from threading import Event

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.optimize import least_squares
from scipy.signal import find_peaks, peak_widths

from materials_data_studio.modules.xrd.background import arpls_background

CU_ALPHA1 = 1.540598
CU_ALPHA2 = 1.544426
DOUBLET = "Cu Kα1/2"


@dataclass
class DetectionSettings:
    prominence: float = 0.02
    smoothing_sigma: float = 0.02
    noise_sigma: float = 0.0
    snr: float = 5.0
    min_fwhm: float = 0.04
    max_fwhm: float = 1.5
    peak_sigma: float = 0.0
    background_smoothness: float = 1e6
    doublet: bool = False
    alpha2_ratio: float = 0.5

    def validate(self) -> None:
        if not all(math.isfinite(float(v)) for v in asdict(self).values()):
            raise ValueError("Detection settings must be finite.")
        if not (0 < self.prominence <= 1 and 0 <= self.smoothing_sigma <= 1 and self.noise_sigma >= 0 and 1 <= self.snr <= 100):
            raise ValueError("Invalid prominence, smoothing/noise sigma or signal-to-noise threshold.")
        if not (0.001 <= self.min_fwhm < self.max_fwhm <= 10 and 0 <= self.peak_sigma <= 3 and self.background_smoothness > 0 and 0 <= self.alpha2_ratio <= 1):
            raise ValueError("Invalid peak widths, background smoothness or Kα2/Kα1 ratio.")


def alpha2_position(alpha1):
    argument = np.sin(np.deg2rad(np.asarray(alpha1) / 2)) * CU_ALPHA2 / CU_ALPHA1
    return np.rad2deg(2 * np.arcsin(np.clip(argument, -1, 1)))


def analyze_peaks(x, y, settings: DetectionSettings, *, limit=500, stop: Event | None = None) -> dict:
    """Return fitted physical reflections plus a compact reproducible baseline."""
    settings.validate()
    stop = stop or Event()
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if x.ndim != 1 or y.shape != x.shape or not 3 <= len(x) <= 2_000_000:
        raise ValueError("Peak detection requires 3–2,000,000 paired samples.")
    if not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)) or np.any(np.diff(x) <= 0):
        raise ValueError("Peak detection requires finite, increasing X and finite intensity.")
    bx = np.linspace(x[0], x[-1], min(len(x), 20000))
    by = np.interp(bx, x, y)
    baseline = arpls_background(by, smoothness=settings.background_smoothness, cancel_event=stop)
    corrected = y - np.interp(x, bx, baseline)
    # Fit on a uniform grid so sigma and width controls are angular quantities.
    grid = np.linspace(x[0], x[-1], min(len(x), 100000))
    values = np.interp(grid, x, corrected)
    step = float(np.median(np.diff(grid)))
    difference = np.diff(corrected)
    estimated = float(np.median(np.abs(difference - np.median(difference))) / (0.67448975 * np.sqrt(2)))
    noise = max(settings.noise_sigma or estimated, float(np.ptp(values)) * 1e-9, 1e-12)
    sigma_samples = settings.smoothing_sigma / step
    smooth = gaussian_filter1d(values, sigma_samples) if sigma_samples > 0.1 else values
    smooth_noise = noise / math.sqrt(max(1, 2 * math.sqrt(math.pi) * sigma_samples))
    threshold = max(float(np.ptp(smooth)) * settings.prominence, settings.snr * smooth_noise)
    indices, properties = find_peaks(smooth, prominence=threshold, distance=max(1, int(settings.min_fwhm / step / 2)),
                                     width=max(1, settings.min_fwhm / step * .5))
    widths = peak_widths(smooth, indices)[0] * step if len(indices) else []
    # Strongest seeds first allow fitted K-alpha2 components to suppress duplicate seeds.
    order = np.argsort(properties["prominences"])[::-1][:limit * 2]
    fitted = []
    secondary_positions: list[float] = []
    for i in order:
        if stop.is_set():
            raise InterruptedError("Peak fitting stopped")
        center = float(grid[indices[i]])
        if any(abs(center - secondary) < max(settings.min_fwhm, 2 * step) for secondary in secondary_positions):
            continue
        width = float(np.clip(widths[i], settings.min_fwhm, settings.max_fwhm))
        half = max(width * 2.5, 0.15)
        if settings.doublet:
            half = max(half, float(alpha2_position(center) - center) + width * 2)
        mask = (grid >= center - half) & (grid <= center + half)
        tx, ty = grid[mask], values[mask]
        if len(tx) < 7:
            continue
        stride = max(1, len(tx) // 800)
        tx, ty = tx[::stride], ty[::stride]
        initial_sigma = settings.peak_sigma or width / 2.354820045
        lower_sigma, upper_sigma = settings.min_fwhm / 2.354820045, settings.max_fwhm / 2.354820045
        initial_sigma = float(np.clip(initial_sigma, lower_sigma * 1.001, upper_sigma * .999))
        def profile(parameters, tx=tx, center=center):
            if settings.peak_sigma:
                amplitude, position, offset, slope = parameters
                sigma = settings.peak_sigma
            else:
                amplitude, position, sigma, offset, slope = parameters
            model = amplitude * np.exp(-.5 * ((tx - position) / sigma) ** 2)
            if settings.doublet:
                model += amplitude * settings.alpha2_ratio * np.exp(-.5 * ((tx - alpha2_position(position)) / sigma) ** 2)
            return model + offset + slope * (tx - center)
        def residual(parameters, profile=profile, ty=ty):
            if stop.is_set():
                raise InterruptedError("Peak fitting stopped")
            return profile(parameters) - ty
        initial = [max(float(smooth[indices[i]]), noise), center, initial_sigma, 0., 0.]
        lower = [0, center - width, lower_sigma, -np.inf, -np.inf]
        upper = [np.inf, center + width, upper_sigma, np.inf, np.inf]
        if settings.peak_sigma:
            initial.pop(2)
            lower.pop(2)
            upper.pop(2)
        fit = least_squares(residual, initial, bounds=(lower, upper), x_scale="jac",
                            loss="soft_l1", f_scale=max(noise, float(np.ptp(ty)) * .02), max_nfev=150)
        amplitude, position = fit.x[:2]
        sigma = settings.peak_sigma or fit.x[2]
        snr = float(amplitude / noise)
        fwhm = float(sigma * 2.354820045)
        if not fit.success or snr < settings.snr or not settings.min_fwhm * .99 <= fwhm <= settings.max_fwhm * 1.01:
            continue
        if any(abs(position - peak["position"]) < max(step * 2, settings.min_fwhm / 2) for peak in fitted):
            continue
        ratio = settings.alpha2_ratio if settings.doublet else 0.
        peak = {"position": float(position), "intensity": float(amplitude * sigma * math.sqrt(2 * math.pi) * (1 + ratio)),
                "height": float(amplitude), "width": fwhm, "sigma": float(sigma), "snr": snr,
                "included": True, "origin": "Cu doublet fit" if settings.doublet else "Gaussian fit"}
        if settings.doublet:
            peak["alpha2_position"] = float(alpha2_position(position))
            secondary_positions.append(peak["alpha2_position"])
        fitted.append(peak)
        if len(fitted) == limit:
            break
    fitted.sort(key=lambda p: p["position"])
    return {"peaks": fitted, "baseline_x": bx.tolist(), "baseline": baseline.tolist(), "noise_sigma": noise,
            "settings": asdict(settings)}
