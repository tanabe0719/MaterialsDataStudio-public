"""Capture v0.10 workflows using generated data and isolated settings only."""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog

from materials_data_studio.core.models import Dataset, ModuleType
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.core.session import save_session
from materials_data_studio.tools.graph_waves import empty_document, make_wave
from materials_data_studio.tools.graph_window import GraphEditorWindow
from materials_data_studio.tools.pxp_model import PxpGraph, PxpImage
from materials_data_studio.ui.analysis_workbench import AnalysisWorkbench
from materials_data_studio.ui.graph_workflow_dialogs import roi_dialog, share_graphs
from materials_data_studio.ui.import_preview import ImportPreview
from materials_data_studio.ui.localization import set_language
from materials_data_studio.ui.main_window import MainWindow
from materials_data_studio.ui.recovery_center import RecoveryCenter

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/screenshots"


def main():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["QT_SCALE_FACTOR"] = "1"
    with tempfile.TemporaryDirectory(prefix="mds-workflow-manual-") as work:
        os.environ["MDS_SETTINGS_DIRECTORY"] = str(Path(work) / "settings")
        os.environ["MDS_RECOVERY_DIRECTORY"] = str(Path(work) / "recovery")
        app = QApplication.instance() or QApplication([])
        from manual_fonts import configure_capture_fonts
        configure_capture_fonts(app)
        app.setStyle("Fusion")
        x = np.linspace(10, 80, 501)
        datasets = []
        for i in range(3):
            y = 30 + 800 * np.exp(-((x - 30 - i * .15) / .3)**2) + 500 * np.exp(-((x - 55) / .5)**2)
            datasets.append(Dataset(f"Synthetic sample {i+1}", ModuleType.XRD,
                {"x": x, "intensity": y}, metadata={"wavelength_a": 1.540598, "normalization": "none", "scale": 1, "offset": 0, "sample_id": f"Demo-{i+1}", "measurement_conditions": {"temperature_k": 100+100*i}},
                analysis_results={"example": {"center_degree": 30+i*.15, "width_degree": .3, "standard_errors": {"center_degree": .005, "width_degree": .01}, "point_count": 501}}))
        window = MainWindow(ProjectState(datasets))
        window.resize(1440, 900)
        window.show()

        def grab(widget, name):
            widget.show()
            app.processEvents()
            assert widget.grab().save(str(OUTPUT / name), "PNG")

        workbench = AnalysisWorkbench(window)
        workbench.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        grab(workbench, "63_workbench_results.png")
        workbench.tabs.setCurrentIndex(1)
        workbench.capture()
        recipe = workbench.recipe_value()
        recipe["steps"].append({"operation": "background", "parameters": {"smoothness": 1e5, "convergence_ratio": .001, "max_iterations": 50}})
        workbench.recipe.setPlainText(json.dumps(recipe, indent=2))
        grab(workbench, "64_workbench_recipes.png")
        workbench.tabs.setCurrentIndex(2)
        workbench._append_sample(["Demo-1", "Fe2O3", "12.5", "1.0", "0.2", "2.0", "Synthetic example only", '{"temperature_k": 100}'])
        workbench.samples.resizeColumnsToContents()
        grab(workbench, "65_sample_registry.png")
        workbench.hide()
        payload = b"# Generated example only\nangle,intensity,sigma\n" + b"".join(f"{a:.4f},{b:.4f},{np.sqrt(b):.4f}\n".encode() for a, b in zip(x[:30], datasets[0].raw_data["intensity"][:30], strict=True))
        preview = ImportPreview(Path("Synthetic_XRD.csv"), payload, window)
        preview.sigma.setCurrentIndex(preview.sigma.findData(2))
        grab(preview, "66_xrd_import_preview.png")
        preview.hide()
        graph = empty_document()
        wave = make_wave("synthetic_image", np.exp(-np.linspace(-2, 2, 51)[:, None]**2) * np.cos(np.linspace(0, 2, 31)[None, :])**2)
        graph.current.waves = (replace(wave, dimension_scaling=((0., .1), (0., .2)), dimension_units=("mm", "mm"), data_units="a.u."),)
        graph.current.graphs = (PxpGraph("Synthetic map", images=(PxpImage(wave.igor_path),)),)
        viewer = GraphEditorWindow()
        viewer.bind_graph_document(graph)
        viewer.show()

        def capture_dialog(name):
            def execute(dialog):
                grab(dialog, name)
                dialog.reject()
                return QDialog.DialogCode.Rejected
            return execute
        with patch.object(QDialog, "exec", capture_dialog("67_image_roi.png")):
            roi_dialog(viewer)
        with patch.object(QDialog, "exec", capture_dialog("68_sharing_copy.png")):
            share_graphs(viewer)
        recovery_path = Path(work) / "recovery" / "Synthetic.mdss"
        recovery_path.parent.mkdir(parents=True, exist_ok=True)
        save_session(window.state, recovery_path)
        from types import SimpleNamespace
        with patch("materials_data_studio.ui.recovery_center.find_recovery_candidates", return_value=[SimpleNamespace(path=recovery_path)]), patch("materials_data_studio.ui.recovery_center.graph_candidates", return_value=[]):
            recovery = RecoveryCenter(window)
        grab(recovery, "69_recovery_center.png")
        recovery.hide()
        set_language("ja")
        grab(window, "70_japanese_ui.png")
        set_language("en")
        viewer._discard_approved = True
        viewer.close()
        window.undo_history.reset(clean=True)
        window.close()
        print("Captured 8 synthetic workflow screenshots")


if __name__ == "__main__":
    main()
