"""Theme-aware application icon resources."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon

TOOLBAR_ICON_NAMES = (
    "new",
    "open",
    "save",
    "import",
    "export_data",
    "export_figure",
    "undo",
    "redo",
    "send_to_igor",
    "edit_graph",
)


def resource_root() -> Path:
    """Return the source checkout or PyInstaller resource root."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[3]))


def toolbar_icon_path(name: str, theme: str) -> Path:
    """Resolve one generated toolbar icon and reject unknown names."""
    if name not in TOOLBAR_ICON_NAMES:
        raise ValueError(f"Unknown toolbar icon: {name}")
    normalized_theme = theme if theme in {"light", "dark"} else "light"
    directory = (
        resource_root()
        / "resources"
        / "icons"
        / "toolbar"
        / normalized_theme
    )
    return directory / f"{name}.png"


def toolbar_icon(name: str, theme: str) -> QIcon:
    """Load one generated toolbar icon from the active theme set."""
    path = toolbar_icon_path(name, theme)
    if not path.is_file():
        raise FileNotFoundError(f"Toolbar icon resource was not found: {path}")
    return QIcon(str(path))
