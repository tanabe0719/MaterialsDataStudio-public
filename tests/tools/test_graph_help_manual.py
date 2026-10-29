"""Independent help menus and split manuals; generated content only."""
from __future__ import annotations

import sys

import pytest
from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import QMessageBox, QPushButton

from materials_data_studio.tools.graph_waves import empty_document
from materials_data_studio.tools.graph_window import GraphEditorWindow
from materials_data_studio.ui.manual import resolve_graph_manual_path, resolve_user_manual_path


@pytest.fixture
def viewer(qtbot):
    window = GraphEditorWindow()
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    window._discard_approved = True
    qtbot.addWidget(window)
    window.bind_graph_document(empty_document())
    window.show()
    yield window
    window.close()


def test_help_menu_consolidates_viewer_manuals_and_python_help(viewer):
    menus = [a.text() for a in viewer.menuBar().actions()]
    assert menus[-2:] == ["View", "Help"]
    assert viewer.graph_manual_action.shortcut().toString() == "F1"
    actions = viewer.help_menu.actions()
    entries = [a for a in actions if not a.isSeparator()]
    assert [a.text() for a in entries[:2]] == ["Check for updates…", "Check for updates at startup"]
    assert entries[1].isCheckable()
    assert entries[2:] == [
        viewer.graph_manual_action, viewer.japanese_graph_manual_action,
        viewer.english_license_action, viewer.japanese_license_action, viewer.python_help_action]
    view = viewer.command_workspace.view_menu
    assert not any("Help" in a.text() for a in view.actions())
    assert not any(b.text() == "Python / mds Help…" for b in viewer.findChildren(QPushButton))
    assert viewer.graph_manual_dialogs == {}
    assert viewer.command_workspace.procedure_help is None


@pytest.mark.parametrize("language", ["en", "ja"])
def test_modeless_manual_reuses_searchable_linked_dialog_without_editing(viewer, qtbot, language):
    before = viewer.editor.document.current
    source = viewer.command_workspace.code.toPlainText()
    output = viewer.command_workspace.output.toPlainText()
    action = viewer.graph_manual_action if language == "en" else viewer.japanese_graph_manual_action
    action.trigger()
    dialog = viewer.graph_manual_dialogs[language]
    qtbot.wait(30)
    assert dialog.isVisible() and not dialog.isModal()
    assert "Load General Text" in dialog.browser.toPlainText()
    assert set(dialog._section_positions) == {f"section-{n}" for n in range(1, 15)}
    dialog.search.setText("Load General Text")
    qtbot.keyClick(dialog.search, Qt.Key.Key_Return)
    assert dialog.isVisible() and dialog.browser.textCursor().selectedText() == "Load General Text"
    dialog._open_manual_link(QUrl("manual:section-13"))
    qtbot.wait(30)
    assert dialog.browser.textCursor().block().text().startswith("13.")
    assert dialog.browser.textCursor().block().blockFormat().headingLevel() == 2
    dialog._open_manual_link(QUrl("manual:section-12"))
    assert dialog.browser.textCursor().block().text().startswith("12.")
    dialog._open_manual_link(QUrl("manual:section-14"))
    assert dialog.browser.textCursor().block().text().startswith("14.")
    assert "Resample" in dialog.browser.toPlainText()
    dialog.close()
    action.trigger()
    assert viewer.graph_manual_dialogs[language] is dialog and dialog.isVisible()
    assert viewer.editor.document.current is before
    assert viewer.command_workspace.code.toPlainText() == source
    assert viewer.command_workspace.output.toPlainText() == output


def test_python_help_menu_keeps_existing_search_and_tab_state(viewer):
    viewer.python_help_action.trigger()
    dialog = viewer.command_workspace.procedure_help
    assert dialog.isVisible() and dialog.tabs.count() == 6
    dialog.tabs.setCurrentIndex(3)
    dialog.close()
    viewer.python_help_action.trigger()
    assert viewer.command_workspace.procedure_help is dialog
    assert dialog.tabs.currentIndex() == 3 and dialog.isVisible()


@pytest.mark.parametrize("language", ["en", "ja"])
def test_main_manual_is_overview_only_and_viewer_has_details(language):
    main = resolve_user_manual_path(language).read_text(encoding="utf-8")
    graph = resolve_graph_manual_path(language).read_text(encoding="utf-8")
    assert "Tools > Graph Editor / Igor Import" in main
    assert "Help > Graph Viewer Manual" in main
    assert "tickEnab" not in main and "scope()" not in main
    assert "Send to Igor Pro" in graph and "LoadWave/G" in graph
    assert "Resample" in graph and "Violin" in graph and "Statistics > T-Test" in graph
    assert all(internal not in graph for internal in ("schema", "AppContainer", "golden", "v0.10.8"))
    assert "](#section-14)" in graph
    assert "(screenshots/77_graph_help_menu.png)" in graph


def test_graph_manual_resolver_prefers_frozen_bundle(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    for language, name in [("en", "graph_viewer_manual_en.md"), ("ja", "graph_viewer_manual.md")]:
        (docs / name).write_text("# Generated bundle guide", encoding="utf-8")
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        assert resolve_graph_manual_path(language) == docs / name
    with pytest.raises(ValueError, match="language"):
        resolve_graph_manual_path("unknown")


def test_missing_manual_reports_failure_without_changing_editor(viewer, monkeypatch):
    def missing(_language):
        raise FileNotFoundError("Generated missing manual")
    monkeypatch.setattr("materials_data_studio.ui.manual.resolve_graph_manual_path", missing)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: warnings.append(_args[2]))
    viewer.show_graph_manual()
    assert warnings == ["Generated missing manual"] and viewer.graph_manual_dialogs == {}
