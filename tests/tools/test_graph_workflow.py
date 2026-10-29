"""Synthetic-only working-wave, graph, annotation and native Quick Fit workflows."""

from __future__ import annotations

import json
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QMessageBox, QToolBar
from scipy.special import expit, voigt_profile
from tests.tools import test_graph_document as fixtures

from materials_data_studio.tools import graph_actions
from materials_data_studio.tools.graph_annotation_dialog import AnnotationDialog
from materials_data_studio.tools.graph_document import (
    EditableGraphDocument,
    GraphEditorController,
    load_graph_document,
    save_graph_document,
)
from materials_data_studio.tools.graph_export import data_columns
from materials_data_studio.tools.graph_fit import quick_fit, trace_values
from materials_data_studio.tools.graph_fit_dialog import QuickFitDialog, fit_state
from materials_data_studio.tools.graph_menus import add_quick_fit
from materials_data_studio.tools.graph_render import IgorMatplotlibRenderer
from materials_data_studio.tools.graph_symbols import visual_combo
from materials_data_studio.tools.graph_trace_dialogs import (
    NewGraphDialog,
    ReorderTracesDialog,
    ReplaceWaveDialog,
)
from materials_data_studio.tools.graph_wave_dialogs import (
    ScalingDialog,
    TextImportDialog,
    WaveBrowserDialog,
    WaveEditorDialog,
    WaveTableModel,
)
from materials_data_studio.tools.graph_waves import (
    delete_wave,
    detect_start,
    empty_document,
    import_columns,
    make_wave,
    put_waves,
    read_text,
    rename_wave,
    reorder_traces,
    resize_wave,
    text_rows,
    wave_references,
)
from materials_data_studio.tools.graph_window import GraphEditorWindow
from materials_data_studio.tools.pxp_model import PxpDocument

source = fixtures.source
window = fixtures.window


def test_copy_on_write_undo_and_persistence(source: PxpDocument, qapp: Any, tmp_path: Path) -> None:
    document = EditableGraphDocument.from_pxp(source)
    editor = GraphEditorController(document)
    original = source.wave("y")
    assert original is not None
    updated = make_wave("y", [20., 40., 30., 50.], original)
    editor.commit(document.current, put_waves(document, [updated], overwrite=True), "Edit wave")
    assert source.wave("y") is original and original.data[0] == 2
    assert document.render_source().wave("y").data[0] == 20  # type: ignore[union-attr]
    assert document.fidelity("XY") == "Modified"
    assert data_columns(document, "XY", ["trace:y"])[1][1][0] == 20
    editor.history.undo()
    assert document.render_source().wave("y") is original
    editor.history.redo()
    path = tmp_path / "working.mdsg"
    editor.save(path)
    restored = load_graph_document(path)
    assert restored.render_source().wave("y").data[0] == 20  # type: ignore[union-attr]
    assert restored.source.wave("y").data[0] == 2  # type: ignore[union-attr]
    assert restored.render_source().wave("x") is restored.source.wave("x")
    assert not restored.render_source().wave("y").data.flags.writeable  # type: ignore[union-attr]
    with zipfile.ZipFile(path) as archive:
        assert "arrays/working.npz" in archive.namelist()
        metadata = json.loads(archive.read("document.json"))
        assert len(metadata["current"]["working"]["waves"]) == 1
    figure = Figure()
    FigureCanvasAgg(figure)
    IgorMatplotlibRenderer().render(figure, restored, "XY")
    line = next(a for a in figure.findobj() if a.get_gid() == "trace:y")
    assert line.get_ydata()[0] == 20  # type: ignore[attr-defined]


def test_version_one_and_blank_document(qapp: Any, tmp_path: Path, source: PxpDocument) -> None:
    path = tmp_path / "legacy.mdsg"
    save_graph_document(path, EditableGraphDocument.from_pxp(source))
    with zipfile.ZipFile(path) as archive:
        members = {n: archive.read(n) for n in archive.namelist()}
    manifest = json.loads(members["manifest.json"])
    manifest["format_version"] = 1
    members["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(path, "w") as archive:
        for n, data in members.items():
            archive.writestr(n, data)
    assert load_graph_document(path).current.waves is None
    blank = empty_document()
    blank.current = put_waves(blank, [make_wave("group:a", [1, 2, 3]), make_wave("text", ["hello", "world", "long note"])])
    save_graph_document(tmp_path / "new.mdsg", blank)
    restored = load_graph_document(tmp_path / "new.mdsg")
    assert restored.render_source().wave("root:group:a") is not None
    assert restored.render_source().wave("text").data[2] == "long note"  # type: ignore[union-attr]


def test_wave_safety_and_renaming(source: PxpDocument) -> None:
    document = EditableGraphDocument.from_pxp(source)
    with pytest.raises(ValueError, match="already exists"):
        put_waves(document, [make_wave("y", [1., 2., 3., 4.])])
    with pytest.raises(ValueError, match="unique"):
        put_waves(document, [make_wave("a", [1]), make_wave("a", [2])])
    with pytest.raises(ValueError, match="lengths"):
        put_waves(document, [make_wave("y", [1., 2.])], overwrite=True)
    with pytest.raises(ValueError, match="already used"):
        rename_wave(document, "y", "x")
    document.current = rename_wave(document, "y", "newFolder:Signal")
    assert document.graph("XY").traces[0].y_wave == "root:newFolder:Signal"
    assert "root:newFolder" in document.render_source().folders
    assert wave_references(document, "root:newFolder:Signal") == ["XY"]
    with pytest.raises(ValueError, match="used by graphs"):
        delete_wave(document, "root:newFolder:Signal")
    document.current = delete_wave(document, "root:newFolder:Signal", cascade=True)
    assert not document.graph("XY").traces
    assert source.wave("y") is not None
    document.current = rename_wave(document, "map", "image_copy")
    assert document.graph("Map").images[0].wave == "root:image_copy"
    document.current = delete_wave(document, "image_copy", cascade=True)
    assert not document.graph("Map").images
    for operation in (rename_wave, delete_wave):
        with pytest.raises(ValueError, match="not found"):
            operation(document, "missing", "z") if operation is rename_wave else operation(document, "missing")


@pytest.mark.parametrize("values", [[], np.zeros((2, 2, 2, 2, 2)), np.array([object()], dtype=object)])
def test_invalid_wave_arrays(values: Any) -> None:
    with pytest.raises(ValueError):
        make_wave("a", values)


def test_point_operations_and_text_parsing(tmp_path: Path) -> None:
    wave = make_wave("a", [1., 2., 3.])
    assert len(resize_wave(wave, 5).data) == 5
    assert np.isnan(resize_wave(wave, 2, 1).data[1:3]).all()
    np.testing.assert_array_equal(resize_wave(wave, 1, 1, True).data, [1, 3])
    assert resize_wave(make_wave("txt", ["long"]), 3).data[1] == ""
    with pytest.raises(ValueError):
        resize_wave(wave, 1, 20)
    with pytest.raises(ValueError):
        resize_wave(wave, 3, 0, True)
    text = '[Header]\nSynthetic example\n[Data]\nTemperature,Comment,Signal\n1,"a,b",2D+0\n2,,3\n3,ok,\n'
    path = tmp_path / "text.dat"
    path.write_text(text, encoding="utf-8")
    rows = text_rows(read_text(path))
    start = detect_start(rows)
    assert start == 4
    waves = import_columns(rows, start, ["Temperature", "Comment", "Signal"], ["Number", "Text", "Number"])
    assert waves[1].data[0] == "a,b"
    assert waves[2].data[0] == 2 and np.isnan(waves[2].data[-1])
    assert len(import_columns(rows, start, ["T", "skip", "S"], ["Number", "Skip", "Number"])) == 2
    with pytest.raises(ValueError, match="not a number"):
        import_columns(rows, start, ["T", "comment", "S"], ["Number"] * 3)
    with pytest.raises(ValueError):
        import_columns(rows, start, ["T", "c", "S"], ["Skip"] * 3)
    assert text_rows("x\ty\n1\t2", "Tab")[1] == ["1", "2"]
    assert text_rows("x y\n1 2")[1] == ["1", "2"]
    assert text_rows("x;y\n1;2", "Semicolon")[1] == ["1", "2"]
    path.write_bytes("温度,信号\n1,2\n2,3".encode("cp932"))
    assert "温度" in read_text(path)
    path.write_bytes("温度\t信号\n1\t2".encode("utf-16"))
    assert "温度" in read_text(path)


def test_import_dialog_and_wave_table(qtbot: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "import.csv"
    path.write_text("#Generated\nx,comment,y\n1,hello,2\n2,,3\n3,world,4", encoding="utf-8")
    dialog = TextImportDialog(None, path)
    qtbot.addWidget(dialog)
    assert dialog.start.value() == 3
    dialog.load()
    assert dialog.result() == QDialog.DialogCode.Accepted and len(dialog.waves) == 3
    model = WaveTableModel(dialog.waves[2])
    assert model.rowCount() == 3 and model.columnCount() == 1
    assert model.rowCount(model.index(0, 0)) == 0
    assert model.data(QModelIndex()) is None
    assert model.headerData(1, Qt.Orientation.Vertical) == "Point 1"
    assert model.flags(model.index(0, 0)) & Qt.ItemFlag.ItemIsEditable
    assert not model.setData(model.index(0, 0), "nonsense")
    assert model.setData(model.index(0, 0), "2.12345678901234")
    assert model.result().data[0] == pytest.approx(2.12345678901234)
    editor = WaveEditorDialog(None, dialog.waves[1])
    qtbot.addWidget(editor)
    editor.table.setCurrentIndex(editor.model.index(0, 0))
    QApplication.clipboard().setText("long text value\nsecond\nthird")
    editor.paste()
    editor.table.selectAll()
    editor.copy()
    assert "long text value" in QApplication.clipboard().text()
    editor.apply()
    assert editor.result_wave is not None and editor.result_wave.data[0] == "long text value"
    integer = WaveTableModel(make_wave("integer", np.array([1], dtype=np.uint8)))
    assert not integer.setData(integer.index(0, 0), "300")
    boolean = WaveTableModel(make_wave("bool", [True]))
    assert not boolean.setData(boolean.index(0, 0), "2")
    complex_model = WaveTableModel(make_wave("complex", [1 + 2j]))
    assert complex_model.setData(complex_model.index(0, 0), "3+4j")
    matrix = WaveTableModel(make_wave("matrix", [[1., 2.], [3., 4.]]))
    assert matrix.setData(matrix.index(1, 1), "5") and matrix.result().data[1, 1] == 5


def test_browser_scaling_and_visual_styles(source: PxpDocument, qtbot: Any) -> None:
    browser = WaveBrowserDialog(None, "Waves", source.waves)
    qtbot.addWidget(browser)
    assert browser.selected_path() == "root:x"
    browser.filter_items("map")
    assert not browser.selected_path()
    scaling = ScalingDialog(None, source.waves[0])
    qtbot.addWidget(scaling)
    scaling.dimensions[0][0].setText("10")
    scaling.dimensions[0][1].setText("0.5")
    scaling.unit.setText("K")
    scaling.apply()
    assert scaling.result_wave is not None
    np.testing.assert_allclose(scaling.result_wave.coordinate(0), [10, 10.5, 11, 11.5])
    assert scaling.result_wave.data is source.waves[0].data
    for kind, count in (("line", 18), ("marker", 63)):
        combo = visual_combo(kind, 8, True)
        qtbot.addWidget(combo)
        assert combo.count() == count and combo.currentData() == 8
        assert all(not combo.itemIcon(i).isNull() for i in range(count))


def test_new_append_reorder_replace(window: GraphEditorWindow, qtbot: Any) -> None:
    assert window.editor is not None
    document = window.editor.document
    dialog = NewGraphDialog(window, document)
    qtbot.addWidget(dialog)
    dialog.y_list.item(1).setSelected(True)
    dialog.x_list.setCurrentRow(1)
    dialog.apply()
    assert dialog.result_graph is not None
    window.editor.edit_graph(dialog.result_graph, "Create graph")
    assert len(document.current.graphs) == 3
    window.editor.history.undo()
    assert len(document.current.graphs) == 2
    append = NewGraphDialog(window, document, document.graph("XY"))
    qtbot.addWidget(append)
    append.y_list.item(1).setSelected(True)
    append.x_list.setCurrentRow(1)
    append.apply()
    assert append.result_graph is not None and len(append.result_graph.traces) == 2
    order = ReorderTracesDialog(window, append.result_graph)
    qtbot.addWidget(order)
    item = order.list.takeItem(0)
    order.list.addItem(item)
    order.apply()
    assert order.result_graph is not None and order.result_graph.traces[0].name == "y#1"
    with pytest.raises(ValueError):
        reorder_traces(append.result_graph, ["y"])
    replacement = ReplaceWaveDialog(window, document, append.result_graph, "y")
    qtbot.addWidget(replacement)
    replacement.y_wave.setCurrentText("root:x")
    replacement.x_enabled.setChecked(True)
    replacement.x_wave.setCurrentText("root:y")
    replacement.apply()
    assert replacement.result_graph is not None
    assert replacement.result_graph.traces[0].y_wave == "root:x"
    assert replacement.result_graph.traces[0].x_wave == "root:y"
    image = NewGraphDialog(window, document)
    qtbot.addWidget(image)
    image.type.setCurrentIndex(1)
    image.y_list.item(0).setSelected(True)
    image.apply()
    assert image.result_graph is not None and image.result_graph.images[0].wave == "root:map"


@pytest.mark.parametrize("colorbar", [False, True])
def test_annotation_editor_preview_commit_and_roundtrip(window: GraphEditorWindow, qtbot: Any, tmp_path: Path, colorbar: bool) -> None:
    assert window.editor is not None
    document = window.editor.document
    before = document.current.clone()
    name, identifier = ("Map", "map") if colorbar else ("XY", "note")
    dialog = AnnotationDialog(window, document, name, identifier, colorbar)
    qtbot.addWidget(dialog)
    dialog.raw.setPlainText(r"\f02Synthetic label\f00")
    dialog.fields["font_size"][0].setText("16")
    dialog.fields["frame_width"][0].setText("2")
    dialog.fields["frame_color"][0].setText("#336699")
    dialog.fields["x_percent"][0].setText("10")
    if colorbar:
        dialog.fields["tick_minor"][0].setChecked(True)
        dialog.fields["tick_length"][0].setText("8")
    else:
        dialog.fields["movable"][0].setChecked(False)
        dialog.fields["rotation"][0].setText("15")
        dialog.fields["symbol_width"][0].setText("25")
    dialog.draw_preview()
    assert not dialog.error.text() and document.current == before
    dialog.apply()
    assert dialog.result() == QDialog.DialogCode.Accepted
    window.editor.edit_graph(dialog.result_graph, "Annotation", dialog.result_layout)
    save_graph_document(tmp_path / "annotation.mdsg", document)
    restored = load_graph_document(tmp_path / "annotation.mdsg")
    assert restored.current.graphs == document.current.graphs
    assert restored.current.layout == document.current.layout
    window.editor.history.undo()
    assert document.current == before


def test_annotation_duplicate_delete(window: GraphEditorWindow, qtbot: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    assert window.editor is not None
    document = window.editor.document
    dialog = AnnotationDialog(window, document, "XY", "note")
    qtbot.addWidget(dialog)
    dialog.duplicate()
    dialog.apply()
    assert len(dialog.result_graph.annotations) == 3
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes)
    deletion = AnnotationDialog(window, document, "XY", "note")
    qtbot.addWidget(deletion)
    deletion.delete()
    assert len(deletion.result_graph.annotations) == 1


@pytest.mark.parametrize("model,expression", [
    ("line", lambda x: 1 + 2 * x), ("poly:2", lambda x: 1 + 2 * x + 0.5 * x**2),
    ("poly_XOffset:2", lambda x: 1 + 2 * x + 0.5 * x**2),
    ("gauss", lambda x: 1 + 3 * np.exp(-((x - 3) / 0.8)**2)),
    ("lor", lambda x: 1 + 3 / (1 + ((x - 3) / 0.8)**2)),
    ("Voigt", lambda x: 1 + 3 * voigt_profile(x - 3, 0.5, 0.3)),
    ("exp", lambda x: 1 + 3 * np.exp(-x / 2)), ("exp_XOffset", lambda x: 1 + 3 * np.exp(-(x - 0.1) / 2)),
    ("dblexp", lambda x: 1 + 3 * np.exp(-x / 2) + np.exp(-x / 0.5)),
    ("dblexp_XOffset", lambda x: 1 + 3 * np.exp(-(x - 0.1) / 2) + np.exp(-(x - 0.1) / 0.5)),
    ("dblexp_peak", lambda x: 1 + 3 * (np.exp(-(x - 0.1) / 2) - np.exp(-(x - 0.1) / 0.4))),
    ("sin", lambda x: 1 + 2 * np.sin(x * 2)), ("Sigmoid", lambda x: 1 + 2 * expit((x - 3) / 0.5)),
    ("HillEquation", lambda x: 1 + 2 * expit(2 * (np.log(x) - np.log(3)))),
    ("Power", lambda x: 1 + 2 * x**1.5), ("Log", lambda x: 1 + 2 * np.log(x)),
    ("LogNormal", lambda x: 1 + 2 * np.exp(-0.5 * (np.log(x / 3) / 0.3)**2)),
])
def test_native_quick_fit_models(model: str, expression: Any) -> None:
    x = np.linspace(0.1, 6, 151)
    result = quick_fit(x, expression(x), model)
    assert result.rss < 1e-6
    assert len(result.errors) == len(result.values) and "±" in result.summary()
    assert np.max(np.abs(expression(result.x) - result.calculated)) < 0.002


def test_fit_selection_weights_validation_and_cancellation() -> None:
    x = np.arange(20.)
    result = quick_fit(x, 1 + 2 * x, "line", 3, 10, np.ones(20) * 0.2)
    assert result.count == 8 and result.values == pytest.approx([1, 2])
    for xdata, ydata, model in (([1, 2], [1], "line"), ([1, 1, 1], [1, 2, 3], "line"),
                              (x, x, "invalid"), (x, x, "poly:11"), (x, x, "Power"), (x, x, "LogNormal")):
        with pytest.raises(ValueError):
            quick_fit(xdata, ydata, model)
    with pytest.raises(ValueError):
        quick_fit(x, x, "line", 10, 3)
    with pytest.raises(ValueError):
        quick_fit(x, x, "line", sigma=np.ones(3))
    with pytest.raises(InterruptedError):
        quick_fit(x, x, "line", cancel=lambda: True)


def test_fit_dialog_async_and_independent_commit(window: GraphEditorWindow, qtbot: Any, tmp_path: Path) -> None:
    assert window.editor is not None
    document = window.editor.document
    dialog = QuickFitDialog(window, document, "XY", "y", "line", (1, 4), True)
    qtbot.addWidget(dialog)
    dialog.run()
    qtbot.waitUntil(lambda: dialog.worker is not None and dialog.worker.isFinished(), timeout=15000)
    qtbot.waitUntil(lambda: dialog.fit_result is not None, timeout=3000)
    dialog.apply()
    assert dialog.result_state is not None and document.current.waves is None
    window.editor.commit(document.current, dialog.result_state, "Quick Fit")
    assert len(document.graph("XY").traces) == 2
    assert len(document.render_source().waves) == 8
    save_graph_document(tmp_path / "fit.mdsg", document)
    restored = load_graph_document(tmp_path / "fit.mdsg")
    assert restored.current.layout["XY"]["fits"]
    window.undo_action.trigger()
    assert len(document.render_source().waves) == 4 and len(document.graph("XY").traces) == 1
    window.redo_action.trigger()
    assert len(document.render_source().waves) == 8


def test_swap_xy_and_unweighted_fit(source: PxpDocument) -> None:
    document = EditableGraphDocument.from_pxp(source)
    graph = replace(document.graph("XY"), swap_xy=True)
    document.current.graphs = (graph,)
    x, y, sigma = trace_values(document, "XY", "y")
    assert sigma is None and x[0] == 2 and y[0] == 1
    result = quick_fit(x, y, "line")
    document.current = fit_state(document, "XY", "y", result, False)
    trace = document.graph("XY").traces[-1]
    assert document.render_source().wave(trace.y_wave).data[0] == result.x[0]  # type: ignore[union-attr]


def test_toolbar_and_quickfit_menu(window: GraphEditorWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    actions = [a.text() for bar in window.findChildren(QToolBar) if bar.isVisible() for a in bar.actions()]
    assert all(word not in actions for word in ("Pan", "Zoom", "Add Text…"))
    assert "Pointer" in actions and "Rectangle" in actions  # Dedicated Text and Draw tools requested in v0.8.9.
    from PySide6.QtWidgets import QMenu
    menu = QMenu(window)
    calls = []
    monkeypatch.setattr(window, "quick_fit", lambda trace, model: calls.append((trace, model)))
    add_quick_fit(menu, window, "y")
    quick = menu.actions()[0].menu()
    assert quick is not None
    quick.actions()[0].trigger()
    assert calls == [("y", "line")]
    poly = next(a.menu() for a in quick.actions() if a.text() == "poly")
    assert poly is not None
    poly.actions()[0].trigger()
    assert calls[-1] == ("y", "poly:2")


@pytest.mark.parametrize("operation", ["make", "edit", "duplicate", "rename", "delete", "scaling", "redimension", "insert", "delete points", "rotate", "unwrap"])
def test_data_menu_commands_undo(window: GraphEditorWindow, monkeypatch: pytest.MonkeyPatch, operation: str) -> None:
    assert window.editor is not None
    document = window.editor.document
    document.current = put_waves(document, [make_wave("unused", [1., 2., 3., 4.])])
    window._state_changed()
    before = document.current.clone()
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args, **_kwargs: ("created", True))
    monkeypatch.setattr(QInputDialog, "getInt", lambda *_args, **_kwargs: (1, True))
    monkeypatch.setattr(QInputDialog, "getDouble", lambda *_args, **_kwargs: (6.28, True))
    monkeypatch.setattr(QInputDialog, "getItem", lambda *_args, **_kwargs: ("Double precision", True))
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes)
    def edit_exec(dialog: WaveEditorDialog) -> int:
        dialog.model.setData(dialog.model.index(0, 0), "99")
        dialog.apply()
        return QDialog.DialogCode.Accepted
    def scale_exec(dialog: ScalingDialog) -> int:
        dialog.unit.setText("K")
        dialog.apply()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(WaveEditorDialog, "exec", edit_exec)
    monkeypatch.setattr(ScalingDialog, "exec", scale_exec)
    graph_actions.wave_action(window, operation, "unused")
    assert document.current != before and window.editor.history.count() == 1
    window.editor.history.undo()
    assert document.current == before


def test_import_menu_and_new_graph(window: GraphEditorWindow, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "generated.csv"
    path.write_text("T,S\n1,2\n2,3\n3,4", encoding="utf-8")
    def import_exec(dialog: TextImportDialog) -> int:
        dialog.load()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(TextImportDialog, "exec", import_exec)
    graph_actions.import_text(window, path)
    assert window.editor is not None and window.editor.document.render_source().wave("T") is not None
    def graph_exec(dialog: NewGraphDialog) -> int:
        dialog.y_list.item(0).setSelected(True)
        dialog.apply()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(NewGraphDialog, "exec", graph_exec)
    graph_actions.graph_action(window, "new")
    assert window.graph() is not None and window.graph().name == "Graph"  # type: ignore[union-attr]
    window.undo_action.trigger()
    assert window.graph() is not None and window.graph().name in {"XY", "Map"}  # type: ignore[union-attr]
