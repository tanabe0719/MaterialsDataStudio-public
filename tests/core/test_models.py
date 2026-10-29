from __future__ import annotations

import numpy as np
import pytest

from materials_data_studio.core.models import Dataset, ModuleType, PlotStyle
from materials_data_studio.core.project_state import ProjectState


def make_dataset(name: str, module: ModuleType = ModuleType.XRD) -> Dataset:
    return Dataset(name, module, {"x": np.arange(3.0), "y": np.arange(3.0)})


def test_raw_arrays_are_owned_and_read_only() -> None:
    source = np.arange(3.0)
    dataset = Dataset("sample", ModuleType.XRD, {"x": source})
    source[0] = 99
    assert dataset.raw_data["x"][0] == 0
    with pytest.raises(ValueError):
        dataset.raw_data["x"][0] = 2


def test_duplicate_has_new_uuid_and_independent_processing() -> None:
    dataset = make_dataset("sample")
    dataset.processed_data["y"] = np.ones(3)
    duplicate = dataset.duplicate()
    duplicate.processed_data["y"][0] = 5
    assert duplicate.dataset_id != dataset.dataset_id
    assert dataset.processed_data["y"][0] == 1


def test_order_visibility_and_removal() -> None:
    state = ProjectState()
    first, second, third = make_dataset("a"), make_dataset("b"), make_dataset("c")
    for item in (first, second, third):
        state.add_dataset(item)
    second.visible = False
    state.reorder([third.dataset_id, first.dataset_id, second.dataset_id])
    assert [item.display_name for item in state.visible_datasets()] == ["c", "a"]
    state.remove_dataset(first.dataset_id)
    assert [item.plot_order for item in state.datasets] == [0, 1]


def test_reorder_rejects_incomplete_order() -> None:
    state = ProjectState([make_dataset("a"), make_dataset("b")])
    with pytest.raises(ValueError):
        state.reorder([state.datasets[0].dataset_id])


def test_plot_style_restores_legacy_integer_pen_style() -> None:
    assert PlotStyle.from_dict({"line_style": 2}).line_style == "--"
    assert PlotStyle.from_dict({"line_style": 3}).line_style == ":"
