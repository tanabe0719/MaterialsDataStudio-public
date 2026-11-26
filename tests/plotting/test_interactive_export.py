from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import Qt

from materials_data_studio.plotting.interactive_export import (
    InteractiveExportDialog,
    export_plot_curves,
)
from materials_data_studio.plotting.plot_spec import CurveSpec, PlotSpec


def _spec() -> PlotSpec:
    return PlotSpec(
        "Temperature (K)",
        "Signal",
        [
            CurveSpec(np.array([1.0, 2.0]), np.array([3.0, 4.0]), "Sample A"),
            CurveSpec(np.array([5.0]), np.array([6.0]), "Sample B"),
        ],
    )


def test_export_dialog_selects_every_visible_curve_by_default(qtbot) -> None:  # type: ignore[no-untyped-def]
    dialog = InteractiveExportDialog(_spec())
    qtbot.addWidget(dialog)
    assert dialog.export_kind() == "data"
    assert dialog.selected_curve_indices() == [0, 1]
    dialog.curves.item(1).setCheckState(Qt.CheckState.Unchecked)
    assert dialog.selected_curve_indices() == [0]
    dialog.tabs.setCurrentIndex(1)
    assert dialog.export_kind() == "vector"
    assert dialog.selected_format() == "svg"
    dialog.tabs.setCurrentIndex(2)
    assert dialog.export_kind() == "raster"
    assert dialog.selected_format() == "png"
    dialog.tabs.setCurrentIndex(3)
    assert dialog.export_kind() == "h5xp"
    assert dialog.selected_format() == "h5xp"


def test_multi_curve_csv_and_tsv_export_pad_shorter_curves(tmp_path: Path) -> None:
    for suffix, delimiter in ((".csv", ","), (".tsv", "\t")):
        target = tmp_path / f"curves{suffix}"
        export_plot_curves(target, _spec(), [0, 1])
        with target.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle, delimiter=delimiter))
        assert rows[0] == ["Sample_A_x", "Sample_A_y", "Sample_B_x", "Sample_B_y"]
        assert rows[1] == ["1.0", "3.0", "5.0", "6.0"]
        assert rows[2] == ["2.0", "4.0", "", ""]


def test_large_interactive_export_preserves_float_values_and_padding(tmp_path: Path) -> None:
    from materials_data_studio.core.native_numeric_text import _column_writer

    if _column_writer() is None:
        pytest.skip("Rust numerical column writer is not available.")
    x = np.linspace(12., 77., 9000)
    shorter = np.array([np.nan, np.inf, -0.0])
    spec = PlotSpec("x", "y", [CurveSpec(x, np.sin(x), "A"), CurveSpec(shorter, shorter, "B")])
    target = tmp_path / "curves.tsv"
    export_plot_curves(target, spec, [0, 1])
    with target.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle, delimiter="\t"))
    assert rows[0] == ["A_x", "A_y", "B_x", "B_y"]
    np.testing.assert_array_equal(np.asarray([row[:2] for row in rows[1:]], dtype=float),
                                  np.column_stack([x, np.sin(x)]))
    np.testing.assert_array_equal([float(row[2]) for row in rows[1:4]], shorter)
    assert np.signbit(float(rows[3][2]))
    assert rows[-1][2:] == ["", ""]
