"""Open the bundled library-rights and corresponding-source notice."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QWidget

from materials_data_studio.ui.manual import UserManualDialog


def notice_path(language: str) -> Path:
    filename = "building_and_licenses_ja.md" if language == "ja" else "building_and_licenses.md"
    root = Path(str(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[3])))
    for candidate in (root / "licenses" / filename, root / "docs" / filename):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("The bundled license notice is missing; reinstall the complete application.")


def show_licenses(parent: QWidget, language: str) -> None:
    dialog = UserManualDialog(notice_path(language), parent)
    dialog.setWindowTitle("ライセンス・対応ソース" if language == "ja" else "Licenses and Corresponding Source")
    dialog.exec()
    dialog.deleteLater()
