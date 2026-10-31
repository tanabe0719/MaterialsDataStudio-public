"""Synthetic multi-phase selection, dynamic annotations and profile regressions."""
from __future__ import annotations

import copy
from dataclasses import asdict

import numpy as np
import pytest
from PySide6.QtCore import QItemSelectionModel, QPoint, Qt

from materials_data_studio.core.models import Dataset, ModuleType
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.core.session import load_session, save_session
from materials_data_studio.modules.xrd.phase_models import Candidate, match_peaks
from materials_data_studio.modules.xrd.phase_overlays import (
    annotation_data,
    annotation_dataset,
    peak_owners,
    reflections,
)
from materials_data_studio.modules.xrd.phase_search import experimental_signature, overlay_dataset
from materials_data_studio.modules.xrd.phase_store import PhaseStore
from materials_data_studio.modules.xrd.phase_window import PhaseSearchWindow
from materials_data_studio.modules.xrd.widget import XrdWidget


@pytest.fixture
def phase_window(qtbot, tmp_path):
    x = np.linspace(20, 80, 6001)
    y = 20 + sum(height * np.exp(-((x - center) / .12)**2) for center, height in ((30, 100), (40, 75), (50, 30), (60, 50)))
    parent = Dataset("Synthetic mixture", ModuleType.XRD, {"x": x, "intensity": y}, metadata={"source_axis": "two_theta"})
    state = ProjectState(datasets=[parent])
    widget = XrdWidget(state)
    qtbot.addWidget(widget)
    widget.refresh()
    window = PhaseSearchWindow(state, widget, store=PhaseStore(tmp_path / "phases"), refresh_callback=widget.refresh)
    qtbot.addWidget(window)
    window.show()
    window._step1_ready = True
    peaks = [{"position": center, "intensity": height, "included": True} for center, height in ((30, 100), (40, 75), (50, 30), (60, 50))]
    window.peaks = peaks
    results = []
    candidates = []
    settings = window.form.settings()
    for identity, positions, intensity in (("one", [30., 40., 75.], [40., 100., 30.]), ("two", [30., 60., 70.], [90., 100., 60.])):
        candidate = Candidate(identity=identity, source="Local", formula=identity, content_hash=identity)
        candidates.append(candidate)
        results.append({"candidate": candidate.to_dict(), "pattern": {"positions": positions, "intensities": intensity, "d": [], "hkl": []},
                        "settings": asdict(settings), "match": match_peaks(peaks, positions, intensity, settings), "cif": "data_synthetic", "adopted": False})
    window._ranked((candidates, results, []), copy.deepcopy(peaks), window.form.state(), experimental_signature(parent))
    yield window, widget, parent
    window.hide()
    widget.close()


def select_both(window):
    window.candidate_table.selectAll()
    assert len(window.selected_results()) == 2


def test_multiphase_ownership_and_filled_above_peak_markers(phase_window):
    window, _, _ = phase_window
    select_both(window)
    assert peak_owners(window.selected_results()) == {0: 1, 1: 0, 3: 1}
    curves = window.plot._materials_plot_spec.curves
    observed = next(curve for curve in curves if curve.label == "Observed")
    sticks = [curve for curve in curves if curve.label.startswith("Calculated sticks")]
    matched = [curve for curve in curves if curve.label.startswith("Matched")]
    assert len(sticks) == len(matched) == 2
    assert len({curve.style.color for curve in sticks}) == 2
    assert not any(curve.label == "Predicted not observed" for curve in curves)
    for curve in [*matched, next(curve for curve in curves if curve.label == "Unexplained")]:
        assert np.all(curve.y > np.interp(curve.x, observed.x, observed.y))
        assert curve.style.marker_fill_color == curve.style.marker_edge_color == curve.style.color
    owner = next(curve for curve in matched if 30 in curve.x)
    assert owner.style.color == window.result_color(window.results[1])


def test_sticks_share_the_same_normalization_as_marker_ownership(phase_window):
    window, _, _ = phase_window
    first = window.results[0]
    first["pattern"]["intensities"] = [20., 50., 15.]
    for group in first["match"]["groups"]:
        group["intensity"] *= .5
    select_both(window)
    curves = window.plot._materials_plot_spec.curves
    sticks = [curve for curve in curves if curve.label.startswith("Calculated sticks")]
    assert all(np.nanmax(curve.y) == pytest.approx(100 * window.stick_scale) for curve in sticks)


def test_checkbox_is_additive_and_ctrl_selection_syncs(phase_window, qtbot):
    window, _, _ = phase_window
    table = window.candidate_table
    assert len(window.selected_results()) == 1
    second = table.visualRect(table.model().index(1, 0))
    qtbot.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=second.topLeft() + QPoint(9, second.height() // 2))
    assert len(window.selected_results()) == 2
    assert all(table.item(row, 0).checkState() == Qt.CheckState.Checked for row in range(2))
    table.clearSelection()
    flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
    table.selectionModel().select(table.model().index(0, 0), flags)
    table.selectionModel().select(table.model().index(1, 0), flags)
    assert len(window.selected_results()) == 2
    table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
    assert len(window.selected_results()) == 1
    assert any(curve.label == "Predicted not observed" for curve in window.plot._materials_plot_spec.curves)


def test_children_follow_background_scale_visibility_and_reload(phase_window, tmp_path):
    window, widget, parent = phase_window
    select_both(window)
    window.add_annotations("matched")
    window.add_annotations("ticks")
    children = [dataset for dataset in widget.state.datasets if dataset.metadata.get("phase_parent") == parent.dataset_id]
    assert len(children) == 4
    root = widget.dataset_list._items[parent.dataset_id]
    assert root.childCount() == 4
    marker = next(child for child in children if child.metadata["phase_annotation"] == "matched" and child.metadata["phase_identity"] == "Local:two")
    def marker_curve():
        return next(curve for curve in widget.build_plot_spec().curves if curve.label == marker.display_name)
    assert marker_curve().y[0] == pytest.approx(120 * 1.08)
    parent.processed_data["background"] = np.full(len(parent.array("x")), 20.)
    parent.metadata["background_enabled"] = True
    assert marker_curve().y[0] == pytest.approx(100 * 1.08)
    parent.metadata.update(scale=3., offset=10.)
    assert marker_curve().y[0] == pytest.approx(310 * 1.08)
    widget.dataset_list.list_widget.setCurrentItem(widget.dataset_list._items[marker.dataset_id])
    widget.phase_lift.setValue(20.)
    assert marker_curve().y[0] == pytest.approx(310 * 1.2)
    marker.visible = False
    assert not any(curve.label == marker.display_name for curve in widget.build_plot_spec().curves)
    path = tmp_path / "phase-children.mdss"
    save_session(widget.state, path)
    restored = load_session(path)
    saved = next(dataset for dataset in restored.datasets if dataset.dataset_id == marker.dataset_id)
    assert not saved.visible and saved.metadata["marker_lift_percent"] == 20
    assert np.array_equal(parent.raw_data["intensity"], phase_window[2].raw_data["intensity"])


def test_doublet_ticks_are_uniform_and_have_ratio(phase_window):
    window, _, parent = phase_window
    result = copy.deepcopy(window.results[0])
    result["settings"].update(radiation="Cu Kα1/2", alpha2_ratio=.5)
    child = annotation_dataset(parent, result, "#16a34a", "ticks")
    x, y = annotation_data(child, parent.array("x"), parent.array("intensity"), child.array("x"))
    heights = (y.reshape(-1, 3)[:, 1] - y.reshape(-1, 3)[:, 0])
    assert heights[:3] == pytest.approx(np.full(3, heights[0]))
    assert heights[3:] == pytest.approx(heights[:3] * .5)
    assert len(x) == 18
    result["pattern"]["intensities"][1] = 0
    assert len(reflections(result)[0]) == 4


def test_add_profiles_all_selected(phase_window, qtbot):
    window, widget, _ = phase_window
    select_both(window)
    window.add_overlay()
    qtbot.waitUntil(lambda: not window._busy, timeout=10000)
    profiles = [dataset for dataset in widget.state.datasets if dataset.metadata.get("is_simulated_phase")]
    assert len(profiles) == 2
    for result, dataset in zip(window.results, profiles, strict=True):
        assert np.isfinite(dataset.array("intensity")).all()
        assert len(dataset.array("x")) > 1000
        assert dataset.plot_style.color == window.result_color(result)
        assert len(dataset.metadata["stick_two_theta_deg"]) == 3


def test_cached_preview_and_checked_selection_survive_restore(phase_window, monkeypatch):
    window, widget, _ = phase_window
    select_both(window)
    cache = window._preview_cache
    items = list(window.plot._materials_curve_items)
    monkeypatch.setattr('materials_data_studio.modules.xrd.phase_window.experimental_data', lambda *a, **k: pytest.fail("Repeated conversion of unchanged observation"))
    window.scale_sticks(1.1)
    assert window._preview_cache is cache
    assert window.plot._materials_curve_items == items
    saved = window.serialize_state()
    monkeypatch.undo()
    reopened = PhaseSearchWindow(widget.state, widget, saved_state=saved, store=window.service.store)
    assert len(reopened.selected_results()) == 2
    reopened.deleteLater()


def test_profile_shift_and_doublet_preserve_source_arrays(phase_window):
    window, _, _ = phase_window
    result = copy.deepcopy(window.results[0])
    result["settings"].update(radiation="Cu Kα1/2", zero_shift=.2)
    original = copy.deepcopy(result["pattern"])
    dataset = overlay_dataset(result)
    assert result["pattern"] == original
    assert dataset.metadata["simulation_zero_shift_deg"] == .2
    assert dataset.metadata["radiation"] == "Cu Kα1/2"
    assert dataset.array("x")[0] == result["settings"]["lower"]


def test_profile_batch_failure_does_not_add_partial_results(phase_window, monkeypatch, qtbot):
    from materials_data_studio.modules.xrd.simulation import SimulatedPattern
    window, widget, _ = phase_window
    select_both(window)
    for result in window.results:
        result["cif"] = ""
    monkeypatch.setattr(window.service.cod, "cif", lambda *args: "data_synthetic")
    calls = []
    def simulate(*args, **kwargs):
        calls.append(True)
        if len(calls) == 2:
            raise ValueError("Synthetic second-structure failure")
        return SimulatedPattern(np.array([30.]), np.array([100.]))
    monkeypatch.setattr('materials_data_studio.modules.xrd.simulation.simulate_cif', simulate)
    window.add_overlay()
    qtbot.waitUntil(lambda: not window._busy, timeout=10000)
    assert len(calls) == 2
    assert len(widget.state.datasets) == 1
    assert "Synthetic second-structure failure" in window.status.text()


def test_reapply_preserves_style_and_parent_duplicate_remove_keeps_hierarchy(phase_window):
    window, widget, parent = phase_window
    select_both(window)
    window.add_annotations("matched")
    child = widget.state.datasets[1]
    child.plot_style.color = "#112233"
    child.metadata["marker_lift_percent"] = 25.
    window.add_annotations("matched")
    assert len(widget.state.datasets) == 3
    assert child.plot_style.color == "#112233" and child.metadata["marker_lift_percent"] == 25
    tree = widget.dataset_list
    tree.list_widget.setCurrentItem(tree._items[parent.dataset_id])
    tree._duplicate()
    assert len(widget.state.datasets) == 6
    new_parent = next(dataset for dataset in widget.state.datasets if not dataset.metadata.get("phase_annotation") and dataset is not parent)
    assert len([dataset for dataset in widget.state.datasets if dataset.metadata.get("phase_parent") == new_parent.dataset_id]) == 2
    tree.list_widget.setCurrentItem(tree._items[parent.dataset_id])
    tree._remove()
    assert len(widget.state.datasets) == 3
    assert all(dataset.metadata.get("phase_parent", new_parent.dataset_id) == new_parent.dataset_id for dataset in widget.state.datasets)


def test_hiding_stronger_phase_reassigns_shared_marker(phase_window):
    window, widget, _ = phase_window
    select_both(window)
    window.add_annotations("matched")
    first, second = widget.state.datasets[1:]
    curves = {curve.label: curve for curve in widget.build_plot_spec().curves}
    assert 30 not in curves[first.display_name].x and 30 in curves[second.display_name].x
    second.visible = False
    curves = {curve.label: curve for curve in widget.build_plot_spec().curves}
    assert 30 in curves[first.display_name].x


def test_close_releases_large_preview_arrays_and_reopen_restores_results(phase_window):
    window, _, _ = phase_window
    select_both(window)
    window.plot.getViewBox().setRange(xRange=(25, 65), yRange=(0, 130), padding=0)
    window.close()
    assert window._preview_cache is None
    assert not window.plot._materials_plot_spec.curves
    assert len(window.results) == 2
    window.show()
    assert window._preview_cache is not None
    assert len(window.selected_results()) == 2
    assert any(curve.label == "Observed" for curve in window.plot._materials_plot_spec.curves)
    assert not window._step1_ready
    assert window.plot.getViewBox().viewRange()[0] == pytest.approx((25, 65))


def test_tree_preserves_tags_and_sibling_drag_order(phase_window):
    window, widget, parent = phase_window
    select_both(window)
    window.add_annotations("ticks")
    tree = widget.dataset_list
    first, second = widget.state.datasets[1:]
    second.metadata["tags"] = ["synthetic-tag"]
    tree.search.setText("synthetic-tag")
    assert not tree._items[parent.dataset_id].isHidden()
    assert not tree._items[second.dataset_id].isHidden()
    assert tree._items[first.dataset_id].isHidden()
    tree.search.clear()
    root = tree._items[parent.dataset_id]
    moved = root.takeChild(1)
    root.insertChild(0, moved)
    tree._rows_moved()
    assert tree.datasets()[1].dataset_id == second.dataset_id
    root = tree._items[parent.dataset_id]
    moved = root.takeChild(0)
    tree.list_widget.addTopLevelItem(moved)
    tree._rows_moved()  # A drag must not detach an annotation from its parent.
    assert tree._items[second.dataset_id].parent() is tree._items[parent.dataset_id]
