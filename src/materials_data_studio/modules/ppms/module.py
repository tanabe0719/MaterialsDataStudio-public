"""PPMS module registration adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import QWidget

from materials_data_studio.core.project_state import ProjectState

if TYPE_CHECKING:
    from materials_data_studio.modules.ppms.widget import PpmsWidget


class PpmsModule:
    """Registered PPMS Heat Capacity and transport module."""

    module_id = "ppms"
    display_name = "PPMS"

    def __init__(self, state: ProjectState) -> None:
        self.state = state
        self._state: dict[str, Any] = {"active_tab": 0}
        self.widget: PpmsWidget | None = None

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        from materials_data_studio.modules.ppms.widget import PpmsWidget

        self.widget = PpmsWidget(self.state, parent)
        return self.widget

    def serialize_state(self) -> dict[str, Any]:
        if self.widget is not None:
            self._state = self.widget.serialize_state()
        return dict(self._state)

    def restore_state(self, state: dict[str, Any]) -> None:
        self._state = dict(state)
        if self.widget is not None:
            self.widget.restore_state(state)

    def supported_extensions(self) -> tuple[str, ...]:
        return (".dat",)
