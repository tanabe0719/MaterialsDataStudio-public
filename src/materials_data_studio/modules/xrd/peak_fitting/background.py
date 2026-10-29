"""Non-destructive background bases for XRD peak fitting."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import lsq_linear

from materials_data_studio.core.exceptions import AnalysisError


def scaled_coordinate(x: NDArray[np.float64]) -> NDArray[np.float64]:
    """Map a non-degenerate x range to [-1, 1]."""
    span = float(np.max(x) - np.min(x))
    if span <= 0:
        raise AnalysisError("The fitting x range must be non-degenerate.")
    return 2.0 * (x - float(np.min(x))) / span - 1.0


def background_basis(
    x: NDArray[np.float64], mode: str, order: int
) -> NDArray[np.float64]:
    """Build constant, polynomial, or Chebyshev background columns."""
    if not 0 <= order <= 8:
        raise AnalysisError("Background polynomial order must be between 0 and 8.")
    if mode == "constant":
        order = 0
    coordinate = scaled_coordinate(x)
    if mode == "polynomial" or mode == "constant":
        return np.column_stack([coordinate**power for power in range(order + 1)])
    if mode == "chebyshev":
        return np.asarray(
            np.polynomial.chebyshev.chebvander(coordinate, order),
            dtype=np.float64,
        )
    if mode == "current":
        return np.empty((len(x), 0), dtype=float)
    raise AnalysisError(f"Unknown fitting background mode: {mode}")


def asymmetric_reweighted_linear_fit(
    design: NDArray[np.float64],
    target: NDArray[np.float64],
    weights: NDArray[np.float64],
    lower: NDArray[np.float64],
    upper: NDArray[np.float64],
    *,
    peak_resistant: bool,
    max_iterations: int = 15,
    convergence_ratio: float = 0.01,
) -> NDArray[np.float64]:
    """Fit bounded coefficients while suppressing unmodelled positive peaks.

    The asymmetric weights follow the negative-residual distribution, as in
    arPLS. Ordinary weighted least squares is retained when no fitted
    background term is present.
    """
    if design.ndim != 2 or design.shape[0] != len(target):
        raise AnalysisError("The background design matrix is not aligned.")
    if len(weights) != len(target):
        raise AnalysisError("The background weights are not aligned.")
    asymmetric = np.ones(len(target), dtype=float)
    coefficients = np.zeros(design.shape[1], dtype=float)
    for _iteration in range(max_iterations if peak_resistant else 1):
        combined = np.maximum(weights * asymmetric, np.finfo(float).tiny)
        square_root = np.sqrt(combined)
        solution = lsq_linear(
            design * square_root[:, None],
            target * square_root,
            bounds=(lower, upper),
            method="trf",
        )
        if not solution.success:
            raise AnalysisError(
                f"The constrained linear fit failed: {solution.message}"
            )
        coefficients = np.asarray(solution.x, dtype=float)
        if not peak_resistant:
            break
        residual = target - design @ coefficients
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
        relative_change = np.linalg.norm(asymmetric - updated) / max(
            np.linalg.norm(asymmetric), np.finfo(float).eps
        )
        asymmetric = updated
        if relative_change < convergence_ratio:
            break
    return coefficients
