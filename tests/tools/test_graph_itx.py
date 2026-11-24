"""Native Igor Text layout and Graph Editor integration regressions."""
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from materials_data_studio.tools.graph_actions import import_itx
from materials_data_studio.tools.graph_trace_dialogs import NewGraphDialog
from materials_data_studio.tools.graph_waves import empty_document, make_wave, put_waves
from materials_data_studio.tools.graph_window import GraphEditorWindow
from materials_data_studio.tools.igor_itx import read_itx, write_itx


def test_native_itx_matrix_does_not_execute_lines(tmp_path: Path):
    path = tmp_path / "matrix.itx"
    path.write_text(
        "IGOR\nWAVES/D/N=(2,3) Matrix\nBEGIN\n0\t10\t20\n1\t11\t21\nEND\n"
        'X SetScale/P x 0.5,0.25,"A", Matrix; SetScale/P y -2,1,"B", Matrix; SetScale d 0,0,"counts", Matrix\n'
        "X KillWaves Matrix\n",
        encoding="ascii",
    )
    wave, = read_itx(path)
    np.testing.assert_array_equal(wave.data, [[0, 10, 20], [1, 11, 21]])
    assert wave.dimension_scaling == ((0.5, 0.25), (-2., 1.))
    assert wave.dimension_units == ("A", "B")
    assert wave.data_units == "counts"


@pytest.mark.parametrize("shape", [(3,), (2, 3), (2, 3, 4), (3, 2, 4, 2)])
def test_itx_multidimensional_roundtrip(tmp_path: Path, shape: tuple[int, ...]):
    path = tmp_path / "output.itx"
    data = np.arange(np.prod(shape), dtype=np.float64).reshape(shape)
    wave = replace(make_wave("Wave", data), data_units="counts")
    write_itx(path, wave)
    restored, = read_itx(path)
    assert restored.data_units == wave.data_units
    np.testing.assert_array_equal(restored.data, data)


def test_itx_drop_creates_undoable_waves(qtbot, tmp_path: Path):
    path = tmp_path / "map.itx"
    write_itx(path, make_wave("Map", np.arange(6.).reshape(2, 3)))
    window = GraphEditorWindow()
    window._discard_approved = True
    qtbot.addWidget(window)
    window._import_dropped_paths([path])
    assert window.editor is not None
    assert window.editor.document.render_source().wave("Map").shape == (2, 3)
    import_itx(window, path)
    assert window.editor.document.render_source().wave("Map_2").shape == (2, 3)
    window.editor.history.undo()
    assert window.editor.document.render_source().wave("Map_2") is None


def test_multiple_xy_pairs_and_separate_graphs(qtbot):
    document = empty_document()
    waves = [make_wave(name, [1., 2.]) for name in ("a_x", "a_y", "b_x", "b_y")]
    document.current = put_waves(document, waves)
    dialog = NewGraphDialog(None, document)
    qtbot.addWidget(dialog)
    dialog.y_list.item(1).setSelected(True)
    dialog.x_list.setCurrentRow(1)
    dialog.queue_selection()
    dialog.y_list.item(3).setSelected(True)
    dialog.x_list.setCurrentRow(3)
    dialog.separate.setChecked(True)
    dialog.apply()
    assert [graph.traces[0].x_wave for graph in dialog.result_graphs] == ["root:a_x", "root:b_x"]
    assert [graph.traces[0].y_wave for graph in dialog.result_graphs] == ["root:a_y", "root:b_y"]


def test_itx_complex_and_dimension_metadata(tmp_path: Path):
    path = tmp_path / "complex.itx"
    wave = replace(make_wave("Waves", np.array([1+5j, 2+6j])),
                   wave_note="proof; with punctuation", dimension_labels=(("", "Second"),),
                   dimension_names=("Point type",))
    write_itx(path, wave)
    recovered, = read_itx(path)
    np.testing.assert_array_equal(recovered.data, wave.data)
    assert recovered.dimension_labels == wave.dimension_labels
    assert recovered.dimension_names == wave.dimension_names
    assert recovered.wave_note == wave.wave_note
