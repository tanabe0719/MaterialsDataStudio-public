"""Continuous candidate-stick scaling without changing experimental intensity."""
from __future__ import annotations

import math

from PySide6.QtCore import QEvent, QObject, Qt, Signal


class StickScaleFilter(QObject):
    scaled = Signal(float)

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Wheel and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.pixelDelta().y() or event.angleDelta().y()
            if delta:
                self.scaled.emit(math.exp(max(-600, min(600, delta)) / 600))
                event.accept()
                return True
        return False


class CandidateCheckFilter(QObject):
    """Checkbox clicks toggle a phase without clearing other selected rows."""

    def __init__(self, table):
        super().__init__(table)
        self.table = table

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            index = self.table.indexAt(event.position().toPoint())
            if index.isValid() and index.column() == 0 and event.position().x() < self.table.visualRect(index).left() + 26:
                item = self.table.item(index.row(), 0)
                item.setCheckState(Qt.CheckState.Unchecked if item.checkState() == Qt.CheckState.Checked else Qt.CheckState.Checked)
                event.accept()
                return True
        return False
