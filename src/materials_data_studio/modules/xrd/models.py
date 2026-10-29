"""XRD-specific processing state."""

from __future__ import annotations

from dataclasses import dataclass, field

from materials_data_studio.core.models import AxisType, NormalizationMode


@dataclass(slots=True)
class TofCalibration:
    """Coefficients for TOF = DIFA*d² + DIFC*d + TZERO."""

    difa: float = 0.0
    difc: float = 0.0
    tzero: float = 0.0

    @property
    def valid(self) -> bool:
        """Return whether at least a linear d calibration is available."""
        return self.difc != 0.0 or self.difa != 0.0


@dataclass(slots=True)
class BackgroundSettings:
    """arPLS background parameters."""

    smoothness: float = 1.0e5
    convergence_ratio: float = 0.01
    max_iterations: int = 50
    enabled: bool = False


@dataclass(slots=True)
class XrdProcessingSettings:
    """Reversible per-dataset XRD transform settings."""

    source_axis: AxisType = AxisType.TWO_THETA
    display_axis: AxisType = AxisType.TWO_THETA
    wavelength_a: float = 1.540_598
    tof_calibration: TofCalibration | None = None
    normalization: NormalizationMode = NormalizationMode.NONE
    scale: float = 1.0
    offset: float = 0.0
    background: BackgroundSettings = field(default_factory=BackgroundSettings)
