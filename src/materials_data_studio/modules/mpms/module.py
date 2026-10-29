"""MPMS module registration adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import QWidget

from materials_data_studio.core.project_state import ProjectState

if TYPE_CHECKING:
    from materials_data_studio.modules.mpms.widget import MpmsWidget


class MpmsModule:
    """Registered MPMS analysis module."""

    module_id = "mpms"
    display_name = "MPMS"

    def __init__(self, state: ProjectState) -> None:
        self.state = state
        self.widget: MpmsWidget | None = None
        self._state: dict[str, Any] = {"active_sub_tab": 0}

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        from materials_data_studio.modules.mpms.widget import MpmsWidget

        self.widget = MpmsWidget(self.state, parent)
        return self.widget

    def serialize_state(self) -> dict[str, Any]:
        if self.widget is not None:
            self._state["active_sub_tab"] = self.widget.tabs.currentIndex()
            self._state["splitter_sizes"] = self.widget.splitter.sizes()
            self._state["mt"] = {
                "plot_mode": self.widget.mt_mode.currentData(),
                "fit_branch": self.widget.fit_branch.currentText(),
                "t_min": self.widget.fit_t_min.value(),
                "t_max": self.widget.fit_t_max.value(),
                "mu_eff": self.widget.mu_eff.value(),
                "chi0": self.widget.chi0.value(),
                "fix_c": self.widget.fix_c.isChecked(),
                "fix_chi0": self.widget.fix_chi0.isChecked(),
            }
            self._state["mh"] = {
                "field_unit": self.widget.field_unit.currentData(),
                "vertical_unit": self.widget.magnetization_unit.currentData(),
                "center_zero": self.widget.center_zero.isChecked(),
            }
            self._state["figure"] = {
                "legend_enabled": self.widget.legend_enabled.isChecked(),
                "width_cm": self.widget.figure_width.value(),
                "height_cm": self.widget.figure_height.value(),
                "dpi": self.widget.figure_dpi.value(),
                "transparent": self.widget.transparent.isChecked(),
                "current_view": self.widget.export_current_view.isChecked(),
            }
        return dict(self._state)

    def restore_state(self, state: dict[str, Any]) -> None:
        self._state = dict(state)
        if self.widget is not None:
            self.widget.tabs.setCurrentIndex(int(state.get("active_sub_tab", 0)))
            sizes = state.get("splitter_sizes")
            if isinstance(sizes, list):
                self.widget.splitter.setSizes([int(value) for value in sizes])
            mt = state.get("mt")
            if isinstance(mt, dict):
                index = self.widget.mt_mode.findData(mt.get("plot_mode"))
                if index >= 0:
                    self.widget.mt_mode.setCurrentIndex(index)
                self.widget.fit_branch.setCurrentText(
                    str(mt.get("fit_branch", "FC"))
                )
                self.widget.fit_t_min.setValue(float(mt.get("t_min", 100.0)))
                self.widget.fit_t_max.setValue(float(mt.get("t_max", 350.0)))
                self.widget.mu_eff.setValue(float(mt.get("mu_eff", 4.9)))
                self.widget.chi0.setValue(float(mt.get("chi0", 0.0)))
                self.widget.fix_c.setChecked(bool(mt.get("fix_c", False)))
                self.widget.fix_chi0.setChecked(bool(mt.get("fix_chi0", False)))
            mh = state.get("mh")
            if isinstance(mh, dict):
                field_index = self.widget.field_unit.findData(mh.get("field_unit"))
                vertical_index = self.widget.magnetization_unit.findData(
                    mh.get("vertical_unit")
                )
                if field_index >= 0:
                    self.widget.field_unit.setCurrentIndex(field_index)
                if vertical_index >= 0:
                    self.widget.magnetization_unit.setCurrentIndex(vertical_index)
                self.widget.center_zero.setChecked(
                    bool(mh.get("center_zero", False))
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
            self.widget.refresh()

    def supported_extensions(self) -> tuple[str, ...]:
        return (".dat",)
