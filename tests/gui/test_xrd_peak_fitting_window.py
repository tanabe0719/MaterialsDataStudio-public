from __future__ import annotations

import importlib.util

import numpy as np
import pytest

from materials_data_studio.core.models import Dataset, ModuleType
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.modules.xrd.cif_backend import UnitCell
from materials_data_studio.modules.xrd.peak_fitting.profile import (
    pseudo_voigt_profile,
)
from materials_data_studio.modules.xrd.peak_fitting.reflections import (
    d_spacing,
    shifted_two_theta,
)

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None
    or importlib.util.find_spec("pyqtgraph") is None,
    reason="Qt GUI dependencies are not installed",
)

CIF_TEXT = """data_phase
_symmetry_space_group_name_H-M 'P m -3 m'
_cell_length_a 4
_cell_length_b 4
_cell_length_c 4
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
X1 Na 0 0 0
"""


def _state() -> ProjectState:
    cell = UnitCell(4, 4, 4, 90, 90, 90)
    wavelength = 1.540598
    hkls = ((1, 1, 0), (1, 1, 1), (2, 0, 0))
    spacings = [d_spacing(hkl, cell) for hkl in hkls]
    positions = [shifted_two_theta(value, wavelength) for value in spacings]
    intensities = [100.0, 60.0, 30.0]
    x = np.linspace(20, 70, 1800)
    y = np.full_like(x, 8.0)
    for position, area in zip(positions, (700.0, 400.0, 200.0), strict=True):
        y += area * pseudo_voigt_profile(x, position + 0.08, 0.28, 0.3)
    state = ProjectState()
    state.add_dataset(
        Dataset(
            "Observed",
            ModuleType.XRD,
            {"x": x, "intensity": y},
            metadata={
                "source_axis": "two_theta",
                "wavelength_a": wavelength,
                "normalization": "none",
                "scale": 1.0,
                "offset": 0.0,
                "background_enabled": False,
            },
        )
    )
    state.add_dataset(
        Dataset(
            "Phase",
            ModuleType.XRD,
            {"x": np.asarray(positions), "intensity": np.asarray(intensities)},
            source_filename="phase.cif",
            source_bytes=CIF_TEXT.encode(),
            metadata={
                "source_axis": "two_theta",
                "wavelength_a": wavelength,
                "normalization": "none",
                "scale": 1.0,
                "offset": 0.0,
                "background_enabled": False,
                "is_simulated_phase": True,
                "edited_cif": CIF_TEXT,
                "fwhm_deg": 0.2,
                "profile_step_deg": 0.01,
                "two_theta_range": [20.0, 70.0],
                "stick_two_theta_deg": positions,
                "stick_intensity": intensities,
                "stick_hkl_labels": [
                    "(1 1 0)",
                    "(1 1 1)",
                    "(2 0 0)",
                ],
                "stick_d_spacing_a": spacings,
            },
        )
    )
    return state


def test_peak_fitting_entry_window_worker_repeat_and_state(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    import materials_data_studio.modules.xrd.peak_fitting.fitting_window as fitting_module
    from materials_data_studio.modules.xrd.widget import XrdWidget

    state = _state()
    widget = XrdWidget(state)
    qtbot.addWidget(widget)
    assert widget.open_peak_fitting_button.isEnabled()
    widget.open_peak_fitting_button.click()
    window = widget._peak_fitting_window
    assert window is not None
    qtbot.addWidget(window)
    assert window.isVisible()
    assert window.dataset_combo.count() == 1
    assert window.phase_combo.count() == 1
    assert "zero_shift" in window._parameter_rows
    assert window.background_mode.count() == 1
    assert window.background_mode.currentData() == "current"
    assert window.reflection_table.rowCount() == 3
    area_row = window._parameter_rows["area_1"]
    assert window.parameter_table.cellWidget(area_row, 0) is None
    assert window.parameter_table.item(area_row, 0).text() == "Reflection Groups"
    area_checkbox = window.reflection_table.cellWidget(0, 0)
    assert area_checkbox is not None
    area_checkbox.setChecked(False)
    assert not window._configuration().parameters["area_1"].refine
    area_checkbox.setChecked(True)
    first_window = window
    widget.open_peak_fitting_button.click()
    assert widget._peak_fitting_window is first_window

    class CapturingPool:
        def __init__(self) -> None:
            self.started = []

        def start(self, worker) -> None:  # type: ignore[no-untyped-def]
            self.started.append(worker)

    assert not window.steps.isTabEnabled(1)
    window._run_fit()
    assert window._fit_worker is None
    original_raw = window._dataset().array("intensity").copy()
    window.background_button.click()
    qtbot.waitUntil(lambda: not window._background_busy, timeout=15000)
    assert window.steps.isTabEnabled(1) and window.steps.currentIndex() == 1
    assert window._dataset().metadata["background_enabled"]
    assert np.array_equal(window._dataset().array("intensity"), original_raw)
    pool = CapturingPool()
    monkeypatch.setattr(
        fitting_module.QThreadPool,
        "globalInstance",
        staticmethod(lambda: pool),
    )
    window._run_fit()
    assert len(pool.started) == 1
    assert window.run_button.isEnabled()
    assert window.run_button.text() == "Stop Fit"
    assert window.run_button.property("danger") is True
    assert window.run_button.property("primary") is False
    assert window.parameter_table.minimumHeight() >= 320
    assert (
        window.parameter_table.horizontalHeaderItem(3).text()
        == "Standard error"
    )
    pool.started[0].run()
    assert window._result is not None
    assert window.run_button.isEnabled()
    assert window.run_button.text() == "Run Fit"
    assert window.run_button.property("danger") is False
    assert window.run_button.property("primary") is True
    assert window.apply_button.isEnabled()
    assert window.undo_fit_button.isEnabled()
    assert window.reflection_table.rowCount() == 3
    assert "Cycle" in window.progress_details.toPlainText()
    zero_row = window._parameter_rows["zero_shift"]
    assert window.parameter_table.item(zero_row, 3).text() != "—"

    previous_position = window._preview_groups[0].fitted_two_theta_deg
    window.parameter_table.item(zero_row, 2).setText("0.25")
    window._refresh_plots()
    assert window._preview_groups[0].fitted_two_theta_deg == pytest.approx(
        previous_position + 0.17
    )
    assert not window.apply_button.isEnabled()

    window._run_fit()
    assert len(pool.started) == 2
    pool.started[1].run()
    assert window._result is not None
    window.undo_fit_button.click()
    assert window._result is None
    assert float(window.parameter_table.item(zero_row, 2).text()) == pytest.approx(
        0.25
    )
    assert not window.undo_fit_button.isEnabled()
    saved = window.serialize_state()
    assert saved["selected_dataset_id"] == state.datasets[0].dataset_id
    assert saved["last_result"] is None
    assert saved["le_bail_cycles"] == 20


def test_peak_fitting_reflections_follow_fit_and_excluded_ranges_immediately(
    qtbot,
) -> None:  # type: ignore[no-untyped-def]
    from materials_data_studio.modules.xrd.widget import XrdWidget

    state = _state()
    widget = XrdWidget(state)
    qtbot.addWidget(widget)
    widget.open_peak_fitting_button.click()
    window = widget._peak_fitting_window
    assert window is not None
    qtbot.addWidget(window)
    positions = state.datasets[1].metadata["stick_two_theta_deg"]

    window.fit_min.setValue(float(positions[1] - 0.01))
    assert window.reflection_table.rowCount() == 2
    window.exclusions.setText(
        f"{float(positions[1] - 0.02):.8g}-{float(positions[1] + 0.02):.8g}"
    )
    assert window.reflection_table.rowCount() == 1
    assert "2 0 0" in window.reflection_table.item(0, 3).text()


def test_peak_fitting_reset_requires_confirmation(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtWidgets import QMessageBox

    from materials_data_studio.modules.xrd.widget import XrdWidget

    widget = XrdWidget(_state())
    qtbot.addWidget(widget)
    widget.open_peak_fitting_button.click()
    window = widget._peak_fitting_window
    assert window is not None
    qtbot.addWidget(window)
    window.exclusions.setText("30-31")

    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.No),
    )
    window._reset_initial()
    assert window.exclusions.text() == "30-31"

    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes),
    )
    window._reset_initial()
    assert window.exclusions.text() == ""


def test_peak_fitting_stop_requests_cooperative_cancellation(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    import materials_data_studio.modules.xrd.peak_fitting.fitting_window as fitting_module
    from materials_data_studio.modules.xrd.widget import XrdWidget

    widget = XrdWidget(_state())
    qtbot.addWidget(widget)
    widget.open_peak_fitting_button.click()
    window = widget._peak_fitting_window
    assert window is not None
    qtbot.addWidget(window)

    class CapturingPool:
        def __init__(self) -> None:
            self.started = []

        def start(self, worker) -> None:  # type: ignore[no-untyped-def]
            self.started.append(worker)

    assert not window.steps.isTabEnabled(1)
    window._run_fit()
    assert window._fit_worker is None
    original_raw = window._dataset().array("intensity").copy()
    window.background_button.click()
    qtbot.waitUntil(lambda: not window._background_busy, timeout=15000)
    assert window.steps.isTabEnabled(1) and window.steps.currentIndex() == 1
    assert window._dataset().metadata["background_enabled"]
    assert np.array_equal(window._dataset().array("intensity"), original_raw)
    pool = CapturingPool()
    monkeypatch.setattr(
        fitting_module.QThreadPool,
        "globalInstance",
        staticmethod(lambda: pool),
    )
    window._run_fit()
    window._run_or_stop_fit()
    assert window.run_button.text() == "Stopping…"
    assert window.run_button.property("danger") is True
    assert not window.run_button.isEnabled()
    pool.started[0].run()
    assert window._result is None
    assert window.run_button.text() == "Run Fit"
    assert "cancelled" in window.status.toPlainText()


def test_peak_fitting_button_explains_missing_phase(qtbot) -> None:  # type: ignore[no-untyped-def]
    from materials_data_studio.modules.xrd.widget import XrdWidget

    state = _state()
    state.datasets.pop()
    widget = XrdWidget(state)
    qtbot.addWidget(widget)
    assert not widget.open_peak_fitting_button.isEnabled()
    assert "simulated CIF phase" in widget.peak_fitting_availability.text()
