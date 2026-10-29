"""Privacy-safe presentation of real Qt dialogs for documentation captures only."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QFileDialog, QLineEdit, QListView


def anonymize_file_dialog(dialog: QFileDialog) -> None:
    """Hide host location/account UI without changing selected demonstration data.

    This is a capture-only UI adjustment, not raster editing or an application
    preference. Call after choosing the generated-data folder and before grab().
    Fail closed if Qt changes the expected nonnative dialog controls.
    """
    location = dialog.findChild(QComboBox, "lookInCombo")
    sidebar = dialog.findChild(QListView, "sidebar")
    if location is None or sidebar is None:
        raise RuntimeError("Cannot anonymize native/unknown file dialog for a manual capture")
    directory = dialog.directory().absolutePath()
    selected = dialog.selectedFiles()
    dialog.setSidebarUrls([])
    sidebar.hide()
    # QFileDialog's private LookInCombo paints its filesystem-model path even
    # when currentText()/an editable line report a different string. Remove the
    # actual path-bearing widget from the visible layout, not just its text.
    caption = QLineEdit("Demo data", dialog)
    caption.setObjectName("manualCaptureLocation")
    caption.setReadOnly(True)
    caption.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    caption.setSizePolicy(location.sizePolicy())
    layout = dialog.layout()
    if layout is None or layout.replaceWidget(location, caption) is None:
        caption.deleteLater()
        raise RuntimeError("Cannot replace the file dialog's private location display")
    location.hide()
    caption.show()
    if dialog.directory().absolutePath() != directory or dialog.selectedFiles() != selected:
        raise RuntimeError("Documentation anonymization changed the selected input")
