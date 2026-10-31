from __future__ import annotations

import importlib.util

import numpy as np
import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None
    or importlib.util.find_spec("pyqtgraph") is None,
    reason="Qt plotting dependencies are not installed",
)


def test_logarithmic_axis_is_serialized_and_restored(qtbot) -> None:  # type: ignore[no-untyped-def]
    from materials_data_studio.core.project_state import ProjectState
    from materials_data_studio.modules.xrd.module import XrdModule
    from materials_data_studio.modules.xrd.widget import XrdWidget

    source = XrdModule(ProjectState())
    source_widget = source.create_widget()
    assert isinstance(source_widget, XrdWidget)
    qtbot.addWidget(source_widget)
    source_widget.log_y_axis.setChecked(True)
    source_widget.arpls_smoothness.setValue(2.5e6)
    source_widget.arpls_ratio.setValue(0.002)
    source_widget.arpls_iterations.setValue(75)
    source_widget._peak_fitting_state = {
        "selected_dataset_id": "data-id",
        "fit_range": [10.0, 70.0],
    }

    saved = source.serialize_state()

    restored = XrdModule(ProjectState())
    restored_widget = restored.create_widget()
    assert isinstance(restored_widget, XrdWidget)
    qtbot.addWidget(restored_widget)
    restored.restore_state(saved)
    assert restored_widget.log_y_axis.isChecked()
    assert restored_widget.arpls_smoothness.value() == pytest.approx(2.5e6)
    assert restored_widget.arpls_ratio.value() == pytest.approx(0.002)
    assert restored_widget.arpls_iterations.value() == 75
    assert restored_widget.peak_fitting_state()["fit_range"] == [10.0, 70.0]


def _xrd_dataset(name: str):  # type: ignore[no-untyped-def]
    from materials_data_studio.core.models import Dataset, ModuleType

    x = np.linspace(10.0, 80.0, 501)
    baseline = 5.0 + 0.02 * x
    peak = 100.0 * np.exp(-0.5 * ((x - 35.0) / 0.25) ** 2)
    return Dataset(
        name,
        ModuleType.XRD,
        {"x": x, "intensity": baseline + peak},
        metadata={
            "source_axis": "two_theta",
            "wavelength_a": 1.540598,
            "normalization": "none",
            "scale": 1.0,
            "offset": 0.0,
            "background_enabled": False,
        },
    )


def test_bulk_normalization_updates_every_xrd_dataset(qtbot) -> None:  # type: ignore[no-untyped-def]
    from materials_data_studio.core.project_state import ProjectState
    from materials_data_studio.modules.xrd.widget import XrdWidget

    state = ProjectState()
    state.add_dataset(_xrd_dataset("A"))
    state.add_dataset(_xrd_dataset("B"))
    widget = XrdWidget(state)
    qtbot.addWidget(widget)
    widget.normalization.setCurrentIndex(widget.normalization.findData("max_100"))
    widget.apply_normalization_all.click()
    assert {
        dataset.metadata["normalization"] for dataset in state.datasets
    } == {"max_100"}


def test_logarithmic_constant_stacking_uses_multipliers(qtbot) -> None:  # type: ignore[no-untyped-def]
    from materials_data_studio.core.project_state import ProjectState
    from materials_data_studio.modules.xrd.widget import XrdWidget

    state = ProjectState()
    state.add_dataset(_xrd_dataset("A"))
    state.add_dataset(_xrd_dataset("B"))
    widget = XrdWidget(state)
    qtbot.addWidget(widget)
    widget.log_y_axis.setChecked(True)
    widget.stacking.setCurrentIndex(widget.stacking.findData("constant"))
    widget.stacking_offset.setValue(10.0)
    spec = widget.build_plot_spec()
    np.testing.assert_allclose(spec.curves[1].y, spec.curves[0].y * 10.0)
    assert widget.stacking.currentText() == "Constant multiplier"
    assert widget.stack_value_label.text() == "Stack factor"


def test_linear_constant_stacking_remains_additive(qtbot) -> None:  # type: ignore[no-untyped-def]
    from materials_data_studio.core.project_state import ProjectState
    from materials_data_studio.modules.xrd.widget import XrdWidget

    state = ProjectState()
    state.add_dataset(_xrd_dataset("A"))
    state.add_dataset(_xrd_dataset("B"))
    widget = XrdWidget(state)
    qtbot.addWidget(widget)
    widget.stacking.setCurrentIndex(widget.stacking.findData("constant"))
    widget.stacking_offset.setValue(10.0)
    spec = widget.build_plot_spec()
    np.testing.assert_allclose(spec.curves[1].y, spec.curves[0].y + 10.0)
    assert widget.stacking.currentText() == "Constant offset"


def test_dataset_gradient_follows_visual_order_and_updates_icons(qtbot) -> None:  # type: ignore[no-untyped-def]
    from materials_data_studio.core.project_state import ProjectState
    from materials_data_studio.modules.xrd.widget import XrdWidget

    state = ProjectState()
    state.add_dataset(_xrd_dataset("First"))
    state.add_dataset(_xrd_dataset("Last"))
    widget = XrdWidget(state)
    qtbot.addWidget(widget)
    widget.dataset_list.palette_combo.setCurrentText("Blue–Red")
    widget.dataset_list._apply_palette()
    ordered = widget.dataset_list.datasets()
    assert [dataset.plot_style.color for dataset in ordered] == [
        "#2166ac",
        "#b2182b",
    ]
    assert not widget.dataset_list.list_widget.topLevelItem(0).icon(0).isNull()


def test_arpls_estimate_previews_then_apply_hides_background(qtbot) -> None:  # type: ignore[no-untyped-def]
    from materials_data_studio.core.project_state import ProjectState
    from materials_data_studio.modules.xrd.widget import XrdWidget

    state = ProjectState()
    dataset = _xrd_dataset("Pattern")
    state.add_dataset(dataset)
    widget = XrdWidget(state)
    qtbot.addWidget(widget)
    widget._estimate_background()
    qtbot.waitUntil(lambda: dataset.metadata.get("background_method") == "arpls", timeout=10000)
    assert dataset.metadata["background_method"] == "arpls"
    assert dataset.metadata["background_preview"]
    assert [curve.label for curve in widget.current_spec.curves] == [
        "Pattern",
        "Pattern background",
    ]
    widget._set_background_enabled(True)
    assert not dataset.metadata["background_preview"]
    assert [curve.label for curve in widget.current_spec.curves] == ["Pattern"]


def test_cif_result_is_dense_line_only_and_remains_editable(
    qtbot, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    from materials_data_studio.core.project_state import ProjectState
    from materials_data_studio.modules.xrd.cif_backend import CifDocument
    from materials_data_studio.modules.xrd.simulation import SimulatedPattern
    from materials_data_studio.modules.xrd.widget import XrdWidget

    path = tmp_path / "phase.cif"
    text = "data_phase\n_cell_length_a 1\n"
    path.write_text(text)
    document = CifDocument(path, text, text)
    x = np.linspace(5.0, 90.0, 8501)
    pattern = SimulatedPattern(
        np.array([20.0, 40.0]),
        np.array([100.0, 50.0]),
        x,
        np.exp(-0.5 * ((x - 20.0) / 0.1) ** 2) * 100,
    )
    widget = XrdWidget(ProjectState())
    qtbot.addWidget(widget)
    widget._finish_cif_import(
        path,
        document,
        {
            "wavelength_a": 1.540598,
            "two_theta_range": (5.0, 90.0),
            "fwhm_deg": 0.15,
            "profile_step_deg": 0.01,
            "relative_scale": 1.0,
            "vertical_offset": 0.0,
            "target_dataset_id": None,
        },
        pattern,
    )
    dataset = widget.dataset_list.current_dataset()
    assert dataset is not None
    assert len(dataset.raw_data["x"]) == 8501
    assert dataset.plot_style.line_style == "-"
    assert dataset.plot_style.marker == "none"
    assert widget.edit_cif_button.isEnabled()
    assert dataset.metadata["edited_cif"] == text
    dataset.processed_data["background"] = np.ones(8501)
    edited_text = text.replace(" 1\n", " 2\n")
    edited_document = CifDocument(path, text, edited_text)
    edited_x = np.linspace(5.0, 95.0, 9001)
    widget._finish_cif_import(
        path,
        edited_document,
        {
            "wavelength_a": 1.540598,
            "two_theta_range": (5.0, 95.0),
            "fwhm_deg": 0.15,
            "profile_step_deg": 0.01,
            "relative_scale": 1.0,
            "vertical_offset": 0.0,
            "target_dataset_id": dataset.dataset_id,
        },
        SimulatedPattern(
            np.array([25.0]),
            np.array([100.0]),
            edited_x,
            np.exp(-0.5 * ((edited_x - 25.0) / 0.1) ** 2) * 100,
        ),
    )
    assert len(dataset.processed_data["x"]) == 9001
    assert "background" not in dataset.processed_data
    assert dataset.metadata["edited_cif"] == edited_text
