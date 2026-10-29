"""Real Qt screenshots of MP supplementation using independent synthetic Si."""
from __future__ import annotations

import argparse
import copy
import os
from pathlib import Path

import numpy as np
from benchmark_reference_search import standards
from manual_fonts import configure_capture_fonts
from PySide6.QtWidgets import QApplication, QTabWidget

from materials_data_studio.core.models import Dataset, ModuleType
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.modules.xrd.mp_database import MPDatabaseManager
from materials_data_studio.modules.xrd.phase_models import SearchQuery
from materials_data_studio.modules.xrd.phase_search import experimental_signature
from materials_data_studio.modules.xrd.phase_store import PhaseStore
from materials_data_studio.modules.xrd.phase_window import PhaseSearchWindow
from materials_data_studio.modules.xrd.qualx_database import ReferenceDatabaseManager
from materials_data_studio.modules.xrd.reference_dialog import ReferenceDatabaseDialog
from materials_data_studio.modules.xrd.widget import XrdWidget
from materials_data_studio.ui.theme import style_sheet


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mp-index", type=Path, required=True)
    parser.add_argument("--cod-database", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    folder = root / ".test-tmp/mp-capture"
    os.environ["MDS_SETTINGS_DIRECTORY"] = str(folder / "settings")
    app = QApplication.instance() or QApplication([])
    configure_capture_fonts(app)
    app.setStyle("Fusion")
    app.setStyleSheet(style_sheet("light"))
    store = PhaseStore(folder / "store")
    MPDatabaseManager(store).register(args.mp_index)
    if args.cod_database:
        ReferenceDatabaseManager(store).register(args.cod_database)
    peaks = [{**p, "origin": "fitted"} for p in standards()["Si"][0]]
    x = np.linspace(10, 90, 8001)
    y = sum(p["intensity"] * np.exp(-.5 * ((x - p["position"]) / .06)**2) for p in peaks)
    dataset = Dataset("Generated Si standard (independent lattice)", ModuleType.XRD, {"x": x, "intensity": y}, metadata={"source_axis": "two_theta", "wavelength_a": 1.540598})
    state = ProjectState(datasets=[dataset])
    parent = XrdWidget(state)
    window = PhaseSearchWindow(state, parent, store=store)
    window.resize(1760, 920)
    window.form.numeric["lower"].setValue(10)
    window.form.fields["required"].setText("Si")
    outcome = window.engine.search(peaks, SearchQuery(required="Si"), window.form.settings(), cod=bool(args.cod_database), user=False, mp=True)
    window.show()
    window._peaks_ready(peaks)
    window._ranked(outcome, copy.deepcopy(peaks), window.form.state(), experimental_signature(dataset))
    window.tabs.setCurrentIndex(1)
    window.status.setText("COD and MP references ranked by MDS Match Score · independent synthetic Si standard · no measured-data accuracy claim")
    app.processEvents()
    assert window.grab().save(str(root / "docs/screenshots/93_xrd_mp_supplement.png"))
    from materials_data_studio.ui.localization import translate_tree
    translate_tree(window, "ja")
    app.processEvents()
    assert window.grab().save(str(root / "docs/screenshots/97_xrd_phase_japanese.png"))
    translate_tree(window, "en")
    dialog = ReferenceDatabaseDialog(window.reference_manager, window)
    dialog.mp_status.setText(dialog.mp_status.text().replace(str(root), "C:\\MaterialsDataStudio"))
    dialog.information.setPlainText(dialog.information.toPlainText().replace(str(root), "C:\\MaterialsDataStudio"))
    dialog.resize(920, 800)
    dialog.show()
    app.processEvents()
    assert dialog.grab().save(str(root / "docs/screenshots/94_xrd_mp_database.png"))
    dialog.close()
    window.close()
    parent.resize(1440, 920)
    parent.show()
    for tabs in parent.findChildren(QTabWidget):
        for index in range(tabs.count()):
            if tabs.tabText(index) == "Analysis":
                tabs.setCurrentIndex(index)
    app.processEvents()
    assert parent.grab().save(str(root / "docs/screenshots/95_xrd_analysis_actions.png"))
    parent.close()


if __name__ == "__main__":
    main()
