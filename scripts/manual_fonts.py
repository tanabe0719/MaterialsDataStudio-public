"""Make offscreen Windows manual captures use real, readable system fonts."""
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase


def configure_capture_fonts(app):
    if not QFontDatabase.families():
        for name in ("segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf", "meiryo.ttc", "consola.ttf"):
            path = Path("C:/Windows/Fonts") / name
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))
    app.setFont(QFont("Segoe UI", 9))
