"""Synthetic coverage of native general-text rules, not GUI CSV import rules."""
from __future__ import annotations

import numpy as np
import pytest

from materials_data_studio.tools import igor_general_text as general
from materials_data_studio.tools.graph_waves import text_rows


def test_general_text_collapses_mixed_separators_and_does_not_create_blank_columns():
    source = (
        "Instrument header\r\nTime,Comment,Field,Temperature,Signal,Unused,\r\n"
        "0,,125, 12,3e-4,,,,\r\n1,,125,\t24,6e-4,,,,\r\n"
        "2,,125, 36,9e-4,,,,\r\n"
    )
    blocks = general.parse_general_text(source, (1, 0, 0, 0, 0))
    assert len(blocks) == 1
    np.testing.assert_allclose(blocks[0], [[0, 125, 12, .0003], [1, 125, 24, .0006], [2, 125, 36, .0009]])
    # The standard importer is deliberately column-preserving and unchanged.
    assert text_rows("0,,125\n1,,125\n", "Comma") == [["0", "", "125"], ["1", "", "125"]]


@pytest.mark.parametrize("terminator", ["\r", "\n", "\r\n"])
def test_line_terminators_and_fortran_exponents_and_explicit_missing_values(terminator):
    source = terminator.join(["columns", "1D+1, NaN, +INF", "2d+1,-Inf,3.5e-2", ""])
    block, = general.parse_general_text(source)
    np.testing.assert_allclose(block[:, 0], [10, 20])
    assert np.isnan(block[0, 1])
    assert np.isposinf(block[0, 2])
    assert np.isneginf(block[1, 1])
    assert block[1, 2] == .035


def test_block_detection_skips_isolated_numeric_metadata_and_preserves_multiple_blocks():
    source = "7\nmetadata\nx y\n0 1\n1 2\n\nnext block\n2 3 4\n3 4 5\n"
    first, second = general.parse_general_text(source)
    np.testing.assert_array_equal(first, [[0, 1], [1, 2]])
    np.testing.assert_array_equal(second, [[2, 3, 4], [3, 4, 5]])


def test_changed_numeric_width_ends_block_and_starts_a_new_one():
    first, second = general.parse_general_text("1 2\n3 4\n5 6 7\n8 9 10\n")
    np.testing.assert_array_equal(first, [[1, 2], [3, 4]])
    np.testing.assert_array_equal(second, [[5, 6, 7], [8, 9, 10]])


def test_blank_cells_collapse_and_explicit_nan_preserves_a_numeric_position():
    first, second = general.parse_general_text("1,,3\n2,,4\n5,NaN,7\n6,NaN,8\n")
    np.testing.assert_array_equal(first, [[1, 3], [2, 4]])
    assert second.shape == (2, 3)
    assert np.all(np.isnan(second[:, 1]))


def test_first_line_begins_search_instead_of_requiring_that_row_to_be_numeric():
    source = "1 2\n3 4\nseparator\nmetadata\nx y z\n5 6 7\n8 9 10\n"
    block, = general.parse_general_text(source, (0, 3, 0, 1, 1))
    np.testing.assert_array_equal(block, [[6], [9]])


def test_num_lines_limits_first_block_and_does_not_continue_into_later_blocks():
    source = "header\n1 2 3\n4 5 6\n7 8 9\n\n10 11\n12 13\n"
    one, = general.parse_general_text(source, (0, 0, 1, 1, 0))
    np.testing.assert_array_equal(one, [[2, 3]])
    two, = general.parse_general_text(source, (0, 0, 2, 0, 0))
    np.testing.assert_array_equal(two, [[1, 2, 3], [4, 5, 6]])
    first, = general.parse_general_text(source, (0, 0, 100, 0, 0))
    np.testing.assert_array_equal(first, [[1, 2, 3], [4, 5, 6], [7, 8, 9]])


@pytest.mark.parametrize("tweaks", [(0, 0, 0, 2, 0), (0, 0, 0, 1, 2)])
def test_requested_column_bounds_are_checked(tweaks):
    with pytest.raises(ValueError, match="outside"):
        general.parse_general_text("1 2\n3 4\n", tweaks)


@pytest.mark.parametrize("tweaks", [(0, 0), (0, -1, 0, 0, 0), (0, .5, 0, 0, 0), (0, True, 0, 0, 0)])
def test_invalid_tweaks_are_rejected(tweaks):
    with pytest.raises(ValueError, match="five nonnegative integers"):
        general.parse_general_text("1 2\n3 4\n", tweaks)


@pytest.mark.parametrize("source", ["", "header\n1 2\n", "1 2\ntext\n3 4\n", "1_0 2\n3_0 4\n", "1;2\n3;4\n"])
def test_no_numeric_pair_is_rejected_instead_of_guessing_a_block(source):
    with pytest.raises(ValueError, match="no numeric text block"):
        general.parse_general_text(source)


def test_column_limit_bounds_tokenization_before_retaining_a_wide_row(monkeypatch):
    monkeypatch.setattr(general, "MAX_PARSED_COLUMNS", 3)
    with pytest.raises(ValueError, match="exceeds 3 fields"):
        general.parse_general_text("1,2,3,4\n1,2,3,4\n")


def test_parsed_cell_limit_is_cumulative_across_blocks_and_headers(monkeypatch):
    monkeypatch.setattr(general, "MAX_TEXT_CELLS", 6)
    with pytest.raises(ValueError, match="cell safety limit"):
        general.parse_general_text("header\n1 2\n3 4\n5 6\n")


def test_line_limit_is_checked_even_before_requested_start(monkeypatch):
    monkeypatch.setattr(general, "MAX_TEXT_LINE", 8)
    with pytest.raises(ValueError, match="line exceeds"):
        general.parse_general_text("too long header\n1 2\n3 4\n", (0, 1, 0, 0, 0))


def test_explicit_line_count_does_not_parse_unrequested_later_content(monkeypatch):
    monkeypatch.setattr(general, "MAX_PARSED_COLUMNS", 2)
    block, = general.parse_general_text("1 2\n3 4\n5 6 7\n", (0, 0, 1, 0, 0))
    np.testing.assert_array_equal(block, [[1, 2]])
