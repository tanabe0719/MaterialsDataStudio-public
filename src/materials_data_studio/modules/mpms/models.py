"""MPMS analysis result models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class BranchSplit:
    """Automatic and optional user-corrected ZFC/FC boundary."""

    automatic_index: int
    manual_index: int | None = None

    @property
    def active_index(self) -> int:
        """Return the manual boundary when supplied."""
        return self.automatic_index if self.manual_index is None else self.manual_index


@dataclass(slots=True)
class FitResult:
    """Curie-Weiss estimates, uncertainties, and fit provenance."""

    c: float
    theta: float
    chi0: float
    c_err: float
    theta_err: float
    chi0_err: float
    r2: float
    mu_eff_calc: float
    point_count: int
    t_min: float
    t_max: float
    branch: str = ""

    def to_text(self) -> str:
        """Return a compact copyable report."""
        return (
            f"C = {self.c:.6g} ± {self.c_err:.3g}\n"
            f"theta = {self.theta:.6g} ± {self.theta_err:.3g} K\n"
            f"chi0 = {self.chi0:.6g} ± {self.chi0_err:.3g}\n"
            f"R² = {self.r2:.6g}\n"
            f"mu_eff = {self.mu_eff_calc:.6g} mu_B\n"
            f"Points = {self.point_count}\n"
            f"Range = {self.t_min:.6g}–{self.t_max:.6g} K\n"
            f"Branch = {self.branch or 'unspecified'}"
        )


@dataclass(slots=True)
class SweepSegment:
    """One continuous, approximately isothermal field sweep."""

    start_index: int
    stop_index: int
    nominal_temperature_k: float
    direction: str
    cycle_index: int

    @property
    def point_count(self) -> int:
        """Return the number of measurements in the inclusive segment."""
        return self.stop_index - self.start_index + 1

