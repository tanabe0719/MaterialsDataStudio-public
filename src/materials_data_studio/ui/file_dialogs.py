"""Application-wide starting folder for local file and directory pickers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QFileDialog as QtFileDialog
from PySide6.QtWidgets import QWidget

from materials_data_studio.core.settings import create_settings

_LAST_FOLDER = "fileDialogs/lastDirectory"


def initial_directory(fallback: str = "", *, save: bool = False) -> str:
    stored = str(create_settings().value(_LAST_FOLDER, "") or "")
    if not stored or not Path(stored).is_dir():
        return fallback
    if save and fallback and not Path(fallback).is_dir():
        return str(Path(stored) / Path(fallback).name)
    return stored


def remember_selection(path: str, *, directory: bool = False) -> None:
    if not path:
        return
    folder = Path(path) if directory else Path(path).parent
    if folder.is_absolute() and folder.is_dir():
        create_settings().setValue(_LAST_FOLDER, str(folder))


class QFileDialog(QtFileDialog):
    """Keep Qt's native pickers, filters and return values."""

    @staticmethod
    def getOpenFileName(parent: QWidget | None = None, caption: str = "", dir: str = "",
                        *args: Any, **kwargs: Any) -> tuple[str, str]:
        path, selected_filter = QtFileDialog.getOpenFileName(parent, caption, initial_directory(dir), *args, **kwargs)
        remember_selection(path)
        return path, selected_filter

    @staticmethod
    def getOpenFileNames(parent: QWidget | None = None, caption: str = "", dir: str = "",
                         *args: Any, **kwargs: Any) -> tuple[list[str], str]:
        paths, selected_filter = QtFileDialog.getOpenFileNames(parent, caption, initial_directory(dir), *args, **kwargs)
        if paths:
            remember_selection(paths[0])
        return paths, selected_filter

    @staticmethod
    def getSaveFileName(parent: QWidget | None = None, caption: str = "", dir: str = "",
                        *args: Any, **kwargs: Any) -> tuple[str, str]:
        path, selected_filter = QtFileDialog.getSaveFileName(parent, caption, initial_directory(dir, save=True), *args, **kwargs)
        remember_selection(path)
        return path, selected_filter

    @staticmethod
    def getExistingDirectory(parent: QWidget | None = None, caption: str = "", dir: str = "",
                             *args: Any, **kwargs: Any) -> str:
        path = QtFileDialog.getExistingDirectory(parent, caption, initial_directory(dir), *args, **kwargs)
        remember_selection(path, directory=True)
        return path
