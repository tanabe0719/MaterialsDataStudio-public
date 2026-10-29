from __future__ import annotations

import zipfile
from pathlib import Path

import numpy as np
import pytest

from materials_data_studio.core.exceptions import DataImportError
from materials_data_studio.modules.xrd.parser import parse_xrd_rasx, parse_xrd_text


def test_hash_star_blank_and_whitespace(tmp_path: Path) -> None:
    path = tmp_path / "data.xy"
    path.write_text("# header\n* instrument\n\n10 100\n20\t200\n")
    result = parse_xrd_text(path)
    np.testing.assert_allclose(result.x, [10, 20])
    np.testing.assert_allclose(result.intensity, [100, 200])
    assert result.invalid_rows == 0


def test_comma_semicolon_and_explicit_delimiters(tmp_path: Path) -> None:
    comma = tmp_path / "comma.csv"
    comma.write_text("10,100\n20,200\n")
    np.testing.assert_allclose(parse_xrd_text(comma).intensity, [100, 200])
    semicolon = tmp_path / "semi.txt"
    semicolon.write_text("10;100\n20;200\n")
    np.testing.assert_allclose(
        parse_xrd_text(semicolon, delimiter=";").intensity, [100, 200]
    )


def test_invalid_rows_are_counted_and_described(tmp_path: Path) -> None:
    path = tmp_path / "mixed.dat"
    path.write_text("10 100\nbad row\n20 nope\n30 300\n")
    result = parse_xrd_text(path)
    assert result.valid_rows == 2
    assert result.invalid_rows == 2
    assert len(result.warnings) == 2
    assert all("Line" in warning for warning in result.warnings)


def test_more_than_two_columns_is_ambiguous_and_selectable(tmp_path: Path) -> None:
    path = tmp_path / "many.dat"
    path.write_text("1 10 100\n2 20 200\n")
    result = parse_xrd_text(path, x_column=1, y_column=2)
    assert result.ambiguous
    assert result.detected_columns == 3
    np.testing.assert_allclose(result.x, [10, 20])


def test_rasx_reads_first_two_profile_columns_without_ambiguity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "profile.rasx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "Data0/Profile0.txt",
            "1.80\t102\t1\n1.81\t116\t1\n1.82\tbad\t1\n",
        )
    result = parse_xrd_rasx(path)
    np.testing.assert_allclose(result.x, [1.80, 1.81])
    np.testing.assert_allclose(result.intensity, [102, 116])
    assert result.detected_columns == 3
    assert not result.ambiguous
    assert result.invalid_rows == 1


def test_rasx_requires_the_profile_member(tmp_path: Path) -> None:
    path = tmp_path / "missing.rasx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("root.xml", "<root/>")
    with pytest.raises(DataImportError, match=r"Data0/Profile0\.txt"):
        parse_xrd_rasx(path)


def test_invalid_rasx_is_reported_as_an_import_error(tmp_path: Path) -> None:
    path = tmp_path / "broken.rasx"
    path.write_text("not a ZIP")
    with pytest.raises(DataImportError, match="Could not read RASX"):
        parse_xrd_rasx(path)
