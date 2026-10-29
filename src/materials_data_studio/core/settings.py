"""Persistent Qt settings access."""

from __future__ import annotations

import os
from pathlib import Path

from materials_data_studio.config import APP_NAME, ORGANIZATION_NAME


def create_settings():  # type: ignore[no-untyped-def]
    """Create the application QSettings object without importing Qt at startup elsewhere."""
    from PySide6.QtCore import QSettings
    directory = os.environ.get("MDS_SETTINGS_DIRECTORY", "").strip()
    if directory:
        return QSettings(str(Path(directory) / "preferences.ini"), QSettings.Format.IniFormat)
    return QSettings(ORGANIZATION_NAME, APP_NAME)
