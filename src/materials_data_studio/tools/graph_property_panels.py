"""Pinned object selection and semantic sections for the graph inspector."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QListWidget,
    QPushButton,
    QVBoxLayout,
)


class AxisSelector(QComboBox):
    axesChanged = Signal(tuple)

    def __init__(self, names, horizontal, parent=None):
        super().__init__(parent)
        self.setMinimumContentsLength(10)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.names, self.horizontal = tuple(names), set(horizontal)
        self.selected_axes = (names[0],) if names else ()
        self.addItems(names)
        self.addItems(["Multiple Selection…", "Select All Axes", "Select Horizontal Axes", "Select Vertical Axes"])
        self.currentIndexChanged.connect(self._single)
        self.activated.connect(self._action)

    def refresh_language(self):
        from materials_data_studio.core.settings import create_settings
        from materials_data_studio.ui.localization import JA
        japanese = create_settings().value("appearance/language", "en") == "ja"
        for i, text in enumerate(("Multiple Selection…", "Select All Axes", "Select Horizontal Axes", "Select Vertical Axes"), len(self.names)):
            self.setItemText(i, JA.get(text, text) if japanese else text)

    def _single(self, index):
        if 0 <= index < len(self.names):
            self.selected_axes = (self.names[index],)
            self.axesChanged.emit(self.selected_axes)

    def select_axes(self, names):
        selected = tuple(name for name in self.names if name in names)
        if not selected:
            return
        self.selected_axes = selected
        self.blockSignals(True)
        self.setCurrentIndex(self.names.index(selected[0]) if len(selected) == 1 else len(self.names))
        self.blockSignals(False)
        self.axesChanged.emit(selected)

    def _action(self, index):
        action = index - len(self.names)
        if action < 0:
            return
        if action:
            self.select_axes([n for n in self.names if action == 1 or (n in self.horizontal) == (action == 2)])
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Multiple Axis Selection")
        layout = QHBoxLayout(dialog)
        axes = QListWidget()
        axes.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        axes.addItems(self.names)
        for i in range(axes.count()):
            axes.item(i).setSelected(axes.item(i).text() in self.selected_axes)
        layout.addWidget(axes)
        right = QVBoxLayout()
        for caption, mode in (("Select All Axes", 0), ("Select Vertical Axes", 1), ("Select Horizontal Axes", 2)):
            button = QPushButton(caption)
            def select(_checked=False, mode=mode):
                for i in range(axes.count()):
                    name = axes.item(i).text()
                    axes.item(i).setSelected(mode == 0 or (name in self.horizontal) == (mode == 2))
            button.clicked.connect(select)
            right.addWidget(button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        right.addWidget(buttons)
        layout.addLayout(right)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.select_axes([item.text() for item in axes.selectedItems()])
        else:
            self.select_axes(self.selected_axes)
        dialog.deleteLater()


def property_section(kind: str, key: str) -> str:
    if kind == "axis":
        if key in {"minimum", "maximum", "log"}:
            return "Axis Range"
        if key in {"tick_mode", "tick_locations", "tick_labels", "major_interval", "minor_count", "tick_count"}:
            return "Auto / Man Ticks"
        if key.startswith("label_") or key in {"font", "font_size"}:
            return "Axis Label"
        if "tick" in key or key.startswith(("major_", "minor_", "subminor_")) or key.startswith("grid"):
            return "Ticks and Grids"
        return "Axis"
    if kind == "trace":
        if "offset" in key or "multiplier" in key:
            return "Offsets"
        if "error" in key:
            return "Error Bars"
        if key in {"axis", "x_axis"} or "wave" in key:
            return "Data and Axes"
        return "Line and Marker"
    if kind == "geometry":
        return "Margins" if key.startswith("margin_") else "Fonts" if "font" in key or "marker" in key else "Size"
    if key in {"x", "y", "rotation", "anchor", "x_mode", "y_mode"}:
        return "Position"
    if "font" in key or "size" in key:
        return "Fonts"
    return "Appearance"
