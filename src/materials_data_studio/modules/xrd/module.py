"""XRD module registration adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import QWidget

from materials_data_studio.core.project_state import ProjectState

if TYPE_CHECKING:
    from materials_data_studio.modules.xrd.widget import XrdWidget


class XrdModule:
    """Registered XRD analysis module."""

    module_id = "xrd"
    display_name = "XRD"

    def __init__(self, state: ProjectState) -> None:
        self.state = state
        self.widget: XrdWidget | None = None
        self._state: dict[str, Any] = {
            "display_axis": "two_theta",
            "y_log_scale": False,
            "stacking": "none",
            "stacking_offset": 100.0,
        }

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        from materials_data_studio.modules.xrd.widget import XrdWidget

        self.widget = XrdWidget(self.state, parent)
        return self.widget

    def serialize_state(self) -> dict[str, Any]:
        if self.widget is not None:
            self._state["display_axis"] = str(self.widget.display_axis.currentData())
            self._state["y_log_scale"] = self.widget.log_y_axis.isChecked()
            self._state["stacking"] = str(self.widget.stacking.currentData())
            self._state["stacking_offset"] = self.widget.stacking_offset.value()
            self._state["splitter_sizes"] = self.widget.splitter.sizes()
            self._state["background"] = {
                "smoothness": self.widget.arpls_smoothness.value(),
                "convergence_ratio": self.widget.arpls_ratio.value(),
                "max_iterations": self.widget.arpls_iterations.value(),
            }
            self._state["figure"] = {
                "legend_enabled": self.widget.legend_enabled.isChecked(),
                "width_cm": self.widget.figure_width.value(),
                "height_cm": self.widget.figure_height.value(),
                "dpi": self.widget.figure_dpi.value(),
                "transparent": self.widget.transparent.isChecked(),
                "current_view": self.widget.export_current_view.isChecked(),
            }
            self._state["peak_fitting"] = self.widget.peak_fitting_state()
            self._state["phase_search"] = self.widget.phase_search_state()
        return dict(self._state)

    def restore_state(self, state: dict[str, Any]) -> None:
        self._state = dict(state)
        if self.widget is not None:
            index = self.widget.display_axis.findData(state.get("display_axis"))
            if index >= 0:
                self.widget.display_axis.setCurrentIndex(index)
            self.widget.log_y_axis.setChecked(bool(state.get("y_log_scale", False)))
            stacking_index = self.widget.stacking.findData(state.get("stacking"))
            if stacking_index >= 0:
                self.widget.stacking.setCurrentIndex(stacking_index)
            self.widget.stacking_offset.setValue(
                float(state.get("stacking_offset", 100.0))
            )
            sizes = state.get("splitter_sizes")
            if isinstance(sizes, list):
                self.widget.splitter.setSizes([int(value) for value in sizes])
            background = state.get("background")
            if isinstance(background, dict):
                self.widget.arpls_smoothness.setValue(
                    float(background.get("smoothness", 1.0e5))
                )
                self.widget.arpls_ratio.setValue(
                    float(background.get("convergence_ratio", 0.01))
                )
                self.widget.arpls_iterations.setValue(
                    int(background.get("max_iterations", 50))
                )
            figure = state.get("figure")
            if isinstance(figure, dict):
                self.widget.legend_enabled.setChecked(
                    bool(figure.get("legend_enabled", True))
                )
                self.widget.figure_width.setValue(
                    float(figure.get("width_cm", 14.0))
                )
                self.widget.figure_height.setValue(
                    float(figure.get("height_cm", 10.0))
                )
                self.widget.figure_dpi.setValue(int(figure.get("dpi", 300)))
                self.widget.transparent.setChecked(
                    bool(figure.get("transparent", False))
                )
                self.widget.export_current_view.setChecked(
                    bool(figure.get("current_view", False))
                )
            peak_fitting = state.get("peak_fitting")
            self.widget.restore_peak_fitting_state(
                peak_fitting if isinstance(peak_fitting, dict) else {}
            )
            self.widget.restore_phase_search_state(state.get("phase_search", {}))
            self.widget.refresh()

    def supported_extensions(self) -> tuple[str, ...]:
        return (".xy", ".dat", ".txt", ".csv", ".rasx", ".cif")
