"""Synthetic source-only procedure drops, separate from numeric-column imports."""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from shiboken6 import isValid

from materials_data_studio.tools.graph_waves import empty_document
from materials_data_studio.tools.graph_window import GraphEditorWindow
from materials_data_studio.tools.igor_conversion_dialog import IgorConversionDialog

SOURCE = "Function SyntheticStyle()\nModifyGraph tick=2,mirror=1\nEnd\n"


@pytest.fixture
def window(qtbot):
    widget = GraphEditorWindow()
    widget.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    widget._discard_approved = True
    widget.bind_graph_document(empty_document())
    qtbot.addWidget(widget)
    yield widget
    if isValid(widget):
        widget.close()


def drag(mime: QMimeData) -> QDragEnterEvent:
    return QDragEnterEvent(QPointF(5, 5).toPoint(), Qt.DropAction.CopyAction,
                           mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)


def drop(mime: QMimeData) -> QDropEvent:
    return QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction,
                      mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)


@pytest.mark.parametrize("via_child", [False, True])
def test_ipf_drop_opens_one_source_only_dialog(window, monkeypatch, tmp_path, via_child, qtbot):
    sources = [("first.ipf", SOURCE, "utf-8"),
               ("second.IPF", "// 合成サンプル\n" + SOURCE, "cp932")]
    mime = QMimeData()
    paths = []
    for name, source, encoding in sources:
        path = tmp_path / name
        path.write_bytes(source.encode(encoding))
        paths.append(path)
    mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    workspace = window.command_workspace
    workspace.code.setPlainText('print("existing source")')
    workspace.flush()
    original_history = window.editor.history.count()
    original_document = window.editor.document.current

    def forbidden(*_args, **_kwargs):
        pytest.fail("A source drop must not parse columns, analyze, or execute")

    monkeypatch.setattr("materials_data_studio.tools.graph_window.import_text", forbidden)
    monkeypatch.setattr(IgorConversionDialog, "analyze", forbidden)
    monkeypatch.setattr(workspace, "start", forbidden)
    calls = []

    def preview(dialog):
        dialog.show()
        QApplication.processEvents()
        assert dialog.report is None and dialog.generated == ""
        assert not dialog.insert_button.isEnabled()
        assert dialog.sources.count() == len(sources)
        assert [(dialog.sources.itemText(i), dialog.sources.itemData(i))
                for i in range(dialog.sources.count())] == [(name, source) for name, source, _ in sources]
        dialog.sources.setCurrentIndex(0)
        assert dialog.source.toPlainText() == SOURCE
        calls.append(dialog)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(IgorConversionDialog, "exec", preview)
    entered, dropped = drag(mime), drop(mime)
    if via_child:
        assert window.file_drop_filter.eventFilter(window.canvas, entered)
        assert window.file_drop_filter.eventFilter(window.canvas, dropped)
    else:
        window.dragEnterEvent(entered)
        window.dropEvent(dropped)
    assert entered.isAccepted() and dropped.isAccepted() and not calls
    qtbot.waitUntil(lambda: len(calls) == 1)
    assert workspace.code.toPlainText() == 'print("existing source")'
    assert workspace.process is None
    assert window.editor.document.current == original_document
    assert window.editor.history.count() == original_history


def test_mixed_drop_preserves_other_import_routes(window, monkeypatch, tmp_path, qtbot):
    calls = []
    monkeypatch.setattr(window, "open_path", lambda path: calls.append(("pxp", path)))
    monkeypatch.setattr(window, "open_graph_path", lambda path: calls.append(("graph", path)))
    monkeypatch.setattr("materials_data_studio.tools.graph_window.import_text",
                        lambda _window, path: calls.append(("text", path)))
    monkeypatch.setattr(window.command_workspace, "convert_ipf_paths",
                        lambda paths: calls.append(("ipf", paths)))
    paths = [tmp_path / ("synthetic" + suffix)
             for suffix in (".ipf", ".pxp", ".mdsg", ".h5xp", ".csv", ".IPF")]
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    event = drop(mime)  # A direct drop is also revalidated.
    window.dropEvent(event)
    assert event.isAccepted() and not calls
    qtbot.waitUntil(lambda: len(calls) == 5)
    assert calls == [("pxp", paths[1]), ("graph", paths[2]), ("graph", paths[3]),
                     ("text", paths[4]), ("ipf", [paths[0], paths[5]])]


@pytest.mark.parametrize("urls", [["https://example.com/macro.ipf"], ["file:///C:/macro.exe"],
                                 ["file:///C:/macro.ipf", "https://example.com/other.ipf"]])
def test_drop_rejects_remote_or_unrecognized_files(window, monkeypatch, urls):
    def forbidden(*_args):
        pytest.fail("Rejected drops must not open any source")
    monkeypatch.setattr(window.command_workspace, "convert_ipf_paths", forbidden)
    mime = QMimeData()
    mime.setUrls([QUrl(url) for url in urls])
    entered, dropped = drag(mime), drop(mime)
    window.dragEnterEvent(entered)
    window.dropEvent(dropped)
    assert not entered.isAccepted() and not dropped.isAccepted()


def test_ipf_read_failure_still_shows_nonexecuting_preview(window, monkeypatch, tmp_path):
    calls = []
    def preview(dialog):
        dialog.show()
        QApplication.processEvents()
        assert "Cannot read Igor source" in dialog.status.text()
        assert dialog.report is None and not dialog.generated
        assert not dialog.insert_button.isEnabled()
        calls.append(dialog)
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(IgorConversionDialog, "exec", preview)
    window.command_workspace.convert_ipf_paths([tmp_path / "missing.ipf"])
    assert len(calls) == 1 and window.command_workspace.process is None


def test_running_procedure_does_not_read_dropped_source(window, monkeypatch, tmp_path):
    monkeypatch.setattr(window.command_workspace, "is_running", lambda: True)
    def forbidden(*_args):
        pytest.fail("Do not open the conversion dialog during a running procedure")
    monkeypatch.setattr(IgorConversionDialog, "exec", forbidden)
    monkeypatch.setattr(IgorConversionDialog, "open_ipf", forbidden)
    window.command_workspace.convert_ipf_paths([Path(tmp_path) / "synthetic.ipf"])
    assert "Stop the current Procedure" in window.command_workspace.output.toPlainText()
    monkeypatch.setattr(window.command_workspace, "is_running", lambda: False)


def test_conversion_handles_owner_destroyed_while_reading(monkeypatch, tmp_path, qapp):
    from shiboken6 import delete, isValid
    window = GraphEditorWindow()
    def close_owner(_dialog, _path):
        delete(window)
    def preview(dialog):
        dialog.show()
        QApplication.processEvents()
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(IgorConversionDialog, "open_ipf", close_owner)
    monkeypatch.setattr(IgorConversionDialog, "exec", preview)
    window.command_workspace.convert_ipf_paths([tmp_path / "synthetic.ipf"])
    assert not isValid(window)


def test_drop_queue_serializes_reentrant_drops(window, monkeypatch, tmp_path, qtbot):
    first, second = tmp_path / "first.dat", tmp_path / "second.ipf"
    calls = []
    def import_batch(paths):
        calls.append(("start", paths))
        if paths == [first]:
            window.file_drop_filter.enqueue([second])
            # Progress dialogs and conversion previews enter nested event loops.
            QApplication.processEvents()
            assert calls == [("start", [first])]
        calls.append(("end", paths))
    monkeypatch.setattr(window, "_import_dropped_paths", import_batch)
    window.file_drop_filter.enqueue([first])
    assert not calls
    qtbot.waitUntil(lambda: len(calls) == 4)
    assert calls == [("start", [first]), ("end", [first]),
                     ("start", [second]), ("end", [second])]
    assert not window.file_drop_filter.busy and not window.file_drop_filter.pending


def test_close_cancels_pending_imports(window, monkeypatch, tmp_path, qtbot):
    def forbidden(*_args):
        pytest.fail("A closed Graph Editor must not process queued imports")
    monkeypatch.setattr(window, "_import_dropped_paths", forbidden)
    window.file_drop_filter.enqueue([tmp_path / "synthetic.ipf"])
    window.close()
    qtbot.wait(20)
    assert window.file_drop_filter.closed and not window.file_drop_filter.pending
    assert not window.file_drop_filter.timer.isActive()


def test_close_while_processing_cancels_remaining_batch(window, monkeypatch, tmp_path, qtbot):
    calls = []
    def import_and_close(_window, path):
        calls.append(path)
        window.close()
    monkeypatch.setattr("materials_data_studio.tools.graph_window.import_text", import_and_close)
    first, second = tmp_path / "first.csv", tmp_path / "second.csv"
    window.file_drop_filter.enqueue([first, second])
    qtbot.waitUntil(lambda: bool(calls))
    assert calls == [first]
    assert window.file_drop_filter.closed


def test_cancel_close_preserves_visible_window_and_pending_imports(window, monkeypatch, tmp_path):
    window.show()
    QApplication.processEvents()
    window.command_workspace.code.setPlainText('print("unsaved synthetic source")')
    window.command_workspace.flush()
    window._discard_approved = False
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Cancel)
    path = tmp_path / "synthetic.ipf"
    window.file_drop_filter.enqueue([path])
    assert not window.close()
    assert window.isVisible() and not window.file_drop_filter.closed
    assert list(window.file_drop_filter.pending) == [[path]]
    window.file_drop_filter.cancel()
    window._discard_approved = True
