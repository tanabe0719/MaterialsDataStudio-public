"""Qt-independent graph state used by Igor Pro experiment export."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from materials_data_studio.core.models import PlotStyle
from materials_data_studio.plotting.plot_spec import PlotSpec

_UNIT_SUFFIX = re.compile(r"^(?P<label>.*?)\s*\((?P<unit>[^()]*)\)\s*$")


@dataclass(slots=True)
class GraphExportTrace:
    """One visible, editable Igor XY trace."""

    x: NDArray[np.generic]
    y: NDArray[np.generic]
    name: str
    style: PlotStyle = field(default_factory=PlotStyle)
    role: str = "trace"
    x_unit: str = ""
    y_unit: str = ""

    def __post_init__(self) -> None:
        self.x = np.asarray(self.x)
        self.y = np.asarray(self.y)
        if self.x.ndim != 1 or self.y.ndim != 1:
            raise ValueError("Igor graph export currently supports one-dimensional waves only.")
        if self.x.shape != self.y.shape:
            raise ValueError("Igor graph x and y arrays must have the same shape.")


@dataclass(slots=True)
class GraphExportState:
    """Scientific and persistent presentation state for one XY graph."""

    module: str
    title: str
    x_label: str
    y_label: str
    traces: list[GraphExportTrace]
    x_unit: str = ""
    y_unit: str = ""
    x_range: tuple[float, float] | None = None
    y_range: tuple[float, float] | None = None
    x_log_scale: bool = False
    y_log_scale: bool = False
    legend_enabled: bool = True


def split_axis_unit(label: str) -> tuple[str, str]:
    """Return a display label and a final parenthesized unit, when present."""
    match = _UNIT_SUFFIX.match(label.strip())
    if match is None:
        return label, ""
    return match.group("label").rstrip(), match.group("unit").strip()


def graph_export_state_from_plot_spec(spec: PlotSpec, module: str) -> GraphExportState:
    """Adapt the application's existing plot abstraction without depending on Qt."""
    _x_name, x_unit = split_axis_unit(spec.x_label)
    _y_name, y_unit = split_axis_unit(spec.y_label)
    traces = [
        GraphExportTrace(
            x=curve.x,
            y=curve.y,
            name=curve.style.legend_text or curve.label,
            style=curve.style,
            x_unit=x_unit,
            y_unit=y_unit,
        )
        for curve in spec.curves
    ]
    return GraphExportState(
        module=module,
        title=spec.title or f"{module} Graph",
        x_label=spec.x_label,
        y_label=spec.y_label,
        traces=traces,
        x_unit=x_unit,
        y_unit=y_unit,
        x_range=spec.x_range,
        y_range=spec.y_range,
        x_log_scale=spec.x_log_scale,
        y_log_scale=spec.y_log_scale,
        legend_enabled=spec.legend_enabled,
    )
