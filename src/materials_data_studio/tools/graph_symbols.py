"""Visual selectors drawn from the same marker paths/dashes as graph rendering."""

from __future__ import annotations

from functools import lru_cache

from matplotlib.markers import MarkerStyle
from matplotlib.path import Path
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QComboBox, QProxyStyle, QStyle

from materials_data_studio.tools.pxp_markers import marker_path
from materials_data_studio.tools.pxp_render import _DASH_PATTERNS


class _ListPopupStyle(QProxyStyle):
    """Use a bounded scrollable list rather than a screen-height native menu."""

    def styleHint(self, hint: QStyle.StyleHint, option=None, widget=None, returnData=None) -> int:
        if hint == QStyle.StyleHint.SH_ComboBox_Popup:
            return 0
        return super().styleHint(hint, option, widget, returnData)

@lru_cache(maxsize=512)
def style_icon(kind: str, number: int, dark: bool = False,
               pattern: tuple[float, ...] | None = None) -> QIcon:
    pixmap = QPixmap(96, 28)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    color = QColor("#e2e8f0" if dark else "#334155")
    painter.setPen(QPen(color, 1.2 if kind == "marker" else 1.6))
    if kind == "marker":
        shape = marker_path(number)
        if isinstance(shape, str):
            marker = MarkerStyle(shape)
            shape = marker.get_path().transformed(marker.get_transform())
        path = QPainterPath()
        for vertices, code in shape.iter_segments(curves=True, simplify=False):
            points = [(48 + vertices[i] * 15, 14 - vertices[i+1] * 15) for i in range(0, len(vertices), 2)]
            if code == Path.MOVETO:
                path.moveTo(*points[0])
            elif code == Path.LINETO:
                path.lineTo(*points[0])
            elif code == Path.CURVE3:
                path.quadTo(*points[0], *points[1])
            elif code == Path.CURVE4:
                path.cubicTo(*points[0], *points[1], *points[2])
            elif code == Path.CLOSEPOLY:
                path.closeSubpath()
        if number in {14, 15, 16, 17, 18, 19, 23, 26, 29, 46, 49, 52, 55, 58, 60, 62} or number in range(32, 39, 2):
            painter.setBrush(color)
        painter.drawPath(path)
    else:
        pen = painter.pen()
        if number:
            # Qt dash units are multiples of pen width, Matplotlib uses points.
            pen.setDashPattern([v / pen.widthF() for v in (pattern or _DASH_PATTERNS.get(number, (4, 4)))])
        painter.setPen(pen)
        painter.drawLine(7, 14, 89, 14)
    painter.end()
    return QIcon(pixmap)


def visual_choices(kind: str) -> list[tuple[str, int]]:
    if kind == "line":
        return [("Solid" if i == 0 else f"Pattern {i}", i) for i in range(18)]
    labels = {0: "+", 1: "×", 2: "✳", 5: "□", 6: "△", 7: "◇", 8: "○", 16: "■", 17: "▲", 18: "◆", 19: "●"}
    return [(labels.get(i, "Symbol") + f"  ({i})", i) for i in range(63)]


def visual_combo(kind: str, value: int | None, dark: bool = False,
                 patterns: tuple[tuple[int, tuple[float, ...]], ...] = ()) -> QComboBox:
    combo = QComboBox()
    popup_style = _ListPopupStyle("Fusion")
    popup_style.setParent(combo)
    combo.setStyle(popup_style)
    combo.setIconSize(QSize(96, 28))
    combo.setMaxVisibleItems(14)
    combo.view().setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    for text, number in visual_choices(kind):
        combo.addItem(style_icon(kind, number, dark, dict(patterns).get(number)), text, number)
    combo.setCurrentIndex(max(0, combo.findData(value or 0)))
    return combo
