"""Capture the real XRD phase-search UI using generated structures only."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import numpy as np
from PySide6.QtWidgets import QApplication

from materials_data_studio.core.models import Dataset, ModuleType
from materials_data_studio.core.packaging_smoke import _SMOKE_CIF
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.modules.xrd.cif_backend import CifDocument
from materials_data_studio.modules.xrd.phase_detection import (
    DetectionSettings,
    alpha2_position,
    analyze_peaks,
)
from materials_data_studio.modules.xrd.phase_models import SearchQuery
from materials_data_studio.modules.xrd.phase_search import experimental_signature
from materials_data_studio.modules.xrd.phase_store import PhaseStore
from materials_data_studio.modules.xrd.phase_window import PhaseSearchWindow
from materials_data_studio.modules.xrd.qualx_database import ReferenceDatabaseManager
from materials_data_studio.modules.xrd.reference_dialog import ReferenceDatabaseDialog
from materials_data_studio.modules.xrd.simulation import simulate_cif
from materials_data_studio.modules.xrd.widget import XrdWidget
from materials_data_studio.ui.theme import style_sheet

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference-db", type=Path)
    arguments = parser.parse_args()
    os.environ.setdefault("QT_QPA_PLATFORM", "windows" if os.name == "nt" else "offscreen")
    root = ROOT / ".test-tmp/phase-capture"
    root.mkdir(parents=True, exist_ok=True)
    os.environ["MDS_SETTINGS_DIRECTORY"] = str(root / "settings")
    os.environ["MDS_RECOVERY_DIRECTORY"] = str(root / "recovery")
    app = QApplication.instance() or QApplication([])
    from manual_fonts import configure_capture_fonts
    configure_capture_fonts(app)
    app.setStyle("Fusion")
    app.setStyleSheet(style_sheet("light"))
    store = PhaseStore(root / "library")
    paths = []
    for name, a in (("Generated_NaCl", "5.6402"), ("Generated_comparison", "6.2402")):
        path = root / (name + ".cif")
        path.write_text(_SMOKE_CIF.replace("5.6402", a), encoding="utf-8")
        paths.append(path)
    store.register(paths)
    simulated = simulate_cif(CifDocument(paths[0], _SMOKE_CIF, _SMOKE_CIF), wavelength_a=1.540598, occupancy_preview=False)
    x = np.linspace(5, 90, 8501)
    y = sum(i * np.exp(-0.5 * ((x - p) / .08) ** 2) for p, i in zip(simulated.two_theta_deg, simulated.intensity, strict=True))
    y += 18 * np.exp(-0.5 * ((x - 41.234) / .08) ** 2)
    y += sum(.5 * i * np.exp(-.5 * ((x - alpha2_position(p)) / .08) ** 2) for p, i in zip(simulated.two_theta_deg, simulated.intensity, strict=True))
    y += 30 + .1 * x + np.random.default_rng(42).normal(0, .3, len(x))
    dataset = Dataset("Generated NaCl + extra peak", ModuleType.XRD, {"x": x, "intensity": y}, metadata={"source_axis": "two_theta", "wavelength_a": 1.540598})
    state = ProjectState(datasets=[dataset])
    parent = XrdWidget(state)
    window = PhaseSearchWindow(state, parent, store=store)
    parent._phase_search_window = window
    window.form.fields["required"].setText("Na Cl")
    window.form.source.setCurrentIndex(1 if arguments.reference_db else 0)
    if arguments.reference_db:
        window.reference_manager.register(arguments.reference_db)
    window.form.radiation.setCurrentText("Cu Kα1/2")
    window.analysis = {**analyze_peaks(x, y, DetectionSettings(doublet=True)), "signature": experimental_signature(dataset), "wavelength": 1.540598}
    window.peaks = window.analysis["peaks"]
    window.candidates, window.results, errors = window.engine.search(window.peaks, SearchQuery(required="Na Cl"), window.form.settings(),
                                                                    cod=arguments.reference_db is not None, user=arguments.reference_db is None)
    assert not errors and window.results
    window.matched_peaks = [dict(p) for p in window.peaks]
    window.matched_signature = experimental_signature(dataset)
    window._candidate_rows()
    window.candidate_table.selectRow(0)
    window.resize(1440, 920)
    window.show()
    window._peaks_ready(window.analysis["peaks"])
    window.tabs.setCurrentIndex(1)
    window.status.setText("Offline reference peaks ranked by MDS Match Score. Generated demonstration with background/noise; score is not probability.")
    app.processEvents()
    assert window.grab().save(str(ROOT / "docs/screenshots/90_xrd_phase_search.png"), "PNG")
    window.tabs.setCurrentIndex(0)
    app.processEvents()
    assert window.grab().save(str(ROOT / "docs/screenshots/91_xrd_phase_peaks.png"), "PNG")
    if arguments.reference_db:
        dialog = ReferenceDatabaseDialog(ReferenceDatabaseManager(store), window)
        dialog.information.setPlainText(dialog.information.toPlainText().replace(str(ROOT), "C:\\MaterialsDataStudio"))
        dialog.resize(920, 800)
        dialog.show()
        app.processEvents()
        assert dialog.grab().save(str(ROOT / "docs/screenshots/92_xrd_reference_database.png"), "PNG")
        dialog.close()
    window.close()
    parent.close()


if __name__ == "__main__":
    main()
