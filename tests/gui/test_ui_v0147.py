"""Display bounds, arbitrary-column branches and theme persistence."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication, QWidget

from materials_data_studio.core.measurement_branches import apply_branches
from materials_data_studio.core.models import Dataset, ModuleType
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.core.session import load_session, save_session
from materials_data_studio.modules.ppms.generic import parse_generic_ppms_text
from materials_data_studio.modules.ppms.generic_widget import GenericPpmsWidget
from materials_data_studio.modules.ppms.heat_capacity_parser import parse_heat_capacity_text
from materials_data_studio.modules.ppms.heat_capacity_widget import HeatCapacityWidget
from materials_data_studio.modules.ppms.resistivity_parser import parse_resistivity_text
from materials_data_studio.modules.ppms.resistivity_widget import ResistivityWidget
from materials_data_studio.modules.xrd.phase_store import PhaseStore
from materials_data_studio.modules.xrd.phase_window import PhaseSearchWindow
from materials_data_studio.tools.graph_window import GraphEditorWindow
from materials_data_studio.ui.branch_editor import BranchEditor
from materials_data_studio.ui.icons import toolbar_icon
from materials_data_studio.ui.theme import DARK, apply_application_theme

DEMO = Path(__file__).resolve().parents[2] / "docs/demo_data"


@pytest.mark.parametrize("model", ["arrhenius", "vrh"])
def test_transport_fit_and_residual_use_saved_physical_bounds(qtbot, model):
    state = ProjectState()
    widget = ResistivityWidget(state)
    qtbot.addWidget(widget)
    path = DEMO / "ppms_resistivity_demo.dat"
    widget._add_result(path, parse_resistivity_text(path.read_text()))
    child = widget.dataset_list.current_dataset()
    t = child.array("temperature_k")
    lower, upper = np.quantile(t, [.25, .75])
    widget.display_mode.setCurrentIndex(widget.display_mode.findData(model))
    widget.fit_min.setValue(float(lower))
    widget.fit_max.setValue(float(upper))
    widget._run_fit()
    bridge = child.metadata["measurement_branch"]["bridge"]
    result = child.analysis_results[f"{model}_bridge_{bridge}"]
    before = child.processed_data[f"bridge_{bridge}_{model}_fit"].copy()
    widget.fit_min.setValue(float(np.min(t)))  # Unrun control edits must not alter fitted extent.
    widget.fit_max.setValue(float(np.max(t)))
    fit = next(curve for curve in widget.build_plot_spec().curves if curve.label.endswith("fit"))
    selected = (t >= result["minimum_k"]) & (t <= result["maximum_k"])
    expected_x, *_ = widget._transform(t[selected], np.ones(selected.sum()))
    np.testing.assert_allclose(fit.x, expected_x)
    residual = widget.residual_widget._materials_plot_spec.curves[0]
    assert np.min(residual.x) >= lower and np.max(residual.x) <= upper
    np.testing.assert_array_equal(child.processed_data[f"bridge_{bridge}_{model}_fit"], before)


def test_heat_low_fit_display_is_bounded_without_changing_calculation(qtbot):
    widget = HeatCapacityWidget(ProjectState())
    qtbot.addWidget(widget)
    path = DEMO / "ppms_heat_capacity_demo.dat"
    widget._add_result(path, parse_heat_capacity_text(path.read_text()))
    widget.mass.setValue(2)
    widget.molar_mass.setValue(58.44)
    widget._save_sample()
    widget.low_min.setValue(3)
    widget.low_max.setValue(7)
    widget._run_low_fit()
    child = widget.dataset_list.current_dataset()
    bounds = child.analysis_results["low_temperature"]["temperature_range_k"]
    widget.unit.setCurrentIndex(widget.unit.findData("molar"))
    widget.low_max.setValue(20)
    fit = next(curve for curve in widget.build_plot_spec("cp_over_t").curves if curve.label.endswith("fit"))
    assert len(fit.x) >= 3
    assert np.min(fit.x) >= bounds[0] ** 2 and np.max(fit.x) <= bounds[1] ** 2


def test_generic_branches_preserve_columns_and_independent_session_state(qtbot, tmp_path):
    state = ProjectState()
    widget = GenericPpmsWidget(state)
    qtbot.addWidget(widget)
    path = DEMO / "ppms_custom_demo.dat"
    widget._add_result(path, parse_generic_ppms_text(path.read_text()))
    root = state.datasets[0]
    key = root.metadata["generic_columns"][1]["key"]
    root.processed_data[key] = np.asarray(root.raw_data[key], dtype=float) * 2  # An edited numeric column.
    root.analysis_results["generic_fit"] = {"x": [0, 1], "y_fit": [2, 3]}
    definitions = [{"id": "first", "name": "First", "start": 0, "stop": 9, "mode": "generic"},
                   {"id": "second", "name": "Second", "start": 10, "stop": 19, "mode": "generic"}]
    apply_branches(state, root, definitions)
    widget.refresh()
    assert not any(curve.label.endswith(" fit") for curve in widget.build_plot_spec().curves)
    first, second = state.datasets[1:]
    np.testing.assert_array_equal(first.array(key), root.array(key)[:10])
    assert widget.dataset_list.list_widget.topLevelItem(0).childCount() == 2
    widget.dataset_list.list_widget.setCurrentItem(widget.dataset_list._items[first.dataset_id])
    widget.fit_min.setValue(2)
    widget.dataset_list.list_widget.setCurrentItem(widget.dataset_list._items[second.dataset_id])
    widget.fit_min.setValue(12)
    widget.dataset_list.list_widget.setCurrentItem(widget.dataset_list._items[first.dataset_id])
    assert widget.fit_min.value() == 2
    editor = BranchEditor(state, root)
    qtbot.addWidget(editor)
    assert editor.table.isColumnHidden(3) and editor.table.isColumnHidden(4)
    editor._cell(0, 0).setText("Renamed")
    editor.commit()
    assert first.display_name == "Renamed"
    save_session(state, tmp_path / "branches.mdss")
    restored = load_session(tmp_path / "branches.mdss")
    assert restored.datasets[1].metadata["branch_analysis_controls"]["fit_min"] == 2
    np.testing.assert_array_equal(restored.datasets[1].array(key), first.array(key))


def test_phase_preview_respects_dark_theme_and_preserves_observed_style(qtbot, tmp_path):
    app = QApplication.instance()
    apply_application_theme(app, "dark")
    state = ProjectState()
    dataset = Dataset("Synthetic", ModuleType.XRD, {"x": np.linspace(15, 80, 101), "intensity": np.ones(101)})
    state.add_dataset(dataset)
    style = dataset.plot_style.to_dict()
    parent = QWidget()
    qtbot.addWidget(parent)
    phase = PhaseSearchWindow(state, parent, store=PhaseStore(tmp_path), selected_dataset_id=dataset.dataset_id)
    qtbot.addWidget(phase)
    assert phase.plot.property("materialsTheme") == "dark"
    assert phase.plot._materials_plot_spec.curves[0].style.color == "#e2e8f0"
    assert app.palette().window().color().name().upper() == DARK.background
    assert dataset.plot_style.to_dict() == style
    phase.apply_theme("light")
    assert phase.plot.property("materialsTheme") == "light"
    apply_application_theme(app, "light")


def test_graph_toolbar_uses_active_theme_without_matching_a_literal_color(qtbot):
    app = QApplication.instance()
    viewer = GraphEditorWindow()
    qtbot.addWidget(viewer)
    viewer._discard_approved = True
    apply_application_theme(app, "dark")
    viewer.update_icons()
    assert viewer.open_action.icon().pixmap(32, 32).toImage() == toolbar_icon("open", "dark").pixmap(32, 32).toImage()
    apply_application_theme(app, "light")


def test_small_main_window_keeps_settings_navigation_reachable(qtbot):
    from PySide6.QtWidgets import QScrollArea

    from materials_data_studio.ui.main_window import MainWindow
    window = MainWindow()
    qtbot.addWidget(window)
    window.resize(940, 490)
    window.show()
    qtbot.wait(50)
    xrd = window._ensure_module_widget(0)
    scroll = xrd.findChild(QScrollArea, "SettingsPanel")
    assert scroll is not None and scroll.width() >= 300
    assert window.width() <= 940 and window.height() <= 490
    window.undo_history.reset(clean=True)
