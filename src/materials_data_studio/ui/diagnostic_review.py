"""Explicit review of the exact, optionally path-redacted support log payload."""
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
)

from materials_data_studio.core.recovery import diagnostic_logs


def review_diagnostics(parent):
    dialog = QDialog(parent)
    dialog.setWindowTitle("Review diagnostic bundle before saving")
    dialog.resize(950, 650)
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel("No sessions or measurement arrays are included. Review logs for sample identifiers before sharing."))
    redact = QCheckBox("Redact local Windows paths")
    redact.setChecked(True)
    layout.addWidget(redact)
    preview = QPlainTextEdit()
    preview.setReadOnly(True)
    layout.addWidget(preview, 1)
    payload = {}
    def refresh():
        payload.clear()
        payload.update(diagnostic_logs(redact_paths=redact.isChecked()))
        preview.setPlainText("\n\n".join(name + "\n" + data.decode("utf-8", errors="replace") for name, data in payload.items()) or "No application logs found. Only version/platform metadata will be saved.")
    redact.toggled.connect(refresh)
    refresh()
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    result = dict(payload) if dialog.exec() == QDialog.DialogCode.Accepted else None
    dialog.deleteLater()
    return result
