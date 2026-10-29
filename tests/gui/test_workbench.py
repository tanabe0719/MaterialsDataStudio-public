"""Real widgets with synthetic measurements and isolated application settings."""
import json

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

from materials_data_studio.core.exceptions import DataImportError
from materials_data_studio.core.models import Dataset, ModuleType
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.plotting.interactive_export import InteractiveExportDialog
from materials_data_studio.plotting.plot_spec import CurveSpec, PlotSpec
from materials_data_studio.tools.graph_document import save_graph_document
from materials_data_studio.tools.graph_waves import empty_document, make_wave
from materials_data_studio.tools.pxp_model import PxpGraph, PxpTrace
from materials_data_studio.ui.analysis_workbench import AnalysisWorkbench
from materials_data_studio.ui.import_preview import ImportPreview
from materials_data_studio.ui.localization import set_language
from materials_data_studio.ui.main_window import MainWindow
from materials_data_studio.ui.recovery_center import RecoveryCenter


@pytest.fixture
def main(qtbot):
    dataset = Dataset("Synthetic", ModuleType.XRD, {"x": np.arange(10.)+10, "intensity": np.arange(10.)+1}, metadata={"normalization": "none", "wavelength_a": 1.540598, "scale": 1, "offset": 0}, analysis_results={"example": {"gamma_j_mol_k2": .02, "gamma_error": .001, "point_count": 10}})
    window = MainWindow(ProjectState([dataset]))
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    def prepare_close(target):
        target.undo_history.reset(clean=True)
        for graph in tuple(target.tool_manager.open_windows):
            graph._discard_approved = True
    qtbot.addWidget(window, before_close_func=prepare_close)
    window.show()
    yield window
    set_language("en")
    for graph in tuple(window.tool_manager.open_windows):
        graph._discard_approved = True
        graph.close()
    window.undo_history.reset(clean=True)
    window.close()


def test_workbench_capture_results_samples_and_comparison(main, qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(QMessageBox, "warning", lambda *_: pytest.fail("Unexpected warning"))
    dialog = AnalysisWorkbench(main)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    qtbot.addWidget(dialog)
    assert dialog.results.rowCount() >= 3
    dialog.copy_results()
    dialog.capture()
    assert dialog.recipe_value()["module"] == "xrd"
    path = tmp_path / "recipe.mdsr"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args, **_kwargs: (str(path), ""))
    dialog.save_recipe()
    assert path.is_file() and "analysis_recipes" in main.state.axis_settings
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args, **_kwargs: (str(path), ""))
    dialog.load_recipe()
    monkeypatch.setattr(QMessageBox, "information", lambda *_: None)
    dialog.preview_recipe()
    dialog._append_sample(["Sample A", "Fe2O3", "5", "1", ".1", "2", "synthetic", '{"field_oe": 1000}'])
    dialog.samples.setCurrentCell(0, 0)
    dialog.apply_sample()
    assert main.state.datasets[0].metadata["sample_id"] == "Sample A"
    assert main.state.datasets[0].analysis_results
    dialog.parameter.setCurrentText("example / gamma_j_mol_k2")
    dialog.condition.setCurrentText("field_oe")
    dialog.compare()
    assert main.tool_manager.open_windows
    dialog.override_sample.setChecked(True)
    dialog.apply_sample()
    assert not main.state.datasets[0].analysis_results
    assert main.state.datasets[0].metadata["sample_weight_mg"] == 5
    dialog.stale.setChecked(True)
    assert any("Stale" in row["status"] for row in dialog.rows)
    dialog.refresh_history()
    assert "source_sha256" in dialog.history.toPlainText()
    csv_path = tmp_path / "results.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args, **_kwargs: (str(csv_path), ""))
    dialog.export_results()
    assert "standard_error" in csv_path.read_text(encoding="utf-8-sig")
    dialog.close()


def test_workbench_batch_commits_only_valid_inputs(main, qtbot, monkeypatch):
    dialog = AnalysisWorkbench(main)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    qtbot.addWidget(dialog)
    dialog.capture()
    value = dialog.recipe_value()
    value["steps"][0]["parameters"]["normalization"] = "max_100"
    dialog.recipe.setPlainText(json.dumps(value))
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    dialog.run_batch()
    qtbot.waitUntil(lambda: main.state.datasets[0].metadata["normalization"] == "max_100", timeout=10000)
    assert main.undo_stack.canUndo()
    main.undo_action.trigger()
    assert main.state.datasets[0].metadata["normalization"] == "none"
    dialog.close()


def test_import_preview_sigma_units_and_templates(qtbot, monkeypatch, tmp_path):
    dialog = ImportPreview(tmp_path / "synthetic.csv", b"angle,intensity,sigma\n0.1,20,2\n0.2,30,3\n")
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: hasattr(dialog, "rows"))
    dialog.sigma.setCurrentIndex(dialog.sigma.findData(2))
    dialog.axis.setCurrentIndex(dialog.axis.findData("radians"))
    result = dialog.arrays()
    np.testing.assert_allclose(result.x, np.rad2deg([.1, .2]))
    np.testing.assert_allclose(result.intensity_error, [2, 3])
    monkeypatch.setattr(QInputDialog, "getText", lambda *_: ("Instrument template", True))
    dialog.save_template()
    dialog.sigma.setCurrentIndex(0)
    dialog.load_template()
    assert dialog.sigma.currentData() == 2
    dialog.x_column.setCurrentIndex(1)
    with pytest.raises(DataImportError):
        dialog.arrays()
    dialog.close()


def test_figure_template_and_japanese_caption_roundtrip(main, qtbot, monkeypatch):
    spec = PlotSpec("x", "y", [CurveSpec(np.arange(3.), np.arange(3.), "signal")])
    dialog = InteractiveExportDialog(spec)
    qtbot.addWidget(dialog)
    dialog.tabs.setCurrentIndex(2)
    dialog.raster_width.setValue(7)
    dialog.raster_height.setValue(5)
    dialog.raster_dpi.setValue(400)
    dialog.raster_transparent.setChecked(True)
    monkeypatch.setattr(QInputDialog, "getText", lambda *_: ("Paper", True))
    _combo, save, load = dialog.figure_template_controls
    save.click()
    dialog.raster_width.setValue(14)
    load.click()
    assert dialog.raster_width.value() == 7 and dialog.appearance_override is not None
    dialog._validate_and_accept()
    set_language("ja")
    assert main.file_menu.title() == "ファイル"
    assert main.import_action.text() == "データを読み込む"
    assert main.state.datasets[0].display_name == "Synthetic"
    set_language("en")
    assert main.file_menu.title() == "File"
    dialog.close()


def test_direct_graph_transfer_and_recent_files(main, monkeypatch, tmp_path):
    main.edit_current_plot()
    graph = next(iter(main.tool_manager.open_windows))
    assert len(graph.editor.document.render_source().waves) == 2
    path = tmp_path / "synthetic.mdss"
    assert main._save_to(path)
    assert any(action.text() == path.name for action in main.recent_menu.actions())
    graph._discard_approved = True


def test_recovery_center_lists_and_retains_graph_copy(main, monkeypatch, tmp_path):
    directory = tmp_path / "recovery"
    directory.mkdir()
    monkeypatch.setenv("MDS_RECOVERY_DIRECTORY", str(directory))
    path = directory / "graph-recovery-999999-abcdef.mdsg"
    doc = empty_document()
    doc.current.waves = (make_wave("x", np.arange(3.)), make_wave("y", np.arange(3.)))
    doc.current.graphs = (PxpGraph("G", (PxpTrace("y", "root:y", "root:x"),)),)
    save_graph_document(path, doc)
    dialog = RecoveryCenter(main)
    dialog.table.setCurrentCell(0, 0)
    assert dialog.entries[0][1] == path
    dialog.recover()
    assert path.is_file() and main.tool_manager.open_windows
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.No)
    dialog.delete()
    assert path.is_file()
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    dialog.delete()
    assert not path.exists()
    dialog.close()
