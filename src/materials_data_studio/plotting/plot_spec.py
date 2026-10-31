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
    marker_sizes: NDArray[np.float64] | None = None
    point_labels: tuple[str, ...] = ()
    label_font: str = ""
    label_size: float = 10.
    label_color: str = "#334155"
    label_angle: float = 90.

    def __post_init__(self) -> None:
        self.x = np.asarray(self.x, dtype=float)
        self.y = np.asarray(self.y, dtype=float)
        if self.x.shape != self.y.shape:
            raise ValueError("Plot x and y arrays must have the same shape.")
        if self.marker_sizes is not None:
            self.marker_sizes = np.asarray(self.marker_sizes, dtype=float)
            if self.marker_sizes.shape != self.x.shape or not np.isfinite(self.marker_sizes).all() or np.any(self.marker_sizes < 0):
                raise ValueError("Marker sizes must be finite nonnegative values paired with points")
        if self.point_labels and len(self.point_labels) != len(self.x):
            raise ValueError("Point labels must be paired with points")


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
