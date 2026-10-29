"""Small shared dialogs."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QMessageBox, QWidget


def show_error(parent: QWidget | None, title: str, message: str) -> None:
    """Show an actionable error without exposing a traceback."""
    dialog = QMessageBox(QMessageBox.Icon.Critical, title, message, QMessageBox.StandardButton.Ok, parent)
    copy = dialog.addButton("Copy details", QMessageBox.ButtonRole.ActionRole)
    copy.clicked.connect(lambda: QApplication.clipboard().setText(f"{title}\n{message}"))
    dialog.setDetailedText(message)
    dialog.exec()


def show_warning(parent: QWidget | None, title: str, message: str) -> None:
    """Show a nonfatal warning."""
    QMessageBox.warning(parent, title, message)

