"""Generated multi-window procedures keep browser, canvas and state in sync."""
from __future__ import annotations

import json
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import Qt

from materials_data_studio.tools.command_engine import CommandEngine
from materials_data_studio.tools.command_services import DocumentServices
from materials_data_studio.tools.graph_document import save_graph_document
from materials_data_studio.tools.graph_waves import empty_document
from materials_data_studio.tools.graph_window import GraphEditorWindow


@pytest.fixture
def window(qtbot):
    viewer = GraphEditorWindow()
    viewer.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    viewer._discard_approved = True
    viewer.bind_graph_document(empty_document())
    qtbot.addWidget(viewer)
    yield viewer
    viewer.close()


def assert_selection(window, kind, name):
    expected = (kind, name)
    assert window.tree.currentItem().data(0, Qt.ItemDataRole.UserRole) == expected
    assert window._selection == expected
    assert tuple(window.view_state["selected"]) == expected
    if kind == "graph":
        assert window.graph().name == name
        assert name in window.windowTitle()


def test_multi_graph_command_selects_same_browser_item_and_canvas(window):
    window.command_workspace.execute("Make/D/N=3 signal; Display/N=GeneratedFirst signal; Display/N=GeneratedSecond signal")
    assert_selection(window, "graph", "GeneratedSecond")


def test_isolated_multi_graph_result_commit_selects_last_active_graph(window):
    workspace = window.command_workspace
    workspace.run_editor = window.editor
    workspace.run_state = window.editor.document.current
    workspace.run_revision = window.editor.history.index()
    working = replace(window.editor.document, current=workspace.run_state.clone())
    engine = CommandEngine(DocumentServices(working))
    result = engine.execute("Make/D/N=3 signal; Display/N=GeneratedFirst signal; Display/N=GeneratedSecond signal")
    assert result.success, result.message
    workspace.work = tempfile.TemporaryDirectory(prefix="mds-synthetic-selection-")
    root = Path(workspace.work.name)
    (root / "output").mkdir()
    save_graph_document(root / "output/output.mdsg", working)
    (root / "output/output.json").write_text(json.dumps(engine.services.context), encoding="utf-8")
    workspace.finished(0, None)
    assert_selection(window, "graph", "GeneratedSecond")
    window.editor.history.undo()
    window.editor.history.redo()
    assert_selection(window, "graph", "GeneratedSecond")


def test_declined_result_selection_restores_browser_and_view_state(window, monkeypatch):
    workspace = window.command_workspace
    workspace.execute("Make/D/N=3 signal; Display/N=GeneratedFirst signal; Display/N=GeneratedSecond signal")
    workspace.engine.services.context.update(graph="GeneratedFirst", selected_kind="graph")
    workspace.select_result()
    assert_selection(window, "graph", "GeneratedFirst")
    workspace.engine.services.context.update(graph="GeneratedSecond", selected_kind="graph")
    def declined():
        window._selection = ("graph", "GeneratedFirst")
    monkeypatch.setattr(window, "_show_selection", declined)
    workspace.select_result()
    assert_selection(window, "graph", "GeneratedFirst")
