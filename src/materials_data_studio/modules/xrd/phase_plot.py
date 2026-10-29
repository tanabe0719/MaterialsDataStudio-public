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
