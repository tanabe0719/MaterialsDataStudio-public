"""Wheel gestures scroll forms; deliberate keyboard and popup edits still work."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from materials_data_studio.ui.input_wheel_guard import install_input_wheel_guard


def wheel(widget, delta=-120):
    local = QPointF(widget.rect().center())
    event = QWheelEvent(local, QPointF(widget.mapToGlobal(local.toPoint())), QPoint(), QPoint(0, delta), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)


def test_wheel_never_changes_focused_or_unfocused_inputs(qtbot):
    install_input_wheel_guard()
    scroll = QScrollArea()
    content = QWidget()
    layout = QVBoxLayout(content)
    combo = QComboBox()
    combo.addItems(["A", "B", "C"])
    editable = QComboBox()
    editable.setEditable(True)
    editable.addItems(["one", "two", "three"])
    integer, floating = QSpinBox(), QDoubleSpinBox()
    integer.setValue(50)
    floating.setValue(12.5)
    for control in (combo, editable, integer, floating):
        layout.addWidget(control)
    content.setMinimumHeight(1000)
    scroll.setWidget(content)
    scroll.resize(300, 200)
    qtbot.addWidget(scroll)
    scroll.show()
    for control in (combo, editable, integer, floating):
        control.setFocus()
        before = control.currentIndex() if isinstance(control, QComboBox) else control.value()
        wheel(control)
        if control is editable or isinstance(control, QDoubleSpinBox):
            child = control.lineEdit()
            assert child is not None
            wheel(child)
        after = control.currentIndex() if isinstance(control, QComboBox) else control.value()
        assert after == before
    assert scroll.verticalScrollBar().value() > 0
    integer.setFocus()
    qtbot.keyClick(integer, Qt.Key.Key_Up)
    assert integer.value() == 51
    combo.setFocus()
    qtbot.keyClick(combo, Qt.Key.Key_Down)
    assert combo.currentIndex() == 1
