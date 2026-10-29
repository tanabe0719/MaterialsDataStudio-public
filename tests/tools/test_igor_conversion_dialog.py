"""Reviewed conversion UI never executes imported source; generated examples only."""
from __future__ import annotations

from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox, QWidget

from materials_data_studio.tools.graph_waves import empty_document
from materials_data_studio.tools.graph_window import GraphEditorWindow
from materials_data_studio.tools.igor_conversion_dialog import (
    MAX_IPF_BYTES,
    IgorConversionDialog,
    read_ipf,
)

SYNTHETIC = '''Function DemoScale(factor)
    Variable factor
    Make/D/O/N=8 demo_signal
    demo_signal = p*factor
    Display/N=Demo demo_signal
    ModifyGraph tick=2,mirror=1
End
'''


@pytest.fixture
def dialog(qtbot):
    parent = QWidget()
    qtbot.addWidget(parent)
    widget = IgorConversionDialog(parent, [("Synthetic imported source", SYNTHETIC)])
    qtbot.addWidget(widget)
    yield widget
    widget.close()


def test_read_and_convert_require_explicit_analyze_and_arguments(dialog, monkeypatch):
    from materials_data_studio.tools.command_engine import CommandEngine
    def forbidden_execution(*_args, **_kwargs):
        raise AssertionError("Reading/analyzing/converting must not execute commands")
    monkeypatch.setattr(CommandEngine, "execute", forbidden_execution)
    assert dialog.report is None and not dialog.insert_button.isEnabled()
    assert dialog.source.toPlainText() == SYNTHETIC
    dialog.analyze()
    assert dialog.report is not None
    assert not dialog.insert_button.isEnabled()  # Required factor is not guessed.
    dialog.arguments.setText("[2.5]")
    assert dialog.refresh_preview()
    assert "from mds import" in dialog.preview.toPlainText()
    assert dialog.source.toPlainText() == SYNTHETIC
    assert dialog.result() != QDialog.DialogCode.Accepted
    dialog.source.appendPlainText("// edit the copy")
    assert dialog.report is None and not dialog.insert_button.isEnabled()
    assert not dialog.preview.toPlainText()


@pytest.mark.parametrize("arguments", ["NaN", "[NaN]", "[Infinity]", '{"factor":2}', "[]", "[{}, 3]"])
def test_invalid_arguments_cannot_copy_or_insert(dialog, arguments):
    dialog.analyze()
    dialog.arguments.setText(arguments)
    assert not dialog.refresh_preview()
    assert not dialog.copy_button.isEnabled() and not dialog.insert_button.isEnabled()


def test_unsupported_source_is_reported_with_lines_and_not_inserted(dialog):
    dialog.source.setPlainText('Function Unsafe()\nExecute "arbitrary"\nEnd\n')
    dialog.analyze()
    assert "line 2" in dialog.diagnostics.toPlainText()
    assert not dialog.insert_button.isEnabled()
    assert not dialog.generated


def test_copy_refreshes_pending_arguments_and_replace_needs_confirmation(dialog, monkeypatch, qapp):
    dialog.analyze()
    dialog.arguments.setText("[2]")
    dialog.copy_python()
    initial = qapp.clipboard().text()
    assert initial and initial == dialog.generated
    dialog.arguments.setText("[3]")
    dialog.copy_python()  # Timer has not fired yet; stale arguments must not win.
    assert qapp.clipboard().text() == dialog.generated != initial
    dialog.replacing_python = True
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.No)
    dialog.insert_python()
    assert dialog.result() != QDialog.DialogCode.Accepted
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes)
    dialog.insert_python()
    assert dialog.result() == QDialog.DialogCode.Accepted


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "cp932"])
def test_ipf_import_preserves_encodings_and_never_includes_files(dialog, tmp_path, encoding):
    path = tmp_path / "synthetic.ipf"
    source = '// 日本語の合成サンプル\n' + SYNTHETIC
    path.write_bytes(source.encode(encoding))
    assert read_ipf(path) == source
    dialog.open_ipf(path)
    assert dialog.source.toPlainText() == source
    assert dialog.report is None and not dialog.generated


def test_canceled_open_and_size_limit_preserve_source(dialog, monkeypatch, tmp_path):
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: ("", ""))
    dialog.open_ipf()
    assert dialog.source.toPlainText() == SYNTHETIC
    path = tmp_path / "oversized.ipf"
    path.write_bytes(b"x" * (MAX_IPF_BYTES + 1))
    dialog.open_ipf(path)
    assert "4 MiB" in dialog.status.text()
    assert dialog.source.toPlainText() == SYNTHETIC


def test_workspace_conversion_preserves_original_and_undo(qtbot, monkeypatch):
    window = GraphEditorWindow()
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    window._discard_approved = True
    qtbot.addWidget(window)
    document = empty_document()
    document.source = replace(document.source, procedures=(SYNTHETIC,))
    window.bind_graph_document(document)
    workspace = window.command_workspace
    workspace.code.setPlainText('print("existing Python")')
    workspace.flush()
    workspace.tabs.setCurrentIndex(1)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes)
    def execute_preview(widget):
        assert widget.sources.count() == 1
        widget.analyze()
        widget.arguments.setText("[2]")
        widget.insert_python()
        return widget.result()
    monkeypatch.setattr(IgorConversionDialog, "exec", execute_preview)
    workspace.convert_igor()
    assert workspace.tabs.currentIndex() == 0
    assert workspace.tabs.widget(1).isReadOnly()
    assert workspace.tabs.widget(1).toPlainText() == SYNTHETIC
    assert workspace.process is None
    assert "from mds import" in workspace.code.toPlainText()
    assert "not executed" in workspace.output.toPlainText()
    assert window.editor is not None
    window.editor.history.undo()
    assert workspace.code.toPlainText() == 'print("existing Python")'
    window.close()
