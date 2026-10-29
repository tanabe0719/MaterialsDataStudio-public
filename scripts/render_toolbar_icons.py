"""Render the two vector toolbar sources with a shared three-pixel stroke."""
import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parents[1]
for theme in ("light", "dark"):
    for name in ("send_to_igor", "edit_graph"):
        source = ROOT / f"resources/icons/toolbar/{theme}/{name}.svg"
        source.write_text(re.sub(r'stroke-width="[0-9.]+"', 'stroke-width="3"', source.read_text()), encoding="utf-8")
        image = QImage(256, 256, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        QSvgRenderer(str(source)).render(painter)
        painter.end()
        image.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation).save(str(source.with_suffix(".png")))
