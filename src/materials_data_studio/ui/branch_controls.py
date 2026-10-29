"""Persist analysis parameters independently for each selected branch."""

from __future__ import annotations

from typing import Any, cast

from PySide6.QtWidgets import QCheckBox, QComboBox, QGroupBox, QLineEdit, QWidget

from materials_data_studio.core.models import Dataset
from materials_data_studio.core.project_state import ProjectState


class BranchControls:
    def __init__(self, state: ProjectState, controls: dict[str, QWidget]) -> None:
        self.state, self.controls = state, controls
        self.active: Dataset | None = None
        self.loading = False

        self.defaults = self.values()
        for control in controls.values():
            if isinstance(control, QComboBox):
                control.currentIndexChanged.connect(self.save)
            elif isinstance(control, QCheckBox):
                control.toggled.connect(self.save)
            elif isinstance(control, QLineEdit):
                control.editingFinished.connect(self.save)
            else:
                control.valueChanged.connect(self.save)  # type: ignore[attr-defined]

    def values(self) -> dict[str, Any]:
        result = {}
        for key, control in self.controls.items():
            result[key] = (
                control.currentIndex()
                if isinstance(control, QComboBox)
                else control.isChecked()
                if isinstance(control, QCheckBox)
                else control.text()
                if isinstance(control, QLineEdit)
                else cast(Any, control).value()
            )
        return result

    def save(self, *_args: Any) -> None:
        if (
            not self.loading
            and self.active is not None
            and self.active.metadata.get("measurement_parent")
        ):
            self.active.metadata["branch_analysis_controls"] = self.values()
            self.state.modified = True

    def load(self, dataset: Dataset | None) -> None:
        self.active = dataset
        if dataset is None or not dataset.metadata.get("measurement_parent"):
            return
        self.loading = True
        values = dataset.metadata.get("branch_analysis_controls", self.defaults)
        for key, control in self.controls.items():
            was_blocked = control.blockSignals(True)
            value = values.get(key, self.defaults[key])
            if isinstance(control, QComboBox):
                control.setCurrentIndex(int(value))
            elif isinstance(control, QCheckBox):
                control.setChecked(bool(value))
            elif isinstance(control, QLineEdit):
                control.setText(str(value))
            else:
                control.setValue(value)  # type: ignore[attr-defined]
            control.blockSignals(was_blocked)
        self.loading = False


def branch_context(widget: QWidget, dataset: Dataset | None) -> None:
    """File rows expose shared data settings; analyses target a selected child."""
    if dataset is not None and dataset.metadata.get("measurement_container"):
        for card in widget.findChildren(QGroupBox):
            if card.property("settingsPage") in {1, 2}:
                card.setEnabled(False)
