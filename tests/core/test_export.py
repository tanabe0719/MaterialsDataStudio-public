from __future__ import annotations

from pathlib import Path

import pytest

from materials_data_studio.core.exceptions import AnalysisError
from materials_data_studio.core.export import (
    export_itx,
    export_table,
    sanitize_column_name,
)


def test_tsv_has_metadata_ascii_columns_and_tabs(tmp_path: Path) -> None:
    target = tmp_path / "data.tsv"
    export_table(
        target,
        {"Temperature (K)": [1, 2], "χ inverse": [3.5, 4.5]},
        metadata=["sample A"],
    )
    lines = target.read_text().splitlines()
    assert lines[0] == "# sample A"
    assert lines[1] == "Temperature_K\tinverse"
    assert lines[2] == "1.0\t3.5"


def test_csv_uses_commas(tmp_path: Path) -> None:
    target = tmp_path / "data.csv"
    export_table(target, {"X": [1], "Y": [2]})
    assert target.read_text().splitlines() == ["X,Y", "1.0,2.0"]


def test_itx_structure(tmp_path: Path) -> None:
    target = tmp_path / "data.itx"
    export_itx(target, {"TwoTheta_deg": [10, 20], "Intensity_raw": [3, 4]})
    text = target.read_text(encoding="ascii")
    assert text.startswith("IGOR\nWAVES/D TwoTheta_deg Intensity_raw\nBEGIN\n")
    assert text.endswith("END\n")


def test_unequal_columns_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(AnalysisError, match="equal lengths"):
        export_table(tmp_path / "bad.tsv", {"a": [1], "b": [1, 2]})


def test_column_name_cannot_become_empty() -> None:
    with pytest.raises(ValueError):
        sanitize_column_name("χ")


def test_native_bulk_numeric_text_roundtrips_if_installed(tmp_path: Path) -> None:
    import numpy as np

    from materials_data_studio.core import native_numeric_text
    from materials_data_studio.tools.graph_waves import numeric_column

    if native_numeric_text._library() is None:
        pytest.skip("optional Rust kernels not built")
    cells = ["0", "-0", "1.25D-3", "nan", "invalid", "-Infinity", "", "2.3e-299", "1e999"] * 4000
    result = numeric_column(cells, None)
    expected = np.tile([0., -0., .00125, np.nan, np.nan, -np.inf, np.nan, 2.3e-299, np.inf], 4000)
    np.testing.assert_array_equal(result, expected)
    assert np.signbit(result[1])
    cells[0] = "1_000"
    assert numeric_column(cells, None)[0] == 1000
    destination = tmp_path / "data.csv"
    export_table(destination, {"Value": result}, metadata=["Synthetic numeric round trip"])
    restored = np.loadtxt(destination, skiprows=2)
    np.testing.assert_array_equal(restored, expected)
    assert np.signbit(restored[1])
