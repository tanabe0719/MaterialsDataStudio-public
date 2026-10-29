"""Backend-independent plot specification."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from materials_data_studio.core.models import PlotStyle


@dataclass(slots=True)
class CurveSpec:
    """One plotted data series."""

    x: NDArray[np.float64]
    y: NDArray[np.float64]
    label: str
    style: PlotStyle = field(default_factory=PlotStyle)

    def __post_init__(self) -> None:
        self.x = np.asarray(self.x, dtype=float)
        self.y = np.asarray(self.y, dtype=float)
        if self.x.shape != self.y.shape:
            raise ValueError("Plot x and y arrays must have the same shape.")


@dataclass(slots=True)
class PlotSpec:
    """All plot semantics shared by screen preview and figure export."""

    x_label: str
    y_label: str
    curves: list[CurveSpec] = field(default_factory=list)
    title: str = ""
    x_range: tuple[float, float] | None = None
    y_range: tuple[float, float] | None = None
    legend_enabled: bool = True
    grid_enabled: bool = False
    figure_width_cm: float = 14.0
    figure_height_cm: float = 10.0
    transparent: bool = False
    dpi: int = 300
    x_log_scale: bool = False
    y_log_scale: bool = False
