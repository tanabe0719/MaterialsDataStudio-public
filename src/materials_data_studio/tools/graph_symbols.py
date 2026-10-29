"""Visual selectors drawn from the same marker paths/dashes as graph rendering."""

from __future__ import annotations

from functools import lru_cache

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QImage, QPixmap
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
    figure = Figure(figsize=(0.96, 0.28), dpi=100)
    figure.patch.set_alpha(0)
    ax = figure.add_axes((0, 0, 1, 1))
    ax.set_axis_off()
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    color = "#e2e8f0" if dark else "#334155"
    if kind == "marker":
        filled = number in {14, 15, 16, 17, 18, 19, 23, 26, 29, 46, 49, 52, 55, 58, 60, 62} or number in range(32, 39, 2)
        ax.plot([0.5], [0.5], marker=marker_path(number), markersize=11,
                markerfacecolor=color if filled else "none", markeredgecolor=color, markeredgewidth=1.2)
    else:
        line, = ax.plot([0.07, 0.93], [0.5, 0.5], color=color, linewidth=1.6)
        if number:
            line.set_linestyle((0, pattern or _DASH_PATTERNS.get(number, (4, 4))))
    canvas = FigureCanvasAgg(figure)
    canvas.draw()
    pixels = np.asarray(canvas.buffer_rgba())
    image = QImage(pixels.data, pixels.shape[1], pixels.shape[0], QImage.Format.Format_RGBA8888).copy()
    return QIcon(QPixmap.fromImage(image))


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
