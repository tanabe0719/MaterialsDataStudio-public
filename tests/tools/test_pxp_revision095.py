"""Generated fixtures for operation display, docking and dedicated Python help."""
from __future__ import annotations

import re
import sys
from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QToolButton

from materials_data_studio.tools.command_engine import CommandEngine
from materials_data_studio.tools.command_recording import drawing_commands, semantic_events
from materials_data_studio.tools.command_services import DocumentServices
from materials_data_studio.tools.graph_document import load_graph_document, save_graph_document
from materials_data_studio.tools.graph_waves import empty_document
from materials_data_studio.tools.graph_window import GraphEditorWindow
from materials_data_studio.tools.procedure_help import EXAMPLES, REFERENCE, START, sections
from materials_data_studio.tools.procedure_policy import validate
from materials_data_studio.tools.procedure_sandbox import launch
from materials_data_studio.tools.pxp_model import PxpAnnotation, PxpDrawing, PxpImage, PxpTable


@pytest.fixture
def viewer(qtbot):
    window = GraphEditorWindow()
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    window._discard_approved = True  # pytest-qt closes before yield-fixture teardown.
    qtbot.addWidget(window)
    window.command_workspace.execute("Make/D/N=8 x,y; x=p; y=p*2; Display/N=G y vs x")
    window.command_workspace.output.clear()
    yield window
    window._discard_approved = True
    window.close()


def test_gui_trace_and_axis_properties_record_real_commands(viewer):
    workspace = viewer.command_workspace
    before = viewer.editor.document.current.clone()
    viewer.apply_properties("trace", "y", {"color": "#ff0000", "marker_fill": "#00ff00", "marker_edge": "#0000ff", "mode": 4, "marker": 19, "line_style": 2, "line_width": 2.5, "visible": False})
    viewer.apply_properties("axis", "left", {"minor_enabled": True, "major_length": 8, "thickness": 2, "font_size": 18, "minimum": 0, "maximum": 15, "label": r"\f02Signal\f00"})
    output = workspace.output.toPlainText()
    for command in ("rgb(y)=(65535,0,0)", "mrkFillRGB(y)=(0,65535,0)", "mrkStrokeRGB(y)=(0,0,65535)", "hideTrace(y)=1", "lstyle(y)=2", "SetAxis/W=G left 0,15", "minor(left)=1", "btLen(left)=8", "axThick(left)=2", "fSize(left)=18", "Label/W=G left"):
        assert command in output
    # Scalar/style operations accepted by the interpreter can be copied back.
    replay = replace(viewer.editor.document, current=before)
    engine = CommandEngine(DocumentServices(replay))
    for line in output.splitlines():
        if line.startswith(("ModifyGraph", "Label", "SetAxis")):
            result = engine.execute(line)
            assert result.success, (line, result.message)
    assert replay.graph("G").traces[0].color_igor == (65535, 0, 0)
    assert replay.graph("G").axes[0].style.font_size == 18


def test_gui_draw_add_move_delete_and_history(viewer):
    workspace = viewer.command_workspace
    graph = viewer.graph()
    drawing = PxpDrawing("DrawLine", (.1, .2, .7, .8), arrow=1, line_width=2)
    viewer.editor.edit_graph(replace(graph, drawings=(drawing,)), "Add drawing")
    assert "DrawLine/W=G 0.1,0.2,0.7,0.8" in workspace.output.toPlainText()
    assert "arrow=1" in workspace.output.toPlainText()
    workspace.output.clear()
    viewer.editor.edit_graph(replace(viewer.graph(), drawings=(replace(drawing, coordinates=(.2, .2, .8, .8)),)), "Move drawing")
    output = workspace.output.toPlainText()
    assert "DrawAction/W=G/L=UserFront delete" in output
    assert "DrawLine/W=G 0.2,0.2,0.8,0.8" in output
    viewer.editor.edit_graph(replace(viewer.graph(), drawings=()), "Clear drawing")
    assert "Clear drawing" in workspace.output.toPlainText()
    workspace.output.clear()
    viewer.undo_action.trigger()
    viewer.redo_action.trigger()
    assert workspace.output.toPlainText().splitlines() == ["Undo", "Redo"]
    workspace.output.clear()
    workspace.execute("Undo")
    assert workspace.output.toPlainText().count("Undo") == 1


@pytest.mark.parametrize("kind,coordinates,text", [
    ("DrawText", (.2, .3), "first\rsecond"), ("DrawRect", (.1, .2, .5, .8), ""),
    ("DrawOval", (.1, .2, .5, .8), ""), ("DrawRRect", (.1, .2, .5, .8), ""),
    ("DrawArc", (.1, .2, .5, .8, 0, 180), ""),
    ("DrawPoly", (.1, .2, .5, .8, .7, .4), ""),
    ("DrawBezier", (.1, .2, .3, .4, .5, .6, .7, .8), ""),
])
def test_all_drawing_tools_have_commands(kind, coordinates, text):
    result = drawing_commands("Graph name", PxpDrawing(kind, coordinates, text=text))
    assert result[0] == "SetDrawLayer/W='Graph name' UserFront"
    assert result[1].startswith("SetDrawEnv/W='Graph name'")
    assert result[2].startswith(("DrawBezier" if kind == "DrawArc" else kind) + "/W='Graph name'")
    if kind in {"DrawPoly", "DrawBezier"}:
        assert "/ABS 0,0,1,1,{" in result[2]


def test_annotation_label_drag_and_image_commands(viewer):
    workspace = viewer.command_workspace
    viewer.editor.edit_graph(replace(viewer.graph(), annotations=(PxpAnnotation("note", "one\rtwo"),)), "Add annotation")
    viewer.apply_properties("annotation", "note", {"x": .2, "y": .3, "color": "#ff0000"})
    viewer.apply_properties("label", "left", {"offset_points": 42, "lateral": .6})
    text = workspace.output.toPlainText()
    assert "TextBox/W=G/C/N=note" in text and "/G=(65535,0,0)" in text
    assert "lblPos(left)=42" in text and "lblPosMode(left)=3" in text
    assert "lblLatPos(left)=" in text and "MDS-only label lateral fraction" not in text
    assert "labels" not in viewer.editor.document.current.layout.get("G", {})
    before = viewer.editor.document.current.clone()
    after = before.clone()
    after.graphs = (replace(after.graphs[0], images=(PxpImage("root:map", "Grays", (0, 4), colorbar_visible=True, colorbar_label_raw="Intensity"),)),)
    commands = "\n".join(semantic_events(viewer.editor.document, before, after, "image settings"))
    assert "AppendImage/W=G root:map" in commands
    assert "ModifyImage/W=G map ctab={0,4,Grays,0}" in commands
    assert "ColorScale/W=G/C/N=scale0" in commands
    assert 'AppendText/W=G/N=scale0 "Intensity"' in commands


def test_remove_replace_reorder_geometry_and_table_logs(viewer):
    document = viewer.editor.document
    before = document.current.clone()
    after = before.clone()
    graph = after.graphs[0]
    extra = replace(graph.traces[0], name="second trace", y_wave="root:x")
    after.graphs = (replace(graph, traces=(extra, replace(graph.traces[0], axis="right")), style=replace(graph.style, width_points=400, margins=(("right", 24),))),)
    after.tables = (PxpTable("Table0", ("root:x", "root:y"), (("root:x", 90.),)),)
    output = "\n".join(semantic_events(document, before, after, "Edit graph"))
    for item in ("RemoveFromGraph/W=G y", "AppendToGraph/W=G/R=right root:y", "/TN='second trace'", "ReorderTraces/W=G", "width=400", "margin(right)=24", "Edit/N=Table0 root:x,root:y", "ModifyTable/W=Table0 width(root:x)=90.0"):
        assert item in output
    removed = after.clone()
    removed.graphs = ()
    removed.tables = ()
    output = "\n".join(semantic_events(document, after, removed, "Remove"))
    assert "KillWindow G" in output and "KillWindow Table0" in output


def test_log_limit_and_bad_observer_cannot_prevent_edit(viewer):
    graph = viewer.graph()
    huge = PxpDrawing("DrawPoly", tuple(float(i) for i in range(5000)))
    assert "log abbreviated" in "\n".join(drawing_commands("G", huge))
    before = viewer.editor.document.current.clone()
    after = before.clone()
    after.graphs = (replace(graph, annotations=(PxpAnnotation("note", "A" * 300000),)),)
    events = semantic_events(viewer.editor.document, before, after, "Large text")
    assert "log abbreviated" in "\n".join(events)
    viewer.editor.observers.append(lambda _: (_ for _ in ()).throw(ValueError("synthetic observer failure")))
    viewer.editor.edit_graph(replace(graph, legend_raw="retained"), "Legend")
    assert viewer.editor.document.graph("G").legend_raw == "retained"


def test_dock_back_repeated_hidden_tabified_and_relocated(viewer, qtbot):
    viewer.show()
    workspace = viewer.command_workspace
    command, procedure = workspace.console_dock, workspace.procedure_dock
    procedure.show()
    viewer.tabifyDockWidget(command, procedure)
    for dock in (command, procedure):
        for _ in range(4):
            workspace.float_dock(dock)
            qtbot.waitUntil(dock.isFloating)
            dock.hide()
            workspace.dock_back(dock)
            qtbot.waitUntil(lambda target=dock: not target.isFloating() and target.isVisible())
            assert viewer.dockWidgetArea(dock) != Qt.DockWidgetArea.NoDockWidgetArea
            assert dock.parentWidget() is viewer
            buttons = dock.titleBarWidget().findChildren(QToolButton)
            assert any("Separate window" in button.text() for button in buttons)
    viewer.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, procedure)
    workspace.float_dock(procedure)
    workspace.dock_back(procedure)
    assert viewer.dockWidgetArea(procedure) == Qt.DockWidgetArea.LeftDockWidgetArea
    assert command.isVisible() and procedure.isVisible()


def test_send_to_igor_is_available_in_editor_menu_and_toolbar(viewer):
    assert callable(viewer.send_to_igor)
    assert viewer.send_to_igor_action in viewer.file_menu.actions()
    assert viewer.send_to_igor_action in viewer.quick_toolbar.actions()
    assert not viewer.send_to_igor_action.icon().isNull()
    assert any("Save Graph Document" in action.text() for action in viewer.file_menu.actions())


def test_help_tabs_search_enter_and_reopen(viewer, qtbot):
    workspace = viewer.command_workspace
    viewer.python_help_action.trigger()
    dialog = workspace.procedure_help
    assert dialog.isVisible() and not dialog.isModal()
    assert dialog.tabs.count() == 6
    assert "Convert Igor / IPF" in dialog.browsers[3].toPlainText()
    dialog.tabs.setCurrentIndex(2)
    dialog.search.setText("wave.create")
    dialog.search.setFocus()
    qtbot.keyClick(dialog.search, Qt.Key.Key_Return)
    assert dialog.isVisible() and not dialog.search_status.text()
    assert "wave.create" in dialog.browsers[2].textCursor().selectedText()
    dialog.search.setText("not present xyz")
    dialog.find_next()
    assert dialog.search_status.text() == "Not found"
    dialog.hide()
    workspace.show_procedure_help()
    assert workspace.procedure_help is dialog and dialog.tabs.currentIndex() == 2
    assert not viewer.editor.document.current.environment.get("python_source")


@pytest.mark.skipif(sys.platform != "win32", reason="Real isolated Procedure execution")
def test_help_examples_run_in_actual_sandbox(tmp_path):
    source = "\n\n".join(block for text in (START, REFERENCE, EXAMPLES) for block in re.findall(r"```python\n(.*?)```", text, re.S))
    validate(source)
    (tmp_path / "input").mkdir()
    (tmp_path / "output").mkdir()
    save_graph_document(tmp_path / "input/input.mdsg", empty_document())
    (tmp_path / "input/procedure.py").write_text(source, encoding="utf-8")
    assert launch(tmp_path) == 0
    restored = load_graph_document(tmp_path / "output/output.mdsg")
    assert {g.name for g in restored.current.graphs} == {"Demo", "Derivative", "LinearFit"}
    assert restored.render_source().wave("trial") is None
    assert restored.render_source().wave("linear_fit") is not None
    assert "Standard errors" in (tmp_path / "output/procedure.log").read_text(encoding="utf-8")
    assert "numpy.linalg" in sections()["Modules"]
