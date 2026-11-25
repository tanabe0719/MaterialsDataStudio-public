"""Invented general-text workflows exercise numeric columns through graphs."""
from __future__ import annotations

import sys

import numpy as np
import pytest

from materials_data_studio.tools.command_services import DocumentServices
from materials_data_studio.tools.graph_waves import empty_document
from materials_data_studio.tools.igor_procedure_conversion import analyze_procedures, build_python
from materials_data_studio.tools.igor_virtual_io import INPUT_RESOURCES_KEY
from materials_data_studio.tools.procedure_api import install_api


@pytest.fixture
def services():
    return DocumentServices(empty_document())


def execute(services, monkeypatch, text, source):
    services.environment[INPUT_RESOURCES_KEY] = [{
        "name": "invented.txt", "path": "C:\\prepared\\invented.txt", "text": text,
    }]
    report = analyze_procedures(source)
    assert all(routine.supported for routine in report.routines)
    code = build_python(report, report.routines[0].name, [])
    monkeypatch.setitem(sys.modules, "mds", None)
    api = install_api(services)
    exec(compile(code, "Invented numeric import", "exec"), {})
    api.igor.finish()


def test_native_general_columns_with_leading_interior_and_trailing_empty_fields(services, monkeypatch):
    source = '''Function ImportInventedChannels()
    LoadWave/G/D/N=channel "invented.txt"
    String loaded=S_waveNames
    Rename $StringFromList(0,loaded),elapsed
    Rename $StringFromList(1,loaded),drive
    Rename $StringFromList(2,loaded),response
    Make/D/N=(numpnts(response)) normalized=response/drive
    Display/N=InventedResponse normalized vs elapsed
End'''
    text = "Header\nElapsed,Comment,Drive,Response,Unused\n,,0,,4,12,,,,\n,,1,,8,40,,,,\n,,2,,5,35,,,,\n"
    execute(services, monkeypatch, text, source)
    np.testing.assert_array_equal(services.wave("elapsed").data, [0, 1, 2])
    np.testing.assert_array_equal(services.wave("drive").data, [4, 8, 5])
    np.testing.assert_array_equal(services.wave("response").data, [12, 40, 35])
    np.testing.assert_array_equal(services.wave("normalized").data, [3, 5, 7])
    graph = services.graph("InventedResponse")
    assert len(graph.traces) == 1
    assert graph.traces[0].y_wave == services.wave("normalized").igor_path
    assert graph.traces[0].x_wave == services.wave("elapsed").igor_path


def test_general_width_changes_create_distinct_waves_and_nan_is_not_dropped(services, monkeypatch):
    source = '''Function ImportInventedBlocks()
    LoadWave/G/D/N=segment "invented.txt"
    Display/N=FirstInvented segment1 vs segment0
    Display/N=SecondInvented segment4 vs segment2
End'''
    text = "x y\n0,,2,,\n1,,4,,\n2,NaN,10\n3,8,12\n"
    execute(services, monkeypatch, text, source)
    np.testing.assert_array_equal(services.wave("segment0").data, [0, 1])
    np.testing.assert_array_equal(services.wave("segment1").data, [2, 4])
    np.testing.assert_array_equal(services.wave("segment2").data, [2, 3])
    np.testing.assert_allclose(services.wave("segment3").data, [np.nan, 8], equal_nan=True)
    np.testing.assert_array_equal(services.wave("segment4").data, [10, 12])
    assert len(services.document.current.graphs) == 2


def test_general_line_search_and_column_subset_feed_a_calculation(services, monkeypatch):
    source = '''Function ImportInventedSubset()
    LoadWave/G/D/N=part/L={0,3,2,1,2} "invented.txt"
    Make/D/N=2 ratio=part1/part0
    Display/N=SubsetInvented ratio
End'''
    text = "0 99\n1 98\nseparator\nheader\na b c d\n0 2 8 100\n1 4 20 200\n2 5 30 300\n"
    execute(services, monkeypatch, text, source)
    np.testing.assert_array_equal(services.wave("part0").data, [2, 4])
    np.testing.assert_array_equal(services.wave("part1").data, [8, 20])
    np.testing.assert_array_equal(services.wave("ratio").data, [4, 5])
    assert services.document.render_source().wave("part2") is None


@pytest.mark.parametrize("start", [0, 1, 4])
def test_large_graph_import_preview_and_columns_match_reference(start):
    from materials_data_studio.core.native_numeric_text import _row_library
    from materials_data_studio.tools.graph_waves import (
        column_names,
        detect_start,
        fast_text_rows,
        import_columns,
        text_rows,
    )

    if _row_library() is None:
        pytest.skip("Rust numeric text library is not built for this checkout.")
    text = "Angle_deg,Signal,Monitor,Offset\r\n" + " 1.25D+1, -2.5, +inf, -0.0\r\n" * 6000
    reference = text_rows(text, "Auto")
    indexed = fast_text_rows(text, "Auto")
    assert indexed is not None
    assert indexed.start == detect_start(reference) == 1
    assert indexed[:2] == reference[:2]
    assert indexed[-2:] == reference[-2:]
    assert column_names(indexed, start) == column_names(reference, start)
    names, kinds = ["angle", "signal", "monitor", "offset"], ["Number", "Text", "Skip", "Number"]
    expected = import_columns(reference, start, names, kinds)
    actual = import_columns(indexed, start, names, kinds)
    for wave, reference_wave in zip(actual, expected, strict=True):
        np.testing.assert_array_equal(wave.data, reference_wave.data)
    assert np.signbit(actual[-1].data[-1])
    assert fast_text_rows(text + '"number,label",1,2,3\n', "Comma") is None
    assert fast_text_rows(text + "\n", "Comma") is None
