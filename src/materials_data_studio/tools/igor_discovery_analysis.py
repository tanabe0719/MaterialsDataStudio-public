"""Bounded image masks, wave transforms and inferential statistics."""
from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Any

import numpy as np

from materials_data_studio.tools.graph_waves import make_wave
from materials_data_studio.tools.pxp_model import PxpWave


def rotate_wave(wave: PxpWave, shift: int) -> PxpWave:
    if wave.data.ndim != 1 or int(shift) != shift:
        raise ValueError("Rotate requires a 1D wave and an integer point shift")
    result = make_wave(wave.igor_path, np.roll(wave.data, int(shift)), wave)
    offset, delta = wave.dimension_scaling[0]
    return replace(result, dimension_scaling=((offset - int(shift) * delta, delta),))


def unwrap_wave(wave: PxpWave, period: float) -> PxpWave:
    if wave.data.ndim != 1 or wave.data.dtype.kind not in "biuf" or not np.isfinite(period) or period <= 0:
        raise ValueError("Unwrap requires a real 1D wave and a positive finite modulus")
    values = np.asarray(wave.data, dtype=float).copy()
    # Igor skips missing points and retains the accumulated correction across
    # gaps. Keep those slots unchanged and unwrap the finite sequence.
    finite = np.isfinite(values)
    values[finite] = np.unwrap(values[finite], period=period)
    return make_wave(wave.igor_path, values.astype(wave.data.dtype), wave)


def threshold_image(wave: PxpWave, *, threshold: float | None = None, method: int = 0,
                    invert: bool = False, background_nan: bool = False,
                    roi: PxpWave | None = None, outside: int = 1,
                    intervals: Any = None, layer: int = -1, name: str = "M_ImageThresh") -> tuple[PxpWave, float, float]:
    if wave.data.ndim not in {2, 3} or wave.data.dtype.kind not in "biuf":
        raise ValueError("ImageThreshold requires a real 2D image or 3D image stack")
    if layer != int(layer) or layer < -1 or (layer >= 0 and (wave.data.ndim != 3 or layer >= wave.shape[2])):
        raise ValueError("Invalid image layer")
    data: Any = wave.data if layer < 0 else wave.data[:, :, int(layer)]
    selected = np.ones(data.shape, bool)
    if roi is not None:
        if roi.data.dtype != np.uint8 or roi.shape != wave.shape[:2]:
            raise ValueError("ROI must be a matching unsigned-byte mask; zero pixels are inside")
        selected = np.broadcast_to(roi.data[..., None], data.shape) == 0 if data.ndim == 3 else roi.data == 0
    if outside not in {0, 1, 2}:
        raise ValueError("ROI outside mode must be 0, 1 or 2")
    finite = np.isfinite(data)
    values = np.asarray(data[finite & selected], dtype=float)
    if intervals is not None:
        pairs = np.asarray(intervals, dtype=float).ravel(order="F")
        if not len(pairs) or len(pairs) % 2 or not np.isfinite(pairs).all() or np.any(pairs[::2] > pairs[1::2]):
            raise ValueError("Threshold intervals require ordered finite low/high pairs")
        active = np.zeros(data.shape, bool)
        for low, high in zip(pairs[::2], pairs[1::2], strict=True):
            active |= (data >= low) & (data <= high)
        level = float("nan")
    else:
        if method == 0:
            if threshold is None or not np.isfinite(threshold):
                raise ValueError("Manual threshold requires finite /T")
            level = float(threshold)
        elif method == 1:
            if not len(values):
                raise ValueError("No finite ROI pixels for automatic threshold")
            level = float((values.min() + values.max()) / 2)
            for _ in range(256):
                a, b = values[values < level], values[values >= level]
                if not len(a) or not len(b):
                    break
                updated = float((np.mean(a) + np.mean(b)) / 2)
                if abs(updated - level) <= max(1, abs(level)) * 1e-12:
                    level = updated
                    break
                level = updated
        elif method == 7:
            if not len(values):
                raise ValueError("No finite ROI pixels for Otsu threshold")
            centers = np.linspace(values.min(), values.max(), 256)
            step = (values.max() - values.min()) / 255
            hist, _ = np.histogram(values, bins=np.r_[centers - step/2, centers[-1] + step/2])
            weight = np.cumsum(hist).astype(float)
            mass = np.cumsum(hist * centers)
            valid = (weight > 0) & (weight < weight[-1])
            score = np.full(256, -np.inf)
            score[valid] = (mass[-1] * weight[valid] - mass[valid] * weight[-1])**2 / (weight[valid] * (weight[-1] - weight[valid]))
            level = float(centers[np.flatnonzero(np.isclose(score, np.max(score), rtol=1e-12, atol=0))[-1]])
        else:
            raise ValueError("ImageThreshold supports manual, iterative and Otsu modes (M=0,1,7)")
        active = data > level
    if invert:
        active = ~active
    output = np.where(active, 255, 64 if background_nan else 0).astype(np.uint8)
    output[~finite] = 64
    if roi is not None and outside == 1:
        # Native ROI mode preserves original values outside the selected region.
        output = output.astype(data.dtype if data.dtype.kind == "f" else np.result_type(data.dtype, np.uint8))
        output[~selected] = data[~selected]
        output[~finite] = data[~finite]
    elif roi is not None:
        output[~selected] = 0 if outside == 0 else 64
    corr = float("nan")
    if len(values) > 1:
        a, b = data[finite & selected], output[finite & selected]
        if np.std(a) and np.std(b):
            corr = float(np.corrcoef(a, b)[0, 1])
    result = make_wave(name, output, wave)
    return replace(result, id=wave.id if result.igor_path == wave.igor_path else uuid.uuid4().hex, data_units="", wave_note=f"ImageThreshold: {wave.igor_path}; method={method}; threshold={level}; invert={invert}; outside={outside}"), level, corr


def t_test(wave: PxpWave, second: PxpWave | None = None, *, mean: float | None = None,
           paired: bool = False, tail: int = 4, alpha: float = .05,
           df_method: int = 0, confidence: bool = False) -> dict[str, float]:
    from scipy.stats import t
    if wave.data.dtype.kind not in "biuf" or (second is not None and second.data.dtype.kind not in "biuf"):
        raise ValueError("T tests require real numeric waves")
    if tail not in {1, 2, 4} or not 0 < alpha < 1 or df_method not in {0, 1, 2}:
        raise ValueError("Invalid tail, alpha or degrees-of-freedom method")
    if (second is None) != (mean is not None) or (mean is not None and not np.isfinite(mean)):
        raise ValueError("Choose one wave with /MEAN, or two waves without /MEAN")
    a = np.asarray(wave.data, dtype=float).ravel(order="F")
    b = np.asarray(second.data, dtype=float).ravel(order="F") if second is not None else None
    if paired:
        if b is None or a.shape != b.shape or confidence or df_method:
            raise ValueError("Paired tests need equal lengths and do not support CI/DFM")
        valid = np.isfinite(a) & np.isfinite(b)
        a, b = a[valid] - b[valid], None
    else:
        a = a[np.isfinite(a)]
        if b is not None:
            b = b[np.isfinite(b)]
    if len(a) < 2 or (b is not None and len(b) < 2):
        raise ValueError("At least two finite observations per distribution are required")
    n, avg, sd = len(a), float(np.mean(a)), float(np.std(a, ddof=1))
    result = {"N": float(n), "DF": float(n - 1), "Mean": avg, "StdDev": sd}
    n2, avg2, sd2 = 0, 0., 0.
    if b is None:
        difference, se, df = avg - (0 if mean is None else mean), sd / np.sqrt(n), float(n - 1)
    else:
        n2, avg2, sd2 = len(b), float(np.mean(b)), float(np.std(b, ddof=1))
        v1, v2 = sd**2/n, sd2**2/n2
        difference, se = avg-avg2, np.sqrt(v1+v2)
        df = (v1+v2)**2 / (v1**2/(n-1)+v2**2/(n2-1)) if v1+v2 else float(n+n2-2)
        df = float(np.floor(df)) if df_method == 1 else float(n+n2-2) if df_method == 2 else float(df)
        result.update(N2=float(n2), Mean2=avg2, StdDev2=sd2)
    statistic = difference / se if se else float(np.sign(difference) * np.inf) if difference else 0.
    p = float(t.sf(statistic, df)) if tail == 1 else float(t.cdf(statistic, df)) if tail == 2 else float(2*t.sf(abs(statistic), df))
    critical = float(t.ppf(1-alpha/(2 if tail == 4 else 1), df))
    if tail == 2:
        critical = -critical
    if paired:
        return dict(n=float(n), dBar=avg, sdBar=float(se), tValue=float(statistic), df=df, critical=critical, P=p)
    if second is None:
        result = dict(n1=float(n), DF1=df, avg1=avg, stdv1=sd, sxBar=float(se), t_statistic=float(statistic), critical=critical, P=p)
        if confidence:
            ci = float(t.ppf(1-alpha/2, df)) * se
            result.update(CI_low=avg-ci, CI_high=avg+ci)
    else:
        if confidence:
            raise ValueError("Two-sample native pooled-mean /CI is not supported; use separate one-sample confidence intervals")
        result = dict(n1=float(n), DF1=float(n-1), avg1=avg, stdv1=sd, n2=float(n2), DF2=float(n2-1), avg2=avg2, stdv2=sd2,
                      t_statistic=float(statistic), P=p, DFc=df, lowCritical=-abs(critical) if tail != 1 else float("-inf"),
                      highCritical=abs(critical) if tail != 2 else float("inf"))
    result["Accept"] = float(p >= alpha)
    return result
