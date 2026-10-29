"""Synthetic regressions for multi-axis PXP imports and editing extensions."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QDialog
from tests.tools.test_graph_document import source as source_fixture
from tests.tools.test_graph_interaction import editor_window

from materials_data_studio.tools.graph_calculation import (
    WaveCalculationDialog,
    calculate_wave,
)
from materials_data_studio.tools.graph_document import (
    EditableGraphDocument,
    load_graph_document,
    save_graph_document,
)
from materials_data_studio.tools.graph_drawing import (
    DrawingArtist,
    DrawingDialog,
    drawing_operation,
    edit_drawing,
    toggle_grid,
)
from materials_data_studio.tools.graph_menus import graph_context_menu
from materials_data_studio.tools.graph_waves import delete_wave, make_wave, rename_wave
from materials_data_studio.tools.graph_window import GraphEditorWindow
from materials_data_studio.tools.pxp_model import (
    PxpDocument,
    PxpDrawing,
)
from materials_data_studio.tools.pxp_recreation import formatted_text, parse_recreation
from materials_data_studio.tools.pxp_render import render_graph
from materials_data_studio.tools.pxp_text import text_runs

source = source_fixture
window = editor_window


@pytest.mark.parametrize("flag,prefix", [("", "\n"), ("/NOCR", ""), ("/NOCR=1", ""), ("/NOCR=0", "\n")])
def test_appendtext_newline(flag: str, prefix: str) -> None:
    graph = parse_recreation(f'Window Test() : Graph\nDisplay y vs x\nTextBox/N=a "one\\rtwo"\nAppendText{flag} "three"\nEndMacro')[0][0]
    assert formatted_text(graph.annotations[0].raw) == "one\ntwo" + prefix + "three"
    assert "".join(r.text for r in text_runs(graph.annotations[0].raw, "Arial", 12)) == "one\ntwo" + prefix + "three"


def test_appendtext_named_and_colorscale() -> None:
    graph = parse_recreation('Window Test() : Graph\nAppendImage im\nTextBox/N=a "a"\nTextBox/N=b "b"\nAppendText/N=a "c"\nColorScale image=im\nAppendText "label"\nEndMacro')[0][0]
    assert graph.annotations[0].raw == "a\nc"
    assert graph.images[0].colorbar_label_raw == "label"


def test_multitrace_names_and_right_axis() -> None:
    g = parse_recreation("Window Test() : Graph\nDisplay/W=(0,0,300,200)/R y1,y2 vs x\nAppendToGraph 'liberal name' vs x\nAppendToGraph y1 vs x\nEndMacro")[0][0]
    assert [(t.name, t.axis) for t in g.traces] == [("y1", "right"), ("y2", "right"), ("liberal name", "left"), ("y1#1", "left")]
    assert g.traces[0].x_wave == "x" and g.traces[2].y_wave == "'liberal name'"


def tick_source() -> PxpDocument:
    waves = tuple(make_wave(name, data) for name, data in (("x", [0., 1., 2.]), ("a", [0., .1, .2]),
        ("b", [100., 200., 300.]), ("locations", [.0, .05, .1, .15, .2]),
        ("labels", [["zero", "Major"], ["", "Minor"], ["ten", "Major"], ["", "Emphasized"], ["twenty", "Major"]])))
    graphs = parse_recreation('Window Test() : Graph\nDisplay a vs x\nAppendToGraph/R b vs x\nModifyGraph mirror=1,tick=2,minor=1,userticks(left)={locations,labels}\nSetAxis left 0,.2\nSetAxis right 0,400\nLabel right "Temperature"\nEndMacro')[0]
    return PxpDocument("s", "synthetic.pxp", "", 0, "10", "synthetic", "1", waves=waves, graphs=graphs, status="FULL")


def test_right_axes_and_wave_ticks() -> None:
    source_doc = tick_source()
    figure = Figure()
    canvas = FigureCanvasAgg(figure)
    render_graph(figure, source_doc, source_doc.graphs[0])
    canvas.draw()
    left, right = figure.axes
    assert left.get_ylim() == (0, .2) and right.get_ylim() == (0, 400)
    assert right.yaxis.get_label_position() == "right"
    assert not right.yaxis.get_major_ticks()[0].label1.get_visible()
    assert right.yaxis.get_major_ticks()[0].label2.get_visible()
    assert not right.xaxis.get_visible()
    assert [t.get_text() for t in left.yaxis.get_ticklabels()] == ["zero", "ten", "twenty"]
    assert left.yaxis.get_minorticklocs() == pytest.approx([.05, .15])


def test_user_ticks_edit_rename_delete_and_persistence(tmp_path: Path) -> None:
    document = EditableGraphDocument.from_pxp(tick_source())
    document.current = rename_wave(document, "labels", "renamed_labels")
    axis = next(a for a in document.graph("Test").axes if a.name == "left")
    assert axis.style.user_ticks == ("locations", "root:renamed_labels")
    with pytest.raises(ValueError, match="used by graphs"):
        delete_wave(document, "locations")
    path = tmp_path / "user_ticks.mdsg"
    save_graph_document(path, document)
    restored = load_graph_document(path)
    assert restored.graph("Test").axes == document.graph("Test").axes
    restored.current = delete_wave(restored, "locations", cascade=True)
    assert next(a for a in restored.graph("Test").axes if a.name == "left").style.user_ticks is None


@pytest.mark.parametrize("expression", ["x + y", "w1 + w2", "sqrt(abs(y))", "maximum(x,y) / (x + 1)", "sin(x*pi)+cos(y)"])
def test_wave_calculation(source: PxpDocument, expression: str) -> None:
    document = EditableGraphDocument.from_pxp(source)
    before = document.current.clone()
    state = calculate_wave(document, "result", expression)
    assert state.waves is not None
    wave = next(w for w in state.waves if w.name == "result")
    assert wave.shape == (4,) and not wave.data.flags.writeable
    assert document.current == before


@pytest.mark.parametrize("expression", ["__import__('os')", "x.__class__", "x[0]", "[x for x in y]", "unknown+x", "1+2", "x + Map", "x + 'text'"])
def test_invalid_wave_calculation(source: PxpDocument, expression: str) -> None:
    document = EditableGraphDocument.from_pxp(source)
    with pytest.raises((ValueError, Exception)):
        calculate_wave(document, "result", expression)
    assert document.current.waves is None


def test_calculation_dialog_and_undo(window: GraphEditorWindow) -> None:
    assert window.editor is not None
    dialog = WaveCalculationDialog(window, window.editor.document)
    dialog.name.setText("ratio")
    dialog.expression.setText("y / x")
    dialog.calculate()
    assert dialog.result_state is not None
    assert "w1" in dialog.symbols.toPlainText()
    window.editor.commit(window.editor.document.current, dialog.result_state, "Calculate wave")
    assert window.editor.document.render_source().wave("ratio") is not None
    window.undo_action.trigger()
    assert window.editor.document.render_source().wave("ratio") is None
    assert window.redo_action.shortcut().toString() == "Ctrl+Y"
    window.redo_action.trigger()
    assert window.editor.document.render_source().wave("ratio") is not None


@pytest.mark.parametrize("kind", ["DrawText", "DrawLine", "DrawRect", "DrawRRect", "DrawOval", "DrawArc", "DrawPoly", "DrawBezier"])
def test_drawing_render_dialog_and_persistence(window: GraphEditorWindow, tmp_path: Path, kind: str) -> None:
    assert window.editor is not None
    graph = window.graph()
    assert graph is not None
    coords = (.2, .3) if kind == "DrawText" else (.2, .3, .5, .3, .5, .6, .6, .6) if kind == "DrawBezier" else (.2, .3, .6, .6)
    drawing = PxpDrawing(kind, coords, text="First\nSecond", fill_mode=1, rotation=20)
    dialog = DrawingDialog(window, drawing)
    dialog.apply()
    assert dialog.result_drawing == drawing
    window.editor.edit_graph(replace(graph, drawings=(drawing,)), "Draw")
    window.canvas.draw()
    artist = next(a for a, target in window.artist_targets.items() if target == ("drawing", "0"))
    assert isinstance(artist, DrawingArtist)
    box = artist.get_window_extent(window.canvas.get_renderer())
    assert box.width > 0 and box.height > 0
    path = tmp_path / "drawing.mdsg"
    save_graph_document(path, window.editor.document)
    assert load_graph_document(path).graph(graph.name).drawings == (drawing,)
    window.undo_action.trigger()
    assert not window.graph().drawings  # type: ignore[union-attr]


@pytest.mark.parametrize("kind", ["annotation", "label", "colorbar", "drawing"])
@pytest.mark.parametrize("delta", [(60, 20), (20, 60)])
def test_shift_axis_lock(window: GraphEditorWindow, kind: str, delta: tuple[int, int]) -> None:
    assert window.editor is not None
    if kind == "colorbar":
        window._selection = "graph", "Map"
        window._show_selection()
    if kind == "drawing":
        graph = window.graph()
        assert graph is not None
        window.editor.edit_graph(replace(graph, drawings=(PxpDrawing("DrawRect", (.1, .1, .3, .3)),)), "Add drawing")
    artist = next(a for a, target in window.artist_targets.items() if target[0] == kind)
    box = artist.get_window_extent(window.canvas.get_renderer())
    x, y = box.x0 + box.width / 2, box.y0 + box.height / 2
    window.pointer_press(SimpleNamespace(button=1, dblclick=False, x=x, y=y, inaxes=artist.axes))
    assert window._drag is not None
    anchor = window._drag["anchor"]
    window.pointer_move(SimpleNamespace(x=x + delta[0], y=y + delta[1], key="shift"))
    point = window._drag["point"]
    assert (point[1] == anchor[1]) if delta[0] > delta[1] else (point[0] == anchor[0])
    window.pointer_release(SimpleNamespace())


def test_drawing_drag_is_blitted(window: GraphEditorWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    assert window.editor is not None
    graph = window.graph()
    assert graph is not None
    window.editor.edit_graph(replace(graph, drawings=(PxpDrawing("DrawRect", (.1, .1, .25, .25)),)), "Draw")
    artist = next(a for a, target in window.artist_targets.items() if target == ("drawing", "0"))
    window.canvas.draw()
    box = artist.get_window_extent(window.canvas.get_renderer())
    x, y = (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2
    before = window.editor.document.current.clone()
    window.pointer_press(SimpleNamespace(button=1, dblclick=False, x=x, y=y, inaxes=artist.axes))
    assert window._drag is not None
    draw = window.canvas.draw
    monkeypatch.setattr(window.canvas, "draw", lambda: pytest.fail("Full redraw during drawing drag"))
    window.pointer_move(SimpleNamespace(x=x + 50, y=y + 10, key=None))
    window._paint_drag()
    assert window.editor.document.current == before
    monkeypatch.setattr(window.canvas, "draw", draw)
    window.pointer_release(SimpleNamespace())
    assert window.editor.history.count() == 2
    assert window.editor.document.current != before
    window.undo_action.trigger()
    assert window.editor.document.current == before


@pytest.mark.parametrize("operation", ["front", "back", "forward", "backward", "duplicate", "delete", "layer"])
def test_drawing_arrange(window: GraphEditorWindow, operation: str) -> None:
    assert window.editor is not None
    graph = window.graph()
    assert graph is not None
    drawings = tuple(PxpDrawing("DrawLine", (.1, .1, .2 + i*.1, .2)) for i in range(3))
    window.editor.edit_graph(replace(graph, drawings=drawings), "Drawings")
    drawing_operation(window, operation, "UserBack", index=1)
    assert window.editor.history.count() == 2
    window.undo_action.trigger()
    assert window.graph().drawings == drawings  # type: ignore[union-attr]


def test_draw_toolbar_insertion_and_context(window: GraphEditorWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    def exec_dialog(dialog: DrawingDialog) -> int:
        dialog.apply()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(DrawingDialog, "exec", exec_dialog)
    ax = window.figure.axes[0]
    start, end = ax.transAxes.transform([(.1, .8), (.3, .65)])
    window.drawing_actions["Rectangle"].trigger()
    window.pointer_press(SimpleNamespace(button=1, dblclick=False, x=start[0], y=start[1], inaxes=ax))
    window.pointer_move(SimpleNamespace(x=end[0], y=end[1]))
    window.pointer_release(SimpleNamespace(x=end[0], y=end[1]))
    assert window.graph().drawings[0].kind == "DrawRect"  # type: ignore[union-attr]
    assert window.mode == "Pointer"
    window.canvas.draw()
    artist = next(a for a, target in window.artist_targets.items() if target == ("drawing", "0"))
    box = artist.get_window_extent(window.canvas.get_renderer())
    menu = graph_context_menu(window, SimpleNamespace(x=(box.x0+box.x1)/2, y=(box.y0+box.y1)/2))
    assert menu.actions()[0].text() == "Modify Drawing…"
    next(a for a in menu.actions() if a.text() == "Duplicate").trigger()
    assert len(window.graph().drawings) == 2  # type: ignore[union-attr]
    toggle_grid(window, "drawing_grid", True)
    assert window.drawing_grid_overlay.isVisible()
    toggle_grid(window, "drawing_snap", True)
    assert window.drawing_snap
    edit_drawing(window, defaults=True)


def test_pxp_drop_accepts_only_local_files(window: GraphEditorWindow, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, qtbot) -> None:
    assert window.acceptDrops()
    path = tmp_path / "synthetic.pxp"
    path.write_bytes(b"dummy")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    event = QDragEnterEvent(QPointF(10, 10).toPoint(), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    window.dragEnterEvent(event)
    assert event.isAccepted()
    paths = []
    monkeypatch.setattr(window, "open_path", paths.append)
    dropped = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    window.dropEvent(dropped)
    assert not paths
    qtbot.waitUntil(lambda: bool(paths))
    assert paths == [path]
    mime.setUrls([QUrl("https://example.com/a.pxp")])
    event = QDragEnterEvent(QPointF(10, 10).toPoint(), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    window.dragEnterEvent(event)
    assert not event.isAccepted()
