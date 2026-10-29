"""Complete PPMS Heat Capacity and Electrical Resistivity workspaces."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QFileDialog, QTabWidget, QVBoxLayout, QWidget

from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.core.source_io import import_source
from materials_data_studio.modules.ppms.generic_widget import GenericPpmsWidget
from materials_data_studio.modules.ppms.heat_capacity_widget import HeatCapacityWidget
from materials_data_studio.modules.ppms.io import detect_ppms_kind, read_ppms_text
from materials_data_studio.modules.ppms.resistivity_widget import ResistivityWidget
from materials_data_studio.ui.dialogs import show_error
from materials_data_studio.ui.tasks import blocking_task


def _parse_source(file: Any) -> tuple[str, Any]:
    from materials_data_studio.modules.ppms.generic import parse_generic_ppms_text
    from materials_data_studio.modules.ppms.heat_capacity_parser import parse_heat_capacity_text
    from materials_data_studio.modules.ppms.resistivity_parser import parse_resistivity_text

    text = read_ppms_text(file)
    kind = detect_ppms_kind(text)
    reader = {
        "heat_capacity": parse_heat_capacity_text,
        "resistivity": parse_resistivity_text,
        "generic": parse_generic_ppms_text,
    }[kind]
    return kind, reader(text)


class PpmsWidget(QWidget):
    """Route PPMS files and commands to specialized and arbitrary-data tabs."""

    def __init__(self, state: ProjectState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setProperty("navigation", "mode")
        self.tabs.tabBar().setExpanding(False)
        self.heat_capacity = HeatCapacityWidget(state, self.tabs)
        self.resistivity = ResistivityWidget(state, self.tabs)
        self.generic = GenericPpmsWidget(state, self.tabs)
        self.tabs.addTab(self.heat_capacity, "Heat Cap.")
        self.tabs.addTab(self.resistivity, "Resistivity")
        self.tabs.addTab(self.generic, "Custom Plot")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tabs)

    @property
    def active_workspace(
        self,
    ) -> HeatCapacityWidget | ResistivityWidget | GenericPpmsWidget:
        widget = self.tabs.currentWidget()
        assert isinstance(widget, (HeatCapacityWidget, ResistivityWidget, GenericPpmsWidget))
        return widget

    @property
    def dataset_list(self):  # type: ignore[no-untyped-def]
        """Expose the active list to the application-level remove command."""
        return self.active_workspace.dataset_list

    def choose_import_files(self) -> None:
        names, _ = QFileDialog.getOpenFileNames(
            self,
            "Import PPMS Data",
            "",
            "PPMS DAT (*.dat);;All files (*)",
        )
        if names:
            self.import_files([Path(name) for name in names])

    def import_files(self, paths: list[Path]) -> None:
        """Auto-detect each PPMS DAT file and route mixed multi-file selections."""
        for path in paths:
            try:
                source = blocking_task(
                    self,
                    f"Import {path.name}",
                    lambda source_path=path: import_source(source_path, _parse_source),
                )
                kind, parsed = source.result
                workspace = {
                    "heat_capacity": self.heat_capacity,
                    "resistivity": self.resistivity,
                    "generic": self.generic,
                }[kind]
                workspace._add_result(path, parsed, source)
                self.tabs.setCurrentWidget(workspace)
            except Exception as exc:
                show_error(self, "PPMS Import Error", f"{path.name}: {exc}")

    def import_dropped_files(self, paths: list[Path]) -> None:
        """Keep files in Custom Plot when that drop target is explicitly active."""
        if self.active_workspace is self.generic:
            self.generic.import_files(paths)
            self.tabs.setCurrentWidget(self.generic)
            return
        self.import_files(paths)

    def export_data(self) -> None:
        self.active_workspace.export_data()

    def export_figure(self) -> None:
        self.active_workspace.export_figure()

    def refresh(self) -> None:
        self.heat_capacity.refresh()
        self.resistivity.refresh()
        self.generic.refresh()

    def serialize_state(self) -> dict[str, Any]:
        return {
            "active_tab": self.tabs.currentIndex(),
            "heat_capacity": self.heat_capacity.serialize_state(),
            "resistivity": self.resistivity.serialize_state(),
            "generic": self.generic.serialize_state(),
        }

    def restore_state(self, state: dict[str, Any]) -> None:
        heat = state.get("heat_capacity")
        if isinstance(heat, dict):
            self.heat_capacity.restore_state(heat)
        transport = state.get("resistivity")
        if isinstance(transport, dict):
            self.resistivity.restore_state(transport)
        generic = state.get("generic")
        if isinstance(generic, dict):
            self.generic.restore_state(generic)
        self.tabs.setCurrentIndex(
            max(0, min(int(state.get("active_tab", 0)), self.tabs.count() - 1))
        )

    def apply_theme(self, theme: str) -> None:
        self.heat_capacity.apply_theme(theme)
        self.resistivity.apply_theme(theme)
        self.generic.apply_theme(theme)
