"""Invented reflection schemas; no user procedure text or research data."""
from __future__ import annotations

import numpy as np
import pytest

from materials_data_studio.tools.command_services import DocumentServices
from materials_data_studio.tools.graph_waves import empty_document, make_wave
from materials_data_studio.tools.igor_prf_compat import reflection_columns
from materials_data_studio.tools.igor_procedure_conversion import analyze_procedures, build_python


def table(extended=True):
    values = [[1, 0, 2, 4, 1, 16, -.02, .1, 8, 5],
              [0, 1, 3, 2, 2, 28, -.03, .2, 9, 6],
              [2, 0, 1, 6, 1, 42, -.04, .3, 7, 4]]
    if extended:
        values = [[*row[:3], 0, *row[3:]] for row in values]
    services = DocumentServices(empty_document())
    names = [f"column{i}" for i in range(len(values[0]))]
    services.write([make_wave(name, array) for name, array in zip(names, np.asarray(values).T, strict=True)])
    return services, names, "2 1 0 7 8 9\r\n"


def test_extended_mapping_retains_every_imported_column_and_warns(capsys):
    services, names, header = table()
    before = services.document.current
    result = reflection_columns(services, ";".join(names) + ";", header)
    assert result.split(";")[:-1] == [names[i] for i in (0, 1, 2, 4, 5, 6, 7, 8, 9, 10, 3)]
    assert set(result.split(";")[:-1]) == set(names)
    assert services.document.current is before
    assert "Native LoadWave/G is unchanged" in capsys.readouterr().out


def test_legacy_mapping_is_unchanged_and_quiet(capsys):
    services, names, header = table(False)
    original = ";".join(names) + ";"
    assert reflection_columns(services, original, header) == original
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("header", ["", "metadata", "0 1", "101 2", "2.5 1", "2 NaN", "2"])
def test_invalid_metadata_rejected_without_document_changes(header):
    services, names, _ = table()
    before = services.document.current
    with pytest.raises(ValueError, match="phase header"):
        reflection_columns(services, ";".join(names), header)
    assert services.document.current is before


@pytest.mark.parametrize("column,values", [
    (0, [1.5, 0, 2]), (3, [0, .5, 0]), (4, [0, 2, 4]),
    (5, [1, 3, 1]), (5, [1, 1, 1]), (6, [-1, 28, 42]),
    (6, [16, 181, 42]), (7, [0, np.nan, 0]),
])
def test_invalid_extended_schema_is_rejected(column, values):
    services, names, header = table()
    services.write([make_wave(names[column], values)], overwrite=True)
    with pytest.raises(ValueError, match="unsupported or ambiguous"):
        reflection_columns(services, ";".join(names), header)


def test_ambiguous_eleven_column_schema_is_rejected():
    services, names, header = table()
    # Both interpretations now have plausible multiplicities/phases/angles.
    services.write([make_wave(names[3], [2, 2, 2]),
                    make_wave(names[4], [1, 2, 1])], overwrite=True)
    with pytest.raises(ValueError, match="ambiguous"):
        reflection_columns(services, ";".join(names), header)


@pytest.mark.parametrize("problem", ["length", "text", "duplicate", "count"])
def test_malformed_wave_catalog_is_rejected(problem):
    services, names, header = table()
    if problem == "length":
        services.write([make_wave(names[-1], [1])], overwrite=True)
    elif problem == "text":
        services.write([make_wave(names[-1], ["a", "b", "c"])], overwrite=True)
    elif problem == "duplicate":
        names[-1] = names[0]
    else:
        names.pop()
        names.pop()
    with pytest.raises(ValueError, match="Legacy PRF"):
        reflection_columns(services, ";".join(names), header)


SOURCE = '''Function InventedReflectionReader()
    String payload="placeholder"
    String metadata=payload[0,StrSearch(payload," 999",0)]
    LoadWave/G/N "first-memory"
    String curveList=S_waveNames
    LoadWave/G/N "second-memory"
    String reflectionList=S_waveNames
    Rename $StringFromList(0,reflectionList,";"),firstIndex
    Rename $StringFromList(1,reflectionList,";"),secondIndex
    Rename $StringFromList(2,reflectionList,";"),thirdIndex
    Rename $StringFromList(4,reflectionList,";"),groupId
    Rename $StringFromList(5,reflectionList,";"),position
    Rename $StringFromList(6,reflectionList,";"),shift
End'''


def test_converter_captures_names_structurally_and_emits_explicit_compatibility():
    report = analyze_procedures(SOURCE)
    routine = report.routines[0]
    assert routine.supported
    assert any("native LoadWave/G is unchanged" in d.message for d in routine.diagnostics)
    code = build_python(report, routine.name, [])
    inserted = "scope.prf_columns('reflectionList', 'metadata')"
    assert code.count(inserted) == 1
    assert code.index(inserted) < code.index("scope.command('Rename")
    renamed = SOURCE.replace("reflectionList", "differentNames").replace("metadata", "differentHeader")
    report = analyze_procedures(renamed)
    assert "scope.prf_columns('differentNames', 'differentHeader')" in build_python(report, report.routines[0].name, [])


@pytest.mark.parametrize("source", [
    SOURCE.replace('" 999"', '"marker"'),
    SOURCE.replace('LoadWave/G/N "first-memory"', 'Print "no import"'),
    SOURCE.replace('StringFromList(4,reflectionList', 'StringFromList(3,reflectionList'),
    SOURCE.replace('String reflectionList=S_waveNames', 'String reflectionList="unrelated"'),
])
def test_ordinary_general_text_and_other_rename_schemas_are_not_adapted(source):
    report = analyze_procedures(source)
    assert report.routines[0].supported
    assert "scope.prf_columns" not in build_python(report, report.routines[0].name, [])
