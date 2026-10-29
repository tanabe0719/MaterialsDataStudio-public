from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from materials_data_studio.core.exceptions import DataImportError
from materials_data_studio.modules.mpms.parser import find_mpms_header, parse_mpms_dc


def write_mpms(path: Path, headings: str, rows: list[str]) -> None:
    path.write_text(
        "\n".join(
            [
                "[Header]",
                "INFO, WEIGHT,12.5,mg",
                "other,value",
                "[Data]",
                headings,
                *rows,
            ]
        )
    )


def test_header_and_column_name_detection(tmp_path: Path) -> None:
    path = tmp_path / "named.dc.dat"
    write_mpms(
        path,
        "Time Stamp (sec),Comment,Magnetic Field (Oe),Temperature (K),Long Moment (emu),Extra",
        ["1,a,1000,10,0.1,x", "2,b,1000,20,0.2,x"],
    )
    weight, header = find_mpms_header(path)
    assert weight == 12.5
    assert header == 4
    result = parse_mpms_dc(path)
    assert not result.used_legacy_columns
    np.testing.assert_allclose(result.temperature_k, [10, 20])


def test_legacy_columns_and_invalid_rows(tmp_path: Path) -> None:
    path = tmp_path / "legacy.dat"
    write_mpms(
        path,
        "A,B,C,D,E,F",
        ["1,x,1000,10,0.1,unused", "bad", "2,x,1000,nope,0.2,unused"],
    )
    result = parse_mpms_dc(path)
    assert result.used_legacy_columns
    assert result.valid_rows == 1
    assert result.invalid_rows == 2
    assert len(result.warnings) == 2


def test_missing_weight_and_data_are_reported(tmp_path: Path) -> None:
    path = tmp_path / "bad.dat"
    path.write_text("[Header]\nno data")
    with pytest.raises(DataImportError, match="weight"):
        parse_mpms_dc(path)

