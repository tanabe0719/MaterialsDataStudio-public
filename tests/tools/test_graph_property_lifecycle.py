"""Disposed Properties must never outlive graph/non-graph selection changes."""
from dataclasses import replace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QMessageBox, QTreeWidgetItemIterator
from shiboken6 import isValid
from tests.tools.test_graph_document import source  # noqa: F401

from materials_data_studio.tools.graph_window import GraphEditorWindow
from materials_data_studio.tools.pxp_model import PxpTable


@pytest.fixture(name="window")
def editor_window(qtbot, request):
    result = GraphEditorWindow()
    result._discard_approved = True
    qtbot.addWidget(result)
    result.bind_document(request.getfixturevalue("source"))
    return result


def select_tree(window, selection):
    iterator = QTreeWidgetItemIterator(window.tree)
    while (item := iterator.value()) is not None:
        if item.data(0, Qt.ItemDataRole.UserRole) == selection:
            window.tree.setCurrentItem(item)
            return
        iterator += 1
    raise AssertionError("Synthetic selection was not found")


def flush_deletions():
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QCoreApplication.processEvents()


@pytest.mark.parametrize("kind", ["wave", "table", "procedure"])
def test_graph_non_graph_graph_releases_all_properties(window, kind):
    assert window.editor is not None
    document = replace(window.editor.document.source, tables=("Table0",), table_definitions=(PxpTable("Table0", ("root:x", "root:y")),), procedures=("// Synthetic source, never executed",))
    window.bind_document(document)
    non_graph = (kind, document.waves[0].id if kind == "wave" else "Table0" if kind == "table" else "0")
    for _ in range(3):
        select_tree(window, ("graph", "XY"))
        records = list(window.property_widgets.values())
        controls = [control for record in records for control, _parser in record["widgets"].values()]
        assert controls and all(isValid(control) for control in controls)
        select_tree(window, non_graph)
        assert not window.property_widgets
        flush_deletions()
        assert all(not isValid(control) for control in controls)
        # A queued Apply callback can still own a Python closure after the
        # QWidget has been deleted: it must not read it or edit another graph.
        assert window.editor is not None
        history_count = window.editor.history.count()
        for record in records:
            assert not window._property_record_dirty(record)
            record["apply"]()
        assert window.editor.history.count() == history_count
        select_tree(window, ("graph", "Map"))
        flush_deletions()
        assert window.property_widgets and window.graph().name == "Map"


def test_pending_edits_checked_before_disposing_controls(window, monkeypatch):
    select_tree(window, ("graph", "XY"))
    record = window.property_widgets["geometry"]
    control = record["widgets"]["width_points"][0]
    control.setText("321")
    original = window.graph().style.width_points
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.No)
    window._selection = ("wave", "x")
    window._show_selection()
    assert window._selection == ("graph", "XY")
    assert isValid(control) and control.text() == "321"
    assert window._property_record_dirty(record)
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    window._selection = ("wave", "x")
    window._show_selection()
    flush_deletions()
    assert not window.property_widgets and not isValid(control)
    select_tree(window, ("graph", "Map"))
    select_tree(window, ("graph", "XY"))
    assert window.graph().style.width_points == original


@pytest.mark.parametrize("target", ["tab", "field", "graph"])
def test_leaving_property_field_commits_once(window, qtbot, monkeypatch, target):
    window.show()
    window.activateWindow()
    select_tree(window, ("graph", "XY"))
    record = window.property_widgets["geometry"]
    window.properties.setCurrentWidget(record["page"])
    control = record["widgets"]["width_points"][0]
    control.setFocus()
    qtbot.waitUntil(control.hasFocus)
    control.selectAll()
    qtbot.keyClicks(control, "321")
    before = window.editor.history.count()
    assert window.editor.document.graph("XY").style.width_points != 321

    def unexpected_question(*_args):
        pytest.fail("A valid field should commit before selection changes.")

    monkeypatch.setattr(QMessageBox, "question", unexpected_question)
    if target == "tab":
        qtbot.keyClick(control, Qt.Key.Key_Tab)
        assert not control.hasFocus()
    elif target == "field":
        other = record["widgets"]["height_points"][0]
        qtbot.mouseClick(other, Qt.MouseButton.LeftButton)
        assert other.hasFocus()
    else:
        iterator = QTreeWidgetItemIterator(window.tree)
        while iterator.value().data(0, Qt.ItemDataRole.UserRole) != ("graph", "Map"):
            iterator += 1
        item = iterator.value()
        window.tree.scrollToItem(item)
        qtbot.mouseClick(window.tree.viewport(), Qt.MouseButton.LeftButton,
                         pos=window.tree.visualItemRect(item).center())
        assert window.graph().name == "Map"

    assert window.editor.document.graph("XY").style.width_points == 321
    assert window.editor.history.count() == before + 1


def test_invalid_property_on_focus_loss_does_not_change_graph(window, qtbot, monkeypatch):
    window.show()
    window.activateWindow()
    record = window.property_widgets["geometry"]
    window.properties.setCurrentWidget(record["page"])
    control = record["widgets"]["width_points"][0]
    control.setFocus()
    qtbot.waitUntil(control.hasFocus)
    control.selectAll()
    qtbot.keyClicks(control, "invalid")
    original = window.graph()
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, _title, message: warnings.append(message))
    qtbot.mouseClick(record["widgets"]["height_points"][0], Qt.MouseButton.LeftButton)
    assert len(warnings) == 1
    assert window.graph() == original
    assert control.text() == "invalid"
