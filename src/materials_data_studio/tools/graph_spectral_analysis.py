"""Explicit spectral methods discovered in Igor's Transforms submenu.

These are MDS workflows with documented normalization, not Igor binary parity.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np
from scipy import signal
from scipy.signal.windows import dpss

from materials_data_studio.tools.graph_waves import make_wave

SPECTRAL = {
    "periodogram": ("Periodogram", ("Rectangular PSD", "Hann PSD")),
    "lomb": ("Lomb Periodogram", ("Normalized Lomb-Scargle",)),
    "multitaper": ("MultiTaper PSD", ("DPSS NW=3, five tapers",)),
    "dwt": ("Discrete Wavelet Transform", ("Orthonormal Haar",)),
    "cwt": ("Continuous Wavelet Transform", ("Morlet power (omega=6)",)),
    "wigner": ("Wigner Transform", ("Analytic-signal Wigner-Ville",)),
    "stft": ("Short-Time Fourier Transform", ("Hann power spectrogram, 50% overlap",)),
}


def spectral(kind: str, waves: list[Any], destination: str, method: int, count: int) -> Any:
    source = waves[0]
    if len(waves) != (2 if kind == "lomb" else 1) or np.iscomplexobj(source.data):
        raise ValueError("Select one real wave; Lomb requires Y then X waves")
    a = np.asarray(source.data, dtype=float)
    n = len(a)
    offset, delta = source.dimension_scaling[0]
    if delta <= 0 or n < 8:
        raise ValueError("Spectral analysis requires >=8 points and positive sample interval")
    fs = 1/delta
    frequency_unit = "Hz" if source.dimension_units[0] == "s" else "1/"+source.dimension_units[0] if source.dimension_units[0] else ""
    scaling: tuple[tuple[float, float], ...]
    units: tuple[str, ...]
    values: Any
    if kind in {"periodogram", "multitaper"}:
        if kind == "periodogram":
            f, values = signal.periodogram(a, fs=fs, window="boxcar" if method == 0 else "hann", scaling="density")
        else:
            if n > 1000000:
                raise ValueError("MultiTaper is limited to one million points")
            tapers = dpss(n, 3, Kmax=5, sym=False)
            window: Any
            estimates = []
            for window in tapers:
                estimates.append(signal.periodogram(a, fs=fs, window=window, detrend="constant", scaling="density")[1])
            values = np.mean(estimates, axis=0)
            f = np.fft.rfftfreq(n, delta)
        scaling, units = ((float(f[0]), float(f[1]-f[0])),), (frequency_unit,)
    elif kind == "lomb":
        x = np.asarray(waves[1].data, float)
        if x.shape != a.shape or np.any(np.diff(x) <= 0) or np.ptp(a) == 0 or not 2 <= count <= 10000 or count*n > 20_000_000:
            raise ValueError("Lomb requires matching, increasing X, nonconstant Y and <=20 million frequency/point products")
        low, high = 1/np.ptp(x), .5/np.median(np.diff(x))
        if high <= low:
            raise ValueError("Insufficient span for a Lomb frequency grid")
        f = np.linspace(low, high, count)
        values = signal.lombscargle(x, a-a.mean(), 2*np.pi*f, normalize=True)
        scaling, units = ((float(f[0]), float(f[1]-f[0])),), (frequency_unit,)
    elif kind == "dwt":
        if n & (n-1):
            raise ValueError("Haar DWT requires a power-of-two length")
        current = a.copy()
        details = []
        while len(current) > 1:
            details.append((current[::2]-current[1::2])/np.sqrt(2))
            current = (current[::2]+current[1::2])/np.sqrt(2)
        values = np.concatenate([current, *reversed(details)])
        scaling, units = ((0.,1.),), ("coefficient",)
    elif kind == "stft":
        if not 4 <= count <= n:
            raise ValueError("STFT window length must be 4..input length")
        f, t, power = signal.spectrogram(a, fs=fs, window="hann", nperseg=count, noverlap=count//2, mode="psd")
        if len(t) < 2:
            raise ValueError("STFT needs at least two time windows; reduce window length")
        values = power.T
        scaling = ((offset+float(t[0]),float(t[1]-t[0])),(float(f[0]),float(f[1]-f[0])))
        units = (source.dimension_units[0],frequency_unit)
    elif kind == "cwt":
        if not 2 <= count <= min(n,512) or count*n > 2_000_000:
            raise ValueError("CWT maximum scale must be 2..min(length,512); output <=2 million pixels")
        columns = []
        for scale in range(1,count+1):
            extent = min(n, 10*scale)
            t = np.arange(-extent,extent+1)/scale
            wavelet = np.exp(6j*t)*np.exp(-t*t/2)/np.sqrt(scale*np.sqrt(np.pi))
            columns.append(abs(signal.fftconvolve(a,np.conj(wavelet[::-1]),mode="same"))**2)
        values = np.array(columns).T
        scaling, units = ((offset,delta),(1.,1.)), (source.dimension_units[0],"scale")
    else:
        if n > 1024:
            raise ValueError("Wigner-Ville is limited to 1024 input points")
        analytic = signal.hilbert(a)
        kernel = np.zeros((n,n),complex)
        for point in range(n):
            extent = min(point,n-1-point,(n-1)//2)
            lag = np.arange(-extent,extent+1)
            kernel[point,lag % n] = analytic[point+lag]*np.conj(analytic[point-lag])
        transformed: Any = np.fft.fft(kernel,axis=1)
        values = transformed.real
        scaling, units = ((offset,delta),(0.,fs/(2*n))), (source.dimension_units[0],frequency_unit)
    return replace(make_wave(destination,values),dimension_scaling=scaling,dimension_units=units,
                   wave_note=f"MDS {SPECTRAL[kind][0]}; method={method}; parameter={count}; inputs={[w.igor_path for w in waves]}")
