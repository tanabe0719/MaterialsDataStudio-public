from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from materials_data_studio.core.models import PlotStyle
from materials_data_studio.plotting.matplotlib_backend import (
    build_figure,
    export_figure,
    resolve_figure_export_path,
)
from materials_data_studio.plotting.plot_spec import CurveSpec, PlotSpec


def make_spec() -> PlotSpec:
    return PlotSpec(
        "Temperature (K)",
        "Signal",
        [
            CurveSpec(
                np.array([1.0, 2.0]),
                np.array([3.0, 4.0]),
                "sample",
                PlotStyle(
                    color="#2563EB",
                    line_style="--",
                    marker="o",
                    legend_text="Sample A",
                ),
            )
        ],
        title="Synthetic",
        x_range=(0, 3),
        y_range=(2, 5),
        grid_enabled=True,
    )


def test_build_figure_uses_shared_semantics() -> None:
    figure = build_figure(make_spec())
    axis = figure.axes[0]
    assert axis.get_xlabel() == "Temperature (K)"
    assert axis.get_ylabel() == "Signal"
    assert axis.get_title() == "Synthetic"
    assert axis.get_legend().get_texts()[0].get_text() == "Sample A"
    assert axis.lines[0].get_linestyle() == "--"


def test_logarithmic_y_axis() -> None:
    spec = make_spec()
    spec.y_log_scale = True
    figure = build_figure(spec)
    assert figure.axes[0].get_yscale() == "log"


@pytest.mark.parametrize("suffix", [".png", ".svg", ".pdf", ".tif", ".jpg"])
def test_required_figure_exports(tmp_path: Path, suffix: str) -> None:
    target = tmp_path / f"plot{suffix}"
    export_figure(target, make_spec())
    assert target.stat().st_size > 100


def test_save_dialog_filter_supplies_missing_svg_extension() -> None:
    assert resolve_figure_export_path("plot", "SVG (*.svg)") == Path("plot.svg")
    assert resolve_figure_export_path("plot.png", "SVG (*.svg)") == Path("plot.svg")
    assert resolve_figure_export_path("plot", "PDF (*.pdf)") == Path("plot.pdf")
    assert resolve_figure_export_path("plot", "TIFF (*.tif *.tiff)") == Path("plot.tif")
    assert resolve_figure_export_path("plot", "JPEG (*.jpg *.jpeg)") == Path("plot.jpg")


def test_unsupported_figure_extension() -> None:
    with pytest.raises(ValueError, match="PNG"):
        export_figure(Path("plot.bmp"), make_spec())


def test_curve_shape_validation() -> None:
    with pytest.raises(ValueError, match="same shape"):
        CurveSpec(np.array([1.0]), np.array([1.0, 2.0]), "bad")
