"""Area-normalized symmetric pseudo-Voigt peak profiles."""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike, NDArray

from materials_data_studio.core.exceptions import AnalysisError


def gaussian_profile(
    x: ArrayLike, center_deg: float, fwhm_deg: float
) -> NDArray[np.float64]:
    """Return an area-normalized Gaussian expressed by its FWHM."""
    if not np.isfinite(fwhm_deg) or fwhm_deg <= 0:
        raise AnalysisError("Peak FWHM must be positive and finite.")
    values = np.asarray(x, dtype=float)
    coefficient = 2.0 * np.sqrt(np.log(2.0)) / (fwhm_deg * np.sqrt(np.pi))
    return coefficient * np.exp(
        -4.0 * np.log(2.0) * ((values - center_deg) / fwhm_deg) ** 2
    )


def lorentzian_profile(
    x: ArrayLike, center_deg: float, fwhm_deg: float
) -> NDArray[np.float64]:
    """Return an area-normalized Lorentzian expressed by its FWHM."""
    if not np.isfinite(fwhm_deg) or fwhm_deg <= 0:
        raise AnalysisError("Peak FWHM must be positive and finite.")
    values = np.asarray(x, dtype=float)
    return 2.0 / (np.pi * fwhm_deg) / (
        1.0 + 4.0 * ((values - center_deg) / fwhm_deg) ** 2
    )


def pseudo_voigt_profile(
    x: ArrayLike,
    center_deg: float,
    fwhm_deg: float,
    eta: float,
) -> NDArray[np.float64]:
    """Return an area-normalized symmetric pseudo-Voigt profile."""
    if not np.isfinite(eta) or not 0 <= eta <= 1:
        raise AnalysisError("Pseudo-Voigt eta must be between 0 and 1.")
    return (1.0 - eta) * gaussian_profile(
        x, center_deg, fwhm_deg
    ) + eta * lorentzian_profile(x, center_deg, fwhm_deg)

