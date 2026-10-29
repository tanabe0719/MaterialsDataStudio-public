"""Real file-dialog captures omit host paths without changing chosen input."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QComboBox, QFileDialog, QLineEdit, QListView
from scripts.manual_privacy import anonymize_file_dialog


def test_manual_file_chooser_omits_location_and_account(qtbot, tmp_path):
    selected = tmp_path / "generated.dat"
    selected.write_text("1 2\n3 4\n", encoding="utf-8")
    dialog = QFileDialog()
    dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
    dialog.setDirectory(str(tmp_path))
    dialog.selectFile(selected.name)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.wait(20)
    directory, files = dialog.directory().absolutePath(), dialog.selectedFiles()
    anonymize_file_dialog(dialog)
    qtbot.wait(20)
    location = dialog.findChild(QComboBox, "lookInCombo")
    sidebar = dialog.findChild(QListView, "sidebar")
    caption = dialog.findChild(QLineEdit, "manualCaptureLocation")
    assert location is not None and not location.isVisible()
    assert caption is not None and caption.isVisible() and caption.text() == "Demo data"
    assert caption.isReadOnly() and not caption.toolTip()
    assert sidebar is not None and not sidebar.isVisible()
    assert not dialog.sidebarUrls()
    assert dialog.directory().absolutePath() == directory
    assert dialog.selectedFiles() == files == [str(selected).replace("\\", "/")]
    # Subsequent event-loop turns must not restore the filesystem path.
    qtbot.wait(30)
    assert not location.isVisible() and caption.text() == "Demo data"
    dialog.close()


def test_unknown_capture_controls_are_rejected(qtbot):
    dialog = QFileDialog()
    dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
    qtbot.addWidget(dialog)
    location = dialog.findChild(QComboBox, "lookInCombo")
    assert location is not None
    location.setObjectName("unexpectedControl")
    with pytest.raises(RuntimeError, match="Cannot anonymize"):
        anonymize_file_dialog(dialog)
