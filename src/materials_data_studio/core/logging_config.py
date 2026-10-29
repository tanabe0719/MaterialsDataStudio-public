"""Rotating diagnostic logging in the user data directory."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from materials_data_studio.config import APP_NAME


def user_data_directory() -> Path:
    """Return a writable per-user application directory."""
    try:
        from PySide6.QtCore import QStandardPaths

        location = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppLocalDataLocation
        )
        if location:
            return Path(location)
    except ImportError:
        pass
    return Path.home() / f".{APP_NAME.lower().replace(' ', '_')}"


def configure_logging(level: int = logging.INFO) -> Path:
    """Configure a rotating UTF-8 application log and return its path."""
    log_dir = user_data_directory() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "materials-data-studio.log"
    root = logging.getLogger("materials_data_studio")
    root.setLevel(level)
    if not any(isinstance(handler, RotatingFileHandler) for handler in root.handlers):
        handler = RotatingFileHandler(
            log_path,
            maxBytes=2 * 1024 * 1024,
            backupCount=4,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root.addHandler(handler)
    return log_path

