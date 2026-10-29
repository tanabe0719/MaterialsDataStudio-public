"""Synthetic regressions for fast dragging, local history and Igor-style exports."""

from __future__ import annotations

import csv
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QMessageBox
from tests.tools.test_graph_document import source  # noqa: F401

from materials_data_studio.tools.graph_export import (
    GraphDataDialog,
    GraphicsSettings,
    SaveGraphicsDialog,
    data_columns,
    save_data,
    save_graphics,
)
from materials_data_studio.tools.graph_menus import graph_context_menu
from materials_data_studio.tools.graph_window import GraphEditorWindow
from materials_data_studio.tools.pxp_model import PxpDocument


@pytest.fixture(name="window")
def editor_window(qtbot: object, request: pytest.FixtureRequest) -> GraphEditorWindow:
    document: PxpDocument = request.getfixturevalue("source")
    result = GraphEditorWindow()
    qtbot.addWidget(result)  # type: ignore[attr-defined]
    result.bind_document(document)
    result._discard_approved = True
    result.show()
    result.canvas.draw()
    return result


def test_drag_does_not_rebuild_graph_or_mutate_document(window: GraphEditorWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    assert window.editor is not None
    artist = next(a for a, value in window.artist_targets.items() if value == ("label", "bottom"))
    box = artist.get_window_extent(window.canvas.get_renderer())
    x, y = box.x0 + box.width / 2, box.y0 + box.height / 2
    before = window.editor.document.current.clone()
    window.pointer_press(SimpleNamespace(button=1, dblclick=False, x=x, y=y, inaxes=None))
    assert window._drag is not None
    calls = []
    original = window.renderer.render
    def render(*args: object, **kwargs: object) -> object:
        calls.append(1)
        return original(*args, **kwargs)  # type: ignore[arg-type]
    monkeypatch.setattr(window.renderer, "render", render)
    draw = window.canvas.draw
    monkeypatch.setattr(window.canvas, "draw", lambda: pytest.fail("Full redraw during motion"))
    for step in range(100):
        window.pointer_move(SimpleNamespace(x=x + step / 2, y=y + step / 4))
    window._paint_drag()
    assert not calls and artist in window.artist_targets
    assert window.editor.document.current == before and window.editor.history.count() == 0
    monkeypatch.setattr(window.canvas, "draw", draw)
    window.pointer_release(SimpleNamespace())
    assert len(calls) == 1 and window.editor.history.count() == 1


def test_click_no_move_and_escape_do_not_add_history(window: GraphEditorWindow) -> None:
    assert window.editor is not None
    artist = next(a for a, value in window.artist_targets.items() if value == ("annotation", "note"))
    box = artist.get_window_extent(window.canvas.get_renderer())
    event = SimpleNamespace(button=1, dblclick=False, x=box.x0 + box.width / 2, y=box.y0 + box.height / 2, inaxes=None)
    window.pointer_press(event)
    window.pointer_release(event)
    assert window.editor.history.count() == 0
    window.pointer_press(event)
    window.pointer_move(SimpleNamespace(x=event.x + 50, y=event.y + 20))
    window.pointer_key(SimpleNamespace(key="escape"))
    assert window._drag is None and window.editor.history.count() == 0


def test_toolbar_history_is_per_window(window: GraphEditorWindow, qtbot: object) -> None:
    assert window.editor is not None
    second = GraphEditorWindow()
    qtbot.addWidget(second)  # type: ignore[attr-defined]
    second.bind_document(window.editor.document.source)
    second._discard_approved = True
    window.apply_properties("trace", "y", {"line_width": 3})
    assert window.undo_action.isEnabled() and not second.undo_action.isEnabled()
    assert not window.undo_action.icon().isNull() and not window.export_data_action.icon().isNull()
    window.undo_action.trigger()
    assert window.editor.history.index() == 0 and window.redo_action.isEnabled()
    window.redo_action.trigger()
    assert window.editor.history.index() == 1 and second.editor is not None and second.editor.history.count() == 0


@pytest.mark.parametrize("direction", ["expand", "shrink"])
@pytest.mark.parametrize("dimensions", ["both", "horizontal", "vertical"])
def test_region_menu_and_zoom_local_undo(window: GraphEditorWindow, direction: str, dimensions: str) -> None:
    assert window.editor is not None
    before = window.editor.document.current.clone()
    ax = window.figure.axes[0]
    window.region = {"ax": ax, "start": ax.transAxes.transform((0.25, 0.25)), "end": ax.transAxes.transform((0.75, 0.75))}
    menu = graph_context_menu(window, SimpleNamespace(x=0, y=0))
    assert [a.text() for a in menu.actions()] == ["Expand", "Horiz Expand", "Vert Expand", "Shrink", "Horiz Shrink", "Vert Shrink"]
    window.zoom_region(direction, dimensions)
    assert window.editor.history.count() == 1
    bottom = next(a for a in window.graph().axes if a.name == "bottom")  # type: ignore[union-attr]
    if dimensions != "vertical":
        assert bottom.limits == pytest.approx((1.5, 4.5) if direction == "expand" else (-3, 9))
    window.undo_action.trigger()
    assert window.editor.document.current == before


def test_region_mouse_gesture_and_clear(window: GraphEditorWindow) -> None:
    ax = window.figure.axes[0]
    x, y = ax.transAxes.transform((0.7, 0.7))
    window.pointer_press(SimpleNamespace(button=1, dblclick=False, x=x, y=y, inaxes=ax))
    window.pointer_move(SimpleNamespace(x=x + 30, y=y + 25))
    window.pointer_release(SimpleNamespace())
    assert window.region is not None and window.rubber_band.isVisible()
    window.clear_region()
    assert window.region is None and not window.rubber_band.isVisible()


def trace_menu(window: GraphEditorWindow) -> QMenu:
    window.canvas.draw()
    artist = next(a for a, value in window.artist_targets.items() if value == ("trace", "y"))
    x, y = artist.get_transform().transform((2, 4))
    return graph_context_menu(window, SimpleNamespace(x=x, y=y, inaxes=artist.axes, button=3))


def test_trace_menu_actions(window: GraphEditorWindow) -> None:
    window.apply_properties("trace", "y", {"color": "#123456", "marker_fill": "none"})
    menu = trace_menu(window)
    actions = {a.text(): a for a in menu.actions()}  # type: ignore[attr-defined]
    for title in ("Browse y…", "Edit y", "Remove y", "Hide y", "Duplicate y", "Replace Wave…", "Copy", "Mode", "Line Style", "Color", "Move to Other Y Axis"):
        assert title in actions
    assert actions["Edit y"].isEnabled()
    assert actions["Quick Fit"].menu() is not None and actions["Quick Fit"].isEnabled()
    line_menu = actions["Line Style"].menu()
    assert line_menu is not None and len(line_menu.actions()) == 18
    assert all(not a.icon().isNull() for a in line_menu.actions())
    marker_menu = actions["Markers"].menu()
    assert marker_menu is not None and len(marker_menu.actions()) == 63
    line_menu.actions()[2].trigger()
    assert window.graph().traces[0].line_style == 2  # type: ignore[union-attr]
    window.undo_action.trigger()
    actions["Duplicate y"].trigger()
    assert len(window.graph().traces) == 2  # type: ignore[union-attr]
    assert window.graph_layout()["view"]["traces"]["y_2"] == window.graph_layout()["view"]["traces"]["y"]
    window.undo_action.trigger()
    actions["Hide y"].trigger()
    assert window.graph_layout()["view"]["traces"]["y"]["visible"] is False
    window.undo_action.trigger()
    actions["Remove y"].trigger()
    assert not window.graph().traces  # type: ignore[union-attr]
    window.undo_action.trigger()


@pytest.mark.parametrize("format", ["svg", "pdf", "eps", "png", "jpg", "tiff", "bmp"])
@pytest.mark.parametrize("graph", ["XY", "Map"])
def test_graphics_formats_and_source_unchanged(window: GraphEditorWindow, tmp_path: Path, format: str, graph: str) -> None:
    assert window.editor is not None
    document = window.editor.document
    before = document.current.clone()
    path = tmp_path / ("graphics." + format)
    save_graphics(path, document, graph, GraphicsSettings(format, (4, 3), 100, color=False))
    assert path.stat().st_size > 500 and document.current == before
    if format in {"png", "jpg", "tiff", "bmp"}:
        with Image.open(path) as result:
            assert result.size == (400, 300)
            pixels = np.asarray(result.convert("RGB"))
            assert np.max(np.abs(pixels[..., 0].astype(int) - pixels[..., 1])) <= 2


def test_transparency_and_limits(window: GraphEditorWindow, tmp_path: Path) -> None:
    assert window.editor is not None
    document = window.editor.document
    path = tmp_path / "alpha.png"
    save_graphics(path, document, "XY", GraphicsSettings("png", (4, 3), 100, transparent=True))
    with Image.open(path) as result:
        assert np.asarray(result)[0, 0, 3] == 0
    for settings in (GraphicsSettings("emf"), GraphicsSettings("png", dpi=float("nan")),
                     GraphicsSettings("png", (100, 100), 300), GraphicsSettings("jpg", transparent=True)):
        with pytest.raises(ValueError):
            save_graphics(tmp_path / "bad", document, "XY", settings)


def test_save_graphics_dialog_settings_and_clipboard(window: GraphEditorWindow, qtbot: object) -> None:
    assert window.editor is not None
    dialog = SaveGraphicsDialog(window, window.editor.document, "XY")
    qtbot.addWidget(dialog)  # type: ignore[attr-defined]
    assert dialog.settings().format == "svg" and dialog.settings().size_inches is None
    assert not dialog.dpi.isEnabled()
    dialog.custom.setChecked(True)
    dialog.width_input.setText("10.16")
    dialog.height_input.setText("7.62")
    assert dialog.settings().size_inches == pytest.approx((4, 3))
    dialog.formats.setCurrentRow(4)
    assert dialog.dpi.isEnabled() and dialog.transparent.isEnabled()
    dialog.formats.setCurrentRow(5)
    assert not dialog.transparent.isEnabled()
    dialog.recommended.setChecked(False)
    assert not dialog.formats.item(0).isHidden() and not dialog.formats.item(0).flags() & Qt.ItemFlag.ItemIsEnabled
    from PySide6.QtWidgets import QPushButton
    next(b for b in dialog.findChildren(QPushButton) if b.text() == "To Clip").click()
    assert QApplication.clipboard().text() == dialog.preview.toPlainText()


def test_data_export_transform_errors_images(window: GraphEditorWindow, tmp_path: Path) -> None:
    assert window.editor is not None
    document = window.editor.document
    graph = document.graph("XY")
    trace = replace(graph.traces[0], multiplier=(2, -3), offset=(1, 7))
    document.current.graphs = (replace(graph, traces=(trace,), swap_xy=True), document.graph("Map"))
    columns = data_columns(document, "XY", ["trace:y"])
    np.testing.assert_array_equal(columns[0][1], document.source.wave("y").data * -3 + 7)  # type: ignore[union-attr]
    np.testing.assert_array_equal(columns[1][1], document.source.wave("x").data * 2 + 1)  # type: ignore[union-attr]
    assert columns[2][0] == "y:x_error_high"
    np.testing.assert_allclose(columns[2][1], document.source.wave("err").data * 3)  # type: ignore[union-attr]
    raw = data_columns(document, "XY", ["trace:y"], False)
    assert raw[0][1] is document.source.wave("x").data  # type: ignore[union-attr]
    image = data_columns(document, "Map", ["image:map"])
    assert len(image) == 3 and len(image[0][1]) == 12
    path = tmp_path / "all.tsv"
    save_data(path, raw + image, "\t")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle, delimiter="\t"))
    assert len(rows) == 13 and rows[-1][0] == "" and len(rows[0]) == 7
    assert not document.source.wave("x").data.flags.writeable  # type: ignore[union-attr]


def test_data_dialog_checks_only_visible(window: GraphEditorWindow, qtbot: object) -> None:
    assert window.editor is not None
    window.apply_properties("trace", "y", {"visible": False})
    dialog = GraphDataDialog(window, window.editor.document, "XY")
    qtbot.addWidget(dialog)  # type: ignore[attr-defined]
    assert dialog.items.item(0).checkState() == Qt.CheckState.Unchecked
    assert dialog.displayed.isChecked()


def test_data_integer_precision_and_empty(tmp_path: Path) -> None:
    path = tmp_path / "integer.csv"
    save_data(path, [("large", np.array([2**62 + 1], dtype=np.int64))], ",")
    assert str(2**62 + 1) in path.read_text(encoding="utf-8-sig")
    with pytest.raises(ValueError):
        save_data(path, [], ",")


def test_view_all_removes_range_overrides(window: GraphEditorWindow) -> None:
    assert window.editor is not None
    window.zoom_region("all", "both")
    assert all(axis.limits is None for axis in window.graph().axes)  # type: ignore[union-attr]
    window.undo_action.trigger()
    assert window.editor.document.fidelity("XY") == "Original"


def test_save_dialogs_extension_and_overwrite(window: GraphEditorWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert window.editor is not None
    path = tmp_path / "selected.svg"
    path.write_text("old", encoding="utf-8")
    graphics = SaveGraphicsDialog(window, window.editor.document, "XY")
    graphics.directory.setText(str(tmp_path))
    graphics.filename.setText(path.name)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.No)
    graphics.save()
    assert path.read_text() == "old" and graphics.result() != QDialog.DialogCode.Accepted
    graphics.overwrite.setChecked(True)
    graphics.save()
    assert "<svg" in path.read_text() and graphics.result() == QDialog.DialogCode.Accepted
    data = GraphDataDialog(window, window.editor.document, "XY")
    monkeypatch.setattr("materials_data_studio.tools.graph_export.QFileDialog.getSaveFileName", lambda *_args, **_kwargs: (str(tmp_path / "selected"), ""))
    data.save()
    assert data.result() == QDialog.DialogCode.Accepted and (tmp_path / "selected.csv").is_file()
    graphics.deleteLater()
    data.deleteLater()
