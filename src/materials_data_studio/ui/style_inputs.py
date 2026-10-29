"""Editable system-font, palette-color and color-map controls."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
from PySide6.QtCore import QEvent, QSize, Signal
from PySide6.QtGui import QColor, QFontDatabase, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QColorDialog, QComboBox, QLineEdit
from shiboken6 import isValid


class FontEdit(QComboBox):
    textChanged = Signal(str)

    def __init__(self, value: str = "", parent: Any = None) -> None:
        super().__init__(parent)
        self.setEditable(True)
        self.setMaxVisibleItems(12)
        self.setStyleSheet("QComboBox { combobox-popup: 0; }")
        families = QFontDatabase.families()
        if not families:
            from matplotlib.font_manager import fontManager

            families = sorted({font.name for font in fontManager.ttflist})
        self.addItems(families)
        self.setCurrentText(value)
        self.currentTextChanged.connect(self.textChanged)

    def text(self) -> str:
        return self.currentText()

    def setText(self, value: str) -> None:
        self.setCurrentText(value)


class ColorEdit(QLineEdit):
    """Keep direct code entry while offering Qt's standard/custom color palette."""

    def __init__(self, value: str = "", parent: Any = None) -> None:
        super().__init__(value, parent)
        self.swatch = self.addAction(QIcon(), QLineEdit.ActionPosition.TrailingPosition)
        self.swatch.setToolTip("Choose from color palette")
        self.swatch.triggered.connect(self.choose)
        self.textChanged.connect(self.update_swatch)
        self.update_swatch()

    def update_swatch(self, *_args: Any) -> None:
        if not hasattr(self, "swatch") or not isValid(self.swatch):
            return
        color = QColor(self.text())
        if not color.isValid():
            color = QColor("#808080")
        icon = QIcon()
        for group, modes in (
            (QPalette.ColorGroup.Active, (QIcon.Mode.Normal, QIcon.Mode.Active, QIcon.Mode.Selected)),
            (QPalette.ColorGroup.Disabled, (QIcon.Mode.Disabled,)),
        ):
            pixmap = QPixmap(18, 18)
            pixmap.fill(color)
            painter = QPainter(pixmap)
            # The field's text color contrasts with its surface in either theme.
            # Draw inside the icon so its border cannot be clipped by the action.
            painter.setPen(self.palette().color(group, QPalette.ColorRole.Text))
            painter.drawRect(0, 0, 17, 17)
            painter.end()
            for mode in modes:
                # Qt's generated disabled icon would alter the selected color.
                icon.addPixmap(pixmap, mode)
        self.swatch.setIcon(icon)

    def changeEvent(self, event: Any) -> None:
        super().changeEvent(event)
        if event.type() in {
            QEvent.Type.PaletteChange,
            QEvent.Type.StyleChange,
            QEvent.Type.EnabledChange,
        }:
            self.update_swatch()

    def choose(self) -> None:
        color = QColorDialog.getColor(QColor(self.text()), self, "Choose color")
        if color.isValid():
            self.setText(color.name())


def colormap_swatches(combo: QComboBox) -> None:
    from matplotlib import colormaps

    from materials_data_studio.tools.pxp_style import colormap

    combo.setIconSize(QSize(90, 16))
    combo.setMaxVisibleItems(12)
    combo.setStyleSheet("QComboBox { combobox-popup: 0; }")
    for index in range(combo.count()):
        palette = colormaps[colormap(combo.itemText(index))]
        pixmap = QPixmap(90, 16)
        painter = QPainter(pixmap)
        for x, rgba in enumerate(palette(np.linspace(0, 1, 90))):
            painter.setPen(QColor.fromRgbF(*rgba))
            painter.drawLine(x, 0, x, 15)
        painter.end()
        combo.setItemIcon(index, QIcon(pixmap))


class StyleCombo(QComboBox):
    """Keep stable style codes while showing a visual example beside each."""

    def __init__(self, kind: str, parent: Any = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.setIconSize(QSize(36, 18))

    def addItems(self, texts: Sequence[str]) -> None:
        super().addItems(texts)
        self.refresh_icons()

    def changeEvent(self, event: Any) -> None:
        super().changeEvent(event)
        if hasattr(self, "kind") and event.type() in {
            QEvent.Type.PaletteChange,
            QEvent.Type.StyleChange,
        }:
            self.refresh_icons()

    def refresh_icons(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QFont, QPen

        patterns = {
            "-": Qt.PenStyle.SolidLine,
            "--": Qt.PenStyle.DashLine,
            ":": Qt.PenStyle.DotLine,
            "-.": Qt.PenStyle.DashDotLine,
        }
        glyphs = {"o": "●", "s": "■", "^": "▲", "v": "▼", "d": "◆", "D": "◆", "x": "×", "+": "+"}
        for index in range(self.count()):
            value = self.itemText(index)
            pixmap = QPixmap(36, 18)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setPen(self.palette().text().color())
            if self.kind == "line" and value in patterns:
                painter.setPen(QPen(self.palette().text().color(), 1.6, patterns[value]))
                painter.drawLine(2, 9, 34, 9)
            elif self.kind == "marker" and value in glyphs:
                painter.setFont(QFont("Segoe UI Symbol", 12))
                painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, glyphs[value])
            painter.end()
            self.setItemIcon(index, QIcon(pixmap))
