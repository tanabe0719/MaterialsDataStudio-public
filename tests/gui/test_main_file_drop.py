"""Main-window drops use the same queued, nonexecuting Graph Editor routes."""
from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QMessageBox

from materials_data_studio.tools.graph_window import GraphEditorWindow
from materials_data_studio.ui.main_window import MainWindow


@pytest.fixture
def window(qtbot, monkeypatch):
    widget = MainWindow()
    qtbot.addWidget(widget, before_close_func=lambda target: target.undo_history.reset(clean=True))
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Discard)
    yield widget
    for graph in widget.tool_manager.open_windows:
        graph.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        graph._discard_approved = True
        qtbot.addWidget(graph)
    widget.undo_history.reset(clean=True)
    widget.close()


def events(urls):
    mime = QMimeData()
    mime.setUrls(urls)
    entered = QDragEnterEvent(QPointF(5, 5).toPoint(), Qt.DropAction.CopyAction,
                              mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    dropped = QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction,
                         mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    return mime, entered, dropped


@pytest.mark.parametrize("suffix,route", [(".pxp", "pxp"), (".mdsg", "graph"),
                                        (".h5xp", "graph"), (".IPF", "ipf")])
def test_main_drop_opens_independent_editor_after_native_drop(window, monkeypatch, tmp_path, qtbot, suffix, route):
    calls = []
    monkeypatch.setattr(GraphEditorWindow, "open_path", lambda self, path: calls.append(("pxp", path)))
    monkeypatch.setattr(GraphEditorWindow, "open_graph_path", lambda self, path: calls.append(("graph", path)))
    monkeypatch.setattr("materials_data_studio.tools.command_window.CommandWorkspace.convert_ipf_paths",
                        lambda self, paths: calls.append(("ipf", paths[0])))
    monkeypatch.setattr(window.active_widget(), "import_files",
                        lambda paths: pytest.fail("Graph formats must not go to XRD"))
    path = tmp_path / ("generated" + suffix)
    mime, entered, dropped = events([QUrl.fromLocalFile(str(path))])
    window.dragEnterEvent(entered)
    window.dropEvent(dropped)
    assert entered.isAccepted() and dropped.isAccepted() and not calls
    assert not window.tool_manager.open_windows  # No expensive work in OLE callback.
    qtbot.waitUntil(lambda: len(calls) == 1)
    assert calls == [(route, path)] and len(window.tool_manager.open_windows) == 1
    assert not window.state.datasets
    assert mime.hasUrls()


def test_mixed_drop_keeps_experiments_independent_and_data_in_active_module(window, monkeypatch, tmp_path, qtbot):
    calls, imports = [], []
    monkeypatch.setattr(GraphEditorWindow, "open_path", lambda self, path: calls.append((self, path)))
    monkeypatch.setattr(GraphEditorWindow, "open_graph_path", lambda self, path: calls.append((self, path)))
    monkeypatch.setattr(window.active_widget(), "import_files", lambda paths: imports.append(paths))
    paths = [tmp_path / name for name in ("one.pxp", "two.mdsg", "data.xy")]
    mime, _, dropped = events([QUrl.fromLocalFile(str(path)) for path in paths])
    window.dropEvent(dropped)
    assert not calls and not imports
    qtbot.waitUntil(lambda: len(calls) == 2)
    assert [item[1] for item in calls] == paths[:2] and calls[0][0] is not calls[1][0]
    assert imports == [[paths[2]]] and mime.hasUrls()


@pytest.mark.parametrize("urls", [["https://example.com/a.ipf"],
                                 ["file:///C:/generated.pxp", "https://example.com/a.ipf"], []])
def test_main_drop_rejects_remote_or_empty_urls(window, urls):
    mime, entered, dropped = events([QUrl(url) for url in urls])
    window.dragEnterEvent(entered)
    window.dropEvent(dropped)
    assert not entered.isAccepted() and not dropped.isAccepted()
    assert not window.file_drop_filter.pending and not window.tool_manager.open_windows
    assert mime.urls() == [QUrl(url) for url in urls]


def test_close_cancels_pending_drop(window, tmp_path, qtbot):
    mime, _, dropped = events([QUrl.fromLocalFile(str(tmp_path / "generated.ipf"))])
    window.dropEvent(dropped)
    window.close()
    qtbot.wait(10)
    assert window.file_drop_filter.closed and not window.file_drop_filter.pending
    assert not window.tool_manager.open_windows and mime.hasUrls()


def test_main_session_drop_retains_session_route(window, monkeypatch, tmp_path, qtbot):
    calls: list[Path] = []
    monkeypatch.setattr(window, "open_session", lambda path: calls.append(path))
    path = tmp_path / "generated.mdss"
    mime, _, dropped = events([QUrl.fromLocalFile(str(path))])
    window.dropEvent(dropped)
    assert not calls
    qtbot.waitUntil(lambda: bool(calls))
    assert calls == [path] and not window.tool_manager.open_windows and mime.hasUrls()


@pytest.mark.parametrize("tab", [0, 1, 2])
def test_child_drop_is_routed_even_after_lazy_module_creation(window, monkeypatch, tmp_path, qtbot, tab):
    calls = []
    monkeypatch.setattr(GraphEditorWindow, "open_path", lambda self, path: calls.append(path))
    window.module_tabs.setCurrentIndex(tab)
    child = window.active_widget()
    path = tmp_path / "generated.pxp"
    mime, entered, dropped = events([QUrl.fromLocalFile(str(path))])
    assert window.file_drop_filter.eventFilter(child, entered)
    assert window.file_drop_filter.eventFilter(child, dropped)
    assert entered.isAccepted() and dropped.isAccepted() and not calls
    qtbot.waitUntil(lambda: bool(calls))
    assert calls == [path] and child.acceptDrops() and mime.hasUrls()
