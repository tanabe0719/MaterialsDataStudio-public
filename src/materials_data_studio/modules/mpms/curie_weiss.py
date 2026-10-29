"""Curie-Weiss model and all fixed/free fitting combinations."""

from __future__ import annotations

from typing import Any

import numpy as np
from numpy.typing import ArrayLike, NDArray

from materials_data_studio.core.exceptions import AnalysisError
from materials_data_studio.modules.mpms.models import FitResult


def curie_weiss_susceptibility(
    temperature_k: ArrayLike, c: float, theta: float, chi0: float
) -> NDArray[np.float64]:
    """Return χ = χ0 + C/(T-theta)."""
    temperature = np.asarray(temperature_k, dtype=float)
    return chi0 + c / (temperature - theta)


def curie_weiss_inverse(
    temperature_k: ArrayLike, c: float, theta: float, chi0: float
) -> NDArray[np.float64]:
    """Return the legacy inverse-susceptibility expression."""
    temperature = np.asarray(temperature_k, dtype=float)
    return (temperature - theta) / (chi0 * (temperature - theta) + c)


def c_from_mu_eff(mu_eff: float) -> float:
    """Return C = mu_eff²/8."""
    return mu_eff**2 / 8.0


def mu_eff_from_c(c: float) -> float:
    """Return sqrt(8C), or NaN for an unphysical negative C."""
    return float(np.sqrt(8.0 * c)) if c >= 0 else float("nan")


def _errors(covariance: NDArray[np.float64], count: int) -> list[float]:
    diagonal = np.diag(covariance) if covariance.size else np.full(count, np.nan)
    return [float(value) for value in np.sqrt(diagonal)]


def fit_curie_weiss(
    temperature_k: ArrayLike,
    susceptibility_inverse: ArrayLike,
    *,
    c_init: float,
    theta_init: float = 100.0,
    chi0_init: float = 0.0,
    fix_c: bool = False,
    fix_chi0: bool = False,
    branch: str = "",
    cancel_event: Any = None,
) -> FitResult:
    """Fit inverse susceptibility using the reference implementation's parameterization."""
    from scipy.optimize import curve_fit
    def solve(model: Any, t: Any, y: Any, **options: Any) -> Any:
        def evaluate(values: Any, *parameters: float) -> Any:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Curie–Weiss fit stopped")
            return model(values, *parameters)
        return curve_fit(evaluate, t, y, **options)

    temperature = np.asarray(temperature_k, dtype=float)
    inverse = np.asarray(susceptibility_inverse, dtype=float)
    finite = np.isfinite(temperature) & np.isfinite(inverse)
    temperature = temperature[finite]
    inverse = inverse[finite]
    parameter_count = 1 + int(not fix_c) + int(not fix_chi0)
    if temperature.size <= parameter_count:
        raise AnalysisError(
            f"At least {parameter_count + 1} finite points are required for this fit mode."
        )
    try:
        if fix_c and fix_chi0:
            def theta_model(t: NDArray[np.float64], theta: float) -> NDArray[np.float64]:
                return curie_weiss_inverse(t, c_init, theta, chi0_init)

            estimates, covariance = solve(
                theta_model, temperature, inverse, p0=[theta_init], maxfev=20_000
            )
            c_fit, theta_fit, chi0_fit = c_init, float(estimates[0]), chi0_init
            c_err, chi0_err = 0.0, 0.0
            theta_err = _errors(covariance, 1)[0]
        elif fix_c:
            def theta_chi0_model(
                t: NDArray[np.float64], theta: float, chi0: float
            ) -> NDArray[np.float64]:
                return curie_weiss_inverse(t, c_init, theta, chi0)

            estimates, covariance = solve(
                theta_chi0_model,
                temperature,
                inverse,
                p0=[theta_init, chi0_init],
                maxfev=20_000,
            )
            theta_fit, chi0_fit = (float(value) for value in estimates)
            theta_err, chi0_err = _errors(covariance, 2)
            c_fit, c_err = c_init, 0.0
        elif fix_chi0:
            def c_theta_model(
                t: NDArray[np.float64], c: float, theta: float
            ) -> NDArray[np.float64]:
                return curie_weiss_inverse(t, c, theta, chi0_init)

            estimates, covariance = solve(
                c_theta_model,
                temperature,
                inverse,
                p0=[c_init, theta_init],
                maxfev=20_000,
            )
            c_fit, theta_fit = (float(value) for value in estimates)
            c_err, theta_err = _errors(covariance, 2)
            chi0_fit, chi0_err = chi0_init, 0.0
        else:
            estimates, covariance = solve(
                curie_weiss_inverse,
                temperature,
                inverse,
                p0=[c_init, theta_init, chi0_init],
                maxfev=20_000,
            )
            c_fit, theta_fit, chi0_fit = (float(value) for value in estimates)
            c_err, theta_err, chi0_err = _errors(covariance, 3)
    except (RuntimeError, ValueError, FloatingPointError) as exc:
        raise AnalysisError(f"Curie-Weiss fitting failed: {exc}") from exc

    predicted = curie_weiss_inverse(temperature, c_fit, theta_fit, chi0_fit)
    residuals = inverse - predicted
    ss_res = float(np.sum(residuals**2))
    ss_tot = float(np.sum((inverse - np.mean(inverse)) ** 2))
    r2 = float("nan") if ss_tot == 0 else 1.0 - ss_res / ss_tot
    return FitResult(
        c=c_fit,
        theta=theta_fit,
        chi0=chi0_fit,
        c_err=c_err,
        theta_err=theta_err,
        chi0_err=chi0_err,
        r2=r2,
        mu_eff_calc=mu_eff_from_c(c_fit),
        point_count=len(temperature),
        t_min=float(np.min(temperature)),
        t_max=float(np.max(temperature)),
        branch=branch,
    )
