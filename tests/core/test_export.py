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

