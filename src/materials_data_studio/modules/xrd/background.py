"""Asymmetrically reweighted penalized least-squares XRD background."""

from __future__ import annotations

from threading import Event

import numpy as np
from numpy.typing import ArrayLike, NDArray

from materials_data_studio.core.exceptions import AnalysisError


def arpls_background(
    intensity: ArrayLike,
    *,
    smoothness: float = 1.0e5,
    convergence_ratio: float = 0.01,
    max_iterations: int = 50,
    cancel_event: Event | None = None,
) -> NDArray[np.float64]:
    """Estimate a baseline using the arPLS algorithm of Baek et al.

    ``smoothness`` is the λ penalty on the second derivative. Larger values
    produce smoother backgrounds. Iteration stops when the relative weight
    change falls below ``convergence_ratio``.
    """
    from scipy.sparse import diags
    from scipy.sparse.linalg import spsolve

    values = np.asarray(intensity, dtype=float)
    if values.ndim != 1 or values.size < 3:
        raise AnalysisError("arPLS requires at least three one-dimensional points.")
    if np.any(~np.isfinite(values)):
        raise AnalysisError("arPLS intensity values must be finite.")
    if not np.isfinite(smoothness) or smoothness <= 0:
        raise AnalysisError("arPLS smoothness must be positive and finite.")
    if not 0 < convergence_ratio < 1:
        raise AnalysisError("arPLS convergence ratio must be between 0 and 1.")
    if max_iterations < 1:
        raise AnalysisError("arPLS maximum iterations must be positive.")

    point_count = values.size
    difference = diags(
        [np.ones(point_count), -2.0 * np.ones(point_count), np.ones(point_count)],
        [0, 1, 2],  # pyright: ignore[reportArgumentType]
        shape=(point_count - 2, point_count),
        format="csc",
    )
    penalty = smoothness * (difference.T @ difference)
    weights = np.ones(point_count)
    baseline = values.copy()

    for _iteration in range(max_iterations):
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Background estimation stopped")
        weight_matrix = diags(weights, offsets=0, format="csc")
        baseline = np.asarray(
            spsolve(weight_matrix + penalty, weights * values), dtype=float
        )
        residual = values - baseline
        negative = residual[residual < 0]
        if negative.size < 2:
            break
        mean = float(np.mean(negative))
        deviation = float(np.std(negative))
        if deviation <= np.finfo(float).eps:
            break
        exponent = np.clip(
            2.0 * (residual - (2.0 * deviation - mean)) / deviation,
            -60.0,
            60.0,
        )
        updated = 1.0 / (1.0 + np.exp(exponent))
        relative_change = np.linalg.norm(weights - updated) / max(
            np.linalg.norm(weights), np.finfo(float).eps
        )
        weights = updated
        if relative_change < convergence_ratio:
            break
    return baseline
