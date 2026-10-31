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
class GraphExportText:
    trace: int
    point: int
    text: str
    font: str = "Arial"
    size: float = 10.
    color: str = "#334155"
    angle: float = 90.
    gap: float = 4.


def label_anchor(angle: float) -> str:
    # Native rotated annotations choose their equivalent anchor at the nearest
    # quarter turn. Keep the text's left-center start fixed at every angle.
    return ("LC", "MB", "RC", "MT")[int(np.floor((angle % 360 + 45) / 90)) % 4]


def point_texts(spec: PlotSpec) -> list[GraphExportText]:
    if not any(curve.point_labels for curve in spec.curves):
        return []
    from matplotlib.colors import to_hex
    return [GraphExportText(trace, point, text, curve.label_font, curve.label_size, to_hex(curve.label_color), curve.label_angle,
                            (curve.style.marker_size / 2 if curve.style.marker != "none" else 0) + 4)
            for trace, curve in enumerate(spec.curves) for point, text in enumerate(curve.point_labels)
            if text and np.isfinite([curve.x[point], curve.y[point]]).all()
            and (not spec.x_log_scale or curve.x[point] > 0) and (not spec.y_log_scale or curve.y[point] > 0)]


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
    texts: list[GraphExportText] = field(default_factory=list)
    figure_height_points: float = 283.464566929


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
        texts=point_texts(spec),
        figure_height_points=spec.figure_height_cm * 72 / 2.54,
    )
