"""Wheel gestures scroll containers without silently editing form values."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPointF
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QWidget,
)


class InputWheelGuard(QObject):
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() != QEvent.Type.Wheel or not isinstance(event, QWheelEvent) or not isinstance(watched, QWidget):
            return False
        target = watched
        control = None
        while target is not None:
            # Open combo lists and ordinary tables retain their own scrolling.
            if isinstance(target, QAbstractItemView):
                return False
            if isinstance(target, QAbstractSpinBox | QComboBox):
                control = target
                break
            target = target.parentWidget()
        if control is None:
            return False
        event.ignore()
        parent = control.parentWidget()
        while parent is not None:
            if isinstance(parent, QAbstractScrollArea):
                viewport = parent.viewport()
                forwarded = QWheelEvent(
                    QPointF(viewport.mapFromGlobal(event.globalPosition().toPoint())), event.globalPosition(),
                    event.pixelDelta(), event.angleDelta(), event.buttons(), event.modifiers(),
                    event.phase(), event.inverted(), event.source(), event.pointingDevice(),
                )
                QApplication.sendEvent(viewport, forwarded)
                event.accept()
                break
            parent = parent.parentWidget()
        return True


def install_input_wheel_guard() -> None:
    application = QApplication.instance()
    if not isinstance(application, QApplication) or getattr(application, "_mds_input_wheel_guard", None) is not None:
        return
    guard = InputWheelGuard(application)
    application.installEventFilter(guard)
    application._mds_input_wheel_guard = guard  # type: ignore[attr-defined]
