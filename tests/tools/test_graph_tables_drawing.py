"""Synthetic regression tests: display-pixel drags, gestures, tables and import."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QDialog
from tests.tools.test_graph_document import source as source_fixture
from tests.tools.test_graph_interaction import editor_window

from materials_data_studio.tools.graph_document import (
    EditableGraphDocument,
    load_graph_document,
    save_graph_document,
)
from materials_data_studio.tools.graph_drawing import (
    DrawingArtist,
    DrawingDialog,
    drawing_icon,
    drawing_transform,
)
from materials_data_studio.tools.graph_tables import (
    TableDefinitionDialog,
    TableEditorDialog,
    TableModel,
    table_state,
)
from materials_data_studio.tools.graph_wave_dialogs import TextImportDialog
from materials_data_studio.tools.graph_waves import (
    column_names,
    delete_wave,
    detect_start,
    make_wave,
    put_waves,
    rename_wave,
    text_rows,
)
from materials_data_studio.tools.graph_window import GraphEditorWindow
from materials_data_studio.tools.pxp_model import PxpDocument, PxpDrawing, PxpTable
from materials_data_studio.tools.pxp_tables import parse_tables

source = source_fixture
window = editor_window


@pytest.mark.parametrize("coord", ["prel", "abs", "bottom"])
@pytest.mark.parametrize("kind", ["DrawLine", "DrawRect", "DrawText"])
def test_drawing_tracks_exact_display_delta(
    window: GraphEditorWindow, coord: str, kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert window.editor is not None
    graph = window.graph()
    assert graph is not None
    coordinates = (0.3, 0.4) if coord == "prel" else (120, 150) if coord == "abs" else (2, 3)
    drawing = PxpDrawing(
        kind,
        coordinates
        if kind == "DrawText"
        else (*coordinates, coordinates[0] + 0.1, coordinates[1] + 0.1),
        xcoord=coord,
        ycoord=coord if coord != "bottom" else "left",
        text="text",
    )
    window.editor.edit_graph(replace(graph, drawings=(drawing,)), "Synthetic drawing")
    window.canvas.draw()
    artist = next(a for a, target in window.artist_targets.items() if target == ("drawing", "0"))
    box = artist.get_window_extent(window.canvas.get_renderer())
    x, y = box.x0 + box.width / 2, box.y0 + box.height / 2
    window.pointer_press(SimpleNamespace(button=1, dblclick=False, x=x, y=y, inaxes=artist.axes))
    assert window._drag is not None
    initial = box.bounds
    original_draw = window.canvas.draw
    monkeypatch.setattr(window.canvas, "draw", lambda: pytest.fail("Full redraw during drag"))
    for dx, dy in [(10, 5), (80, -30), (-20, 15)]:
        window.pointer_move(SimpleNamespace(x=x + dx, y=y + dy))
        box = artist.get_window_extent(window.canvas.get_renderer())
        assert box.x0 - initial[0] == pytest.approx(dx, abs=0.1)
        assert box.y0 - initial[1] == pytest.approx(dy, abs=0.1)
    window._paint_drag()
    monkeypatch.setattr(window.canvas, "draw", original_draw)
    window.pointer_release(SimpleNamespace())
    moved = window.graph().drawings[0]  # type: ignore[union-attr]
    ax = window.figure.axes[0]
    delta = drawing_transform(ax, moved).transform(
        np.array(moved.coordinates[:2])
    ) - drawing_transform(ax, drawing).transform(np.array(drawing.coordinates[:2]))
    assert delta == pytest.approx([-20, 15])


def test_arrow_heads_are_filled(window: GraphEditorWindow) -> None:
    for arrow in (1, 2, 3):
        artist = DrawingArtist(
            window.figure.axes[0], PxpDrawing("DrawLine", (0.1, 0.1, 0.3, 0.3), arrow=arrow), 1, 0
        )
        assert artist.child.get_facecolor()[3] == 1


@pytest.mark.parametrize(
    "mode", ["Line", "Arrow", "Rectangle", "Oval", "Rounded rectangle", "Arc", "Freehand"]
)
def test_mouse_first_drawing_no_options(
    window: GraphEditorWindow, mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        DrawingDialog, "exec", lambda *_: pytest.fail("Options opened while drawing")
    )
    ax = window.figure.axes[0]
    start, end = ax.transAxes.transform([(0.2, 0.7), (0.5, 0.3)])
    window.set_mode(mode)
    window.pointer_press(
        SimpleNamespace(button=1, dblclick=False, inaxes=ax, x=start[0], y=start[1])
    )
    window.pointer_move(SimpleNamespace(x=end[0], y=end[1]))
    window._paint_drag()
    window.pointer_release(SimpleNamespace(x=end[0], y=end[1]))
    assert len(window.graph().drawings) == 1  # type: ignore[union-attr]
    assert window.mode == "Pointer"
    window.pointer_key(SimpleNamespace(key="delete"))
    assert not window.graph().drawings  # type: ignore[union-attr]
    window.editor.history.undo()  # type: ignore[union-attr]
    assert len(window.graph().drawings) == 1  # type: ignore[union-attr]


def test_polygon_clicks_and_enter(window: GraphEditorWindow) -> None:
    ax = window.figure.axes[0]
    window.set_mode("Polygon")
    for point in [(0.2, 0.2), (0.4, 0.7), (0.7, 0.3)]:
        x, y = ax.transAxes.transform(point)
        event = SimpleNamespace(button=1, dblclick=False, inaxes=ax, x=x, y=y)
        window.pointer_press(event)
        window.pointer_release(event)
    assert not window.graph().drawings  # type: ignore[union-attr]
    window.pointer_key(SimpleNamespace(key="enter"))
    coords = window.graph().drawings[0].coordinates  # type: ignore[union-attr]
    assert len(coords) == 8 and coords[:2] == coords[-2:]


def test_drawing_icons_distinct(qapp: object) -> None:
    icons = [
        drawing_icon(name).pixmap(24, 24).toImage()
        for name in ("Line", "Polygon", "Freehand", "Bezier")
    ]
    assert all(a != b for i, a in enumerate(icons) for b in icons[i + 1 :])


def test_annotation_doubleclick_opens_modify(
    window: GraphEditorWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    artist = next(a for a, t in window.artist_targets.items() if t == ("annotation", "note"))
    box = artist.get_window_extent(window.canvas.get_renderer())
    calls = []
    monkeypatch.setattr(window, "edit_annotation", lambda *args: calls.append(args))
    monkeypatch.setattr(window, "edit_text", lambda *_: pytest.fail("Old text dialog opened"))
    window.pointer_press(
        SimpleNamespace(
            button=1,
            dblclick=True,
            x=box.x0 + box.width / 2,
            y=box.y0 + box.height / 2,
            inaxes=None,
        )
    )
    assert calls == [("annotation", "note")]


def test_table_parse_edit_roundtrip(source: PxpDocument, tmp_path: Path, qapp: object) -> None:
    tables = parse_tables(
        "Window Table0() : Table\nEdit/W=(0,0,200,200) x,y\nAppendToTable 'text wave'\nModifyTable width(y)=122\nEndMacro"
    )
    assert tables == (PxpTable("Table0", ("x", "y", "text wave"), (("y", 122.0),)),)
    doc = EditableGraphDocument.from_pxp(source)
    doc.current = put_waves(doc, [make_wave("text wave", ["one", "two"])])
    doc.current = table_state(doc, tables[0])
    model = TableModel(doc, tables[0], editable=True)
    assert model.rowCount() == 4 and model.columnCount() == 3
    assert model.data(model.index(3, 2)) == ""
    assert not model.setData(model.index(3, 2), "never")
    assert model.setData(model.index(0, 1), "25")
    doc.current = put_waves(doc, model.changes(), overwrite=True)
    assert source.wave("y").data[0] == 2  # type: ignore[union-attr]
    doc.current = rename_wave(doc, "y", "renamed")
    assert doc.render_source().table_definitions[0].columns[1] == "root:renamed"
    path = tmp_path / "tables.mdsg"
    save_graph_document(path, doc)
    restored = load_graph_document(path)
    assert (
        restored.current == doc.current
        or restored.render_source().table_definitions == doc.render_source().table_definitions
    )
    assert restored.render_source().wave("renamed").data[0] == 25  # type: ignore[union-attr]
    with pytest.raises(ValueError, match="tables"):
        delete_wave(restored, "renamed")
    restored.current = delete_wave(restored, "renamed", cascade=True)
    assert "root:renamed" not in restored.render_source().table_definitions[0].columns
    restored.current = table_state(restored, None, replace_name="Table0")
    assert not restored.render_source().tables and restored.render_source().waves


def test_table_dialogs(window: GraphEditorWindow) -> None:
    doc = window.editor.document  # type: ignore[union-attr]
    table = PxpTable("Table0", ("x", "y"))
    definition = TableDefinitionDialog(window, doc, table)
    definition.apply()
    assert definition.result_table == PxpTable("Table0", ("root:x", "root:y"))
    dialog = TableEditorDialog(window, doc, table)
    dialog.model.setData(dialog.model.index(0, 1), "30")
    dialog.apply()
    assert dialog.result_waves[0].data[0] == 30


@pytest.mark.parametrize(
    "delimiter,header", [(",", "Time,Signal"), ("\t", "Time\tSignal"), (" ", "Time Signal")]
)
def test_auto_skip_instrument_headers(
    tmp_path: Path, window: GraphEditorWindow, delimiter: str, header: str
) -> None:
    text = (
        "Instrument settings\nWeight"
        + delimiter
        + "5\nVoltage"
        + delimiter
        + "10\n[Data]\n"
        + header
        + "\n"
        + f"0{delimiter}1\n1{delimiter}2\n2{delimiter}3"
    )
    rows = text_rows(text)
    assert detect_start(rows) == 5 and column_names(rows, 5) == ["Time", "Signal"]
    path = tmp_path / "input.dat"
    path.write_text(text)
    dialog = TextImportDialog(window, path)
    assert dialog.start.value() == 6
    dialog.load()
    assert dialog.waves[0].name == "Time" and len(dialog.waves[0].data) == 3


def test_child_viewport_text_drop_opens_import(
    window: GraphEditorWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qtbot
) -> None:
    path = tmp_path / "input.csv"
    path.write_text("X,Y\n0,1\n1,2\n")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    event = QDragEnterEvent(
        QPointF(5, 5).toPoint(),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    assert window.file_drop_filter.eventFilter(window.canvas, event) and event.isAccepted()  # type: ignore[attr-defined]
    calls = []

    def dialog_exec(dialog: TextImportDialog) -> QDialog.DialogCode:
        calls.append(dialog.windowTitle())
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(TextImportDialog, "exec", dialog_exec)
    drop = QDropEvent(
        QPointF(5, 5),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    window.file_drop_filter.eventFilter(window.canvas, drop)  # type: ignore[attr-defined]
    assert not calls
    qtbot.waitUntil(lambda: bool(calls))
    assert calls and "Load General Text" in calls[0]
