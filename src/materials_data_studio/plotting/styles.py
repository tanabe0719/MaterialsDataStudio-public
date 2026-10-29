"""Style mappings and centralized plotting defaults."""

from __future__ import annotations

from materials_data_studio.core.models import PlotStyle

SCIENCE_COLORS = (
    "#2563EB",
    "#16A34A",
    "#DC2626",
    "#9333EA",
    "#EA580C",
    "#0891B2",
    "#4F46E5",
    "#475569",
)


def default_plot_style(index: int, legend_text: str = "") -> PlotStyle:
    """Return a deterministic accessible style for a dataset index."""
    return PlotStyle(color=SCIENCE_COLORS[index % len(SCIENCE_COLORS)], legend_text=legend_text)


MATPLOTLIB_LINE_STYLES = {
    "solid": "-",
    "dash": "--",
    "dot": ":",
    "dash_dot": "-.",
    "none": "None",
    "-": "-",
    "--": "--",
    ":": ":",
    "-.": "-.",
}

PYQTGRAPH_LINE_STYLES = {
    "solid": "SolidLine",
    "dash": "DashLine",
    "dot": "DotLine",
    "dash_dot": "DashDotLine",
    "-": "SolidLine",
    "--": "DashLine",
    ":": "DotLine",
    "-.": "DashDotLine",
}

