"""Branch ownership, editing transactions, scientific data and persistence."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtWidgets import QDialog, QProgressDialog, QWidget

from materials_data_studio.core.measurement_branches import (
    apply_branches,
    automatic_branches,
    share_sample,
)
from materials_data_studio.core.models import Dataset, ModuleType
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.core.session import load_session, save_session
from materials_data_studio.modules.mpms.parser import parse_mpms_dc
from materials_data_studio.modules.mpms.widget import MpmsWidget
from materials_data_studio.modules.ppms.heat_capacity_parser import parse_heat_capacity_text
from materials_data_studio.modules.ppms.heat_capacity_widget import HeatCapacityWidget
from materials_data_studio.modules.ppms.io import read_ppms_text
from materials_data_studio.modules.ppms.resistivity_parser import parse_resistivity_text
from materials_data_studio.modules.ppms.resistivity_widget import ResistivityWidget
from materials_data_studio.ui.branch_editor import BranchEditor
from materials_data_studio.ui.tasks import blocking_task

DEMO = Path(__file__).resolve().parents[2] / "docs" / "demo_data"


def imported_workspace(qtbot: Any, kind: str):
    state = ProjectState()
    if kind == "mpms":
        widget = MpmsWidget(state)
        path = DEMO / "mpms_mt_demo.dc.dat"
        widget._finish_import(path, parse_mpms_dc(path))
    elif kind == "heat_capacity":
        widget = HeatCapacityWidget(state)
        path = DEMO / "ppms_heat_capacity_demo.dat"
        widget._add_result(path, parse_heat_capacity_text(read_ppms_text(path)))
    else:
        widget = ResistivityWidget(state)
        path = DEMO / "ppms_resistivity_demo.dat"
        widget._add_result(path, parse_resistivity_text(read_ppms_text(path)))
    qtbot.addWidget(widget)
    return state, widget


@pytest.mark.parametrize("kind", ["mpms", "heat_capacity", "resistivity"])
def test_import_tree_owns_independent_branches_and_shares_immutable_rows(qtbot: Any, kind: str):
    state, widget = imported_workspace(qtbot, kind)
    root, *children = state.datasets
    assert root.metadata["measurement_container"]
    assert len(children) >= 2
    assert widget.dataset_list.list_widget.topLevelItemCount() == 1
    assert widget.dataset_list.current_dataset() is children[0]
    assert root not in state.visible_datasets()
    for child in children:
        assert child.metadata["measurement_parent"] == root.dataset_id
        definition = child.metadata["measurement_branch"]
        selection = slice(definition["start"], definition["stop"] + 1)
        for key, raw in root.raw_data.items():
            np.testing.assert_array_equal(child.raw_data[key], raw[selection])
            assert not child.raw_data[key].flags.writeable
            assert np.shares_memory(child.raw_data[key], raw)
    children[0].plot_style.color = "#ff0000"
    children[0].analysis_results["test"] = {"value": 5}
    assert children[1].plot_style.color != "#ff0000"
    assert "test" not in children[1].analysis_results
    tree = widget.dataset_list
    item = tree._items[children[0].dataset_id]
    item.setCheckState(0, Qt.CheckState.Unchecked)
    assert not children[0].visible and children[1].visible
    tree._items[root.dataset_id].setCheckState(0, Qt.CheckState.Unchecked)
    assert not any(child.visible for child in children)
    tree._items[root.dataset_id].setCheckState(0, Qt.CheckState.Checked)
    assert all(child.visible for child in children)


@pytest.mark.parametrize("kind", ["mpms", "heat_capacity", "resistivity"])
def test_branch_manual_editor_is_transactional_and_preserves_unchanged_fits(qtbot: Any, kind: str):
    state, _widget = imported_workspace(qtbot, kind)
    root, first, *others = state.datasets
    first.analysis_results["existing"] = {"value": 42}
    before = copy.deepcopy(root.metadata["branch_definitions"])
    dialog = BranchEditor(state, root)
    qtbot.addWidget(dialog)
    assert dialog.model.rowCount() == len(root.raw_data["temperature_k"])
    assert dialog.model.data(dialog.model.index(0, 0)) is not None
    dialog._cell(0, 0).setText("Renamed")
    dialog.reject()
    assert root.metadata["branch_definitions"] == before
    assert first.display_name != "Renamed"
    dialog.commit()
    assert first.display_name == "Renamed"
    assert first.analysis_results["existing"]["value"] == 42
    assert dialog.result() == QDialog.DialogCode.Accepted
    definitions = copy.deepcopy(root.metadata["branch_definitions"])
    definitions[0]["stop"] = definitions[0]["start"]
    apply_branches(state, root, definitions)
    assert state.datasets[1] is first
    assert not first.analysis_results
    for item in others:
        assert any(value is item for value in state.datasets)
    with pytest.raises(ValueError):
        apply_branches(state, root, [{**definitions[0], "stop": -1}])
    assert root.metadata["branch_definitions"] == definitions


@pytest.mark.parametrize("kind", ["mpms", "heat_capacity"])
def test_sample_information_is_shared_and_invalidates_all_branch_fits(qtbot: Any, kind: str):
    state, widget = imported_workspace(qtbot, kind)
    root, first, *others = state.datasets
    for dataset in state.datasets:
        dataset.analysis_results["old"] = 1
    first.metadata["molar_mass_g_mol"] = 99.5
    share_sample(state, first)
    assert all(dataset.metadata["molar_mass_g_mol"] == 99.5 for dataset in state.datasets)
    assert all(not dataset.analysis_results for dataset in [root, *others])
    # The caller invalidates its own result when editing its sample fields.
    if kind == "mpms":
        widget.molar_mass.setValue(88.5)
        widget._apply_sample_information()
    else:
        widget.molar_mass.setValue(88.5)
        widget._save_sample()
    assert all(dataset.metadata["molar_mass_g_mol"] == 88.5 for dataset in state.datasets)
    assert all(not dataset.analysis_results for dataset in state.datasets)


@pytest.mark.parametrize(
    "kind,field", [("mpms", "fit_t_min"), ("heat_capacity", "low_min"), ("resistivity", "fit_min")]
)
def test_analysis_parameters_and_session_restore_are_branch_local(
    qtbot: Any, tmp_path: Path, kind: str, field: str
):
    state, widget = imported_workspace(qtbot, kind)
    root, first, second, *_rest = state.datasets
    control = getattr(widget, field)
    control.setValue(12.25)
    tree = widget.dataset_list
    tree.list_widget.setCurrentItem(tree._items[second.dataset_id])
    control.setValue(23.5)
    tree.list_widget.setCurrentItem(tree._items[first.dataset_id])
    assert control.value() == 12.25
    first.visible = False
    first.analysis_results["independent"] = {"value": 123}
    tree.list_widget.setCurrentItem(tree._items[second.dataset_id])
    path = tmp_path / "hierarchy.mdss"
    save_session(state, path)
    restored = load_session(path)
    assert len(restored.datasets) == len(state.datasets)
    assert restored.datasets[1].metadata["measurement_parent"] == root.dataset_id
    assert restored.datasets[1].metadata["branch_analysis_controls"][field] == 12.25
    assert not restored.datasets[1].visible
    assert restored.datasets[1].analysis_results["independent"]["value"] == 123
    restored_widget = type(widget)(restored)
    qtbot.addWidget(restored_widget)
    assert restored_widget.dataset_list.current_dataset().dataset_id == second.dataset_id
    assert getattr(restored_widget, field).value() == 23.5


def test_heat_capacity_averaging_can_be_previewed_disabled_and_reapplied(qtbot: Any):
    state, widget = imported_workspace(qtbot, "heat_capacity")
    root, child, *_rest = state.datasets
    definitions = copy.deepcopy(root.metadata["branch_definitions"])
    dialog = BranchEditor(state, root)
    qtbot.addWidget(dialog)
    dialog.table.selectRow(0)
    dialog._cell(0, 5).setCheckState(Qt.CheckState.Unchecked)
    dialog.commit()
    np.testing.assert_array_equal(
        widget._data_array(child, "temperature_k"), child.raw_data["temperature_k"]
    )
    definitions[0]["average_pairs"] = True
    apply_branches(state, root, definitions)
    assert len(widget._data_array(child, "temperature_k")) <= len(child.raw_data["temperature_k"])
    np.testing.assert_array_equal(
        root.raw_data["temperature_k"], dialog.root.raw_data["temperature_k"]
    )


def test_invalid_only_bridge_is_not_registered_in_hierarchy():
    root = Dataset(
        "transport",
        ModuleType.PPMS,
        {
            "temperature_k": np.arange(6.0) + 2,
            "field_oe": np.zeros(6),
            "bridge_1_resistance": np.ones(6),
            "bridge_2_resistance": np.full(6, np.nan),
        },
        metadata={
            "ppms_kind": "resistivity",
            "bridges": {"1": {"available": True}, "2": {"available": True}},
            "bridge_settings": {"1": {"source": "resistance"}, "2": {"source": "resistance"}},
        },
    )
    definitions = automatic_branches(root)
    assert definitions and {item["bridge"] for item in definitions} == {1}


def test_mixed_transport_sweeps_are_not_split_at_every_field_step(qtbot: Any):
    state, widget = imported_workspace(qtbot, "resistivity")
    root = state.datasets[0]
    definitions = root.metadata["branch_definitions"]
    assert {item["mode"] for item in definitions} == {"rho_t", "rho_h"}
    assert len(definitions) < 20
    for bridge in {item["bridge"] for item in definitions}:
        rows = np.concatenate(
            [
                np.arange(item["start"], item["stop"] + 1)
                for item in definitions
                if item["bridge"] == bridge
            ]
        )
        np.testing.assert_array_equal(rows, np.arange(len(root.raw_data["temperature_k"])))
    child = next(
        item
        for item in state.datasets
        if item.metadata.get("measurement_branch", {}).get("mode") == "rho_h"
    )
    widget.dataset_list.list_widget.setCurrentItem(widget.dataset_list._items[child.dataset_id])
    assert widget.plot_type.currentData() == "rho_h"
    assert widget.plot_tabs.currentIndex() == 1


@pytest.mark.parametrize("kind", ["mpms", "heat_capacity", "resistivity"])
def test_manual_add_delete_and_duplicate_preserve_originals(qtbot: Any, kind: str):
    state, widget = imported_workspace(qtbot, kind)
    root, first, *_rest = state.datasets
    original_count = len(state.datasets)
    dialog = BranchEditor(state, root)
    qtbot.addWidget(dialog)
    dialog.add_branch()
    dialog._cell(dialog.table.currentRow(), 0).setText("Manual interval")
    dialog.commit()
    assert len(state.datasets) == original_count + 1
    assert root.metadata["branch_definitions"][-1]["name"] == "Manual interval"
    manual_id = state.datasets[-1].dataset_id
    tree = widget.dataset_list
    tree.refresh()
    tree.list_widget.setCurrentItem(tree._items[manual_id])
    tree._move(-1)
    assert root.metadata["branch_definitions"][-2]["name"] == "Manual interval"
    tree._remove()
    assert len(state.datasets) == original_count
    first.analysis_results["preserved"] = {"value": 8}
    tree.list_widget.setCurrentItem(tree._items[root.dataset_id])
    tree._duplicate()
    assert len(state.datasets) == original_count * 2
    copied = next(
        item
        for item in state.datasets
        if item.metadata.get("measurement_container") and item is not root
    )
    copied_first = next(
        item
        for item in state.datasets
        if item.metadata.get("measurement_parent") == copied.dataset_id
    )
    assert copied_first.analysis_results["preserved"]["value"] == 8
    np.testing.assert_array_equal(
        copied_first.raw_data["temperature_k"], first.raw_data["temperature_k"]
    )
    tree.list_widget.setCurrentItem(tree._items[copied.dataset_id])
    tree._move(-1)
    assert state.datasets[0] is copied
    tree._remove()
    assert len(state.datasets) == original_count and state.datasets[0] is root


@pytest.mark.parametrize("delayed", [False, True])
def test_completed_progress_cannot_reappear_in_next_modal_loop(qtbot: Any, delayed: bool):
    owner = QWidget()
    qtbot.addWidget(owner)
    if delayed:
        from threading import Event

        gate = Event()
        QTimer.singleShot(250, gate.set)

        def delayed_work():
            return gate.wait(2)

        work = delayed_work
    else:

        def immediate_work():
            return True

        work = immediate_work
    assert blocking_task(owner, "Read general text", work)
    assert blocking_task(owner, "Parse text columns", lambda: 42) == 42
    # Keep a nested modal event loop alive beyond the old minimum-duration
    # timer, including the stage that finished before its timer first fired.
    dialog = QDialog(owner)
    QTimer.singleShot(350, dialog.accept)
    dialog.exec()
    assert not owner.findChildren(QProgressDialog)
    loop = QEventLoop()
    QTimer.singleShot(10, loop.quit)
    loop.exec()
    assert not owner.findChildren(QProgressDialog)


@pytest.mark.parametrize("kind", ["mpms", "heat_capacity", "resistivity"])
def test_real_fit_targets_selected_branch_only(qtbot: Any, kind: str):
    state, widget = imported_workspace(qtbot, kind)
    root, child, sibling, *_rest = state.datasets
    if kind == "mpms":
        widget.formula.setText("NaCl")
        widget._calculate_mass()
        widget._apply_sample_information()
        widget._run_fit()
        key = "curie_weiss_fit"
    elif kind == "heat_capacity":
        widget.mass.setValue(2)
        widget.molar_mass.setValue(58.44)
        widget._save_sample()
        widget._run_low_fit()
        key = "low_temperature"
    else:
        widget.display_mode.setCurrentIndex(widget.display_mode.findData("arrhenius"))
        widget._run_fit()
        key = f"arrhenius_bridge_{child.metadata['measurement_branch']['bridge']}"
    qtbot.waitUntil(lambda: bool(child.analysis_results), timeout=15000)
    assert not root.analysis_results and not sibling.analysis_results
    assert key in child.analysis_results
    from threading import Event

    from materials_data_studio.core.analysis_workflows import capture_recipe, run_recipe

    replayed = run_recipe(child, capture_recipe(child), Event())
    operation = {
        "mpms": "curie_weiss",
        "heat_capacity": "low_temperature",
        "resistivity": "arrhenius",
    }[kind]
    assert (
        replayed.analysis_results[f"recipe_{operation}"]["point_count"]
        == child.analysis_results[key]["point_count"]
    )
