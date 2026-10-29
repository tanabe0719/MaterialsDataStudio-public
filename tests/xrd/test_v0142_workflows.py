"""XRD step gates, database controls and display-threshold regressions."""
from __future__ import annotations

import copy

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QMessageBox, QPushButton, QWidget

from materials_data_studio.core.models import Dataset, ModuleType
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.modules.xrd.phase_forms import SearchForm
from materials_data_studio.modules.xrd.phase_store import PhaseStore
from materials_data_studio.modules.xrd.phase_window import PhaseSearchWindow
from materials_data_studio.modules.xrd.qualx_database import ReferenceDatabaseManager
from materials_data_studio.modules.xrd.reference_dialog import ReferenceDatabaseDialog
from materials_data_studio.ui.localization import translate_tree


def test_sources_migration_and_japanese_keep_semantics(qtbot):
    form = SearchForm(1.540598)
    qtbot.addWidget(form)
    assert form.source.currentIndex() == 0 and form.source.count() == 4
    assert not form.findChildren(QCheckBox)
    original = form.settings()
    translate_tree(form, "ja")
    assert form.source.currentText() == "COD + MP + ユーザーCIF"
    assert form.settings() == original
    assert form.state()["source"] == 0
    form.restore({"source_format":"offline-v1", "source":0})
    assert form.source.currentIndex() == 3
    form.restore({"source_format":"combined-v2", "source":2})
    assert form.source.currentIndex() == 2
    translate_tree(form, "en")
    assert form.source.itemText(3) == "User CIF"


def test_remove_registrations_require_confirmation(qtbot, monkeypatch, tmp_path):
    store = PhaseStore(tmp_path / "store")
    dialog = ReferenceDatabaseDialog(ReferenceDatabaseManager(store))
    qtbot.addWidget(dialog)
    removed = []
    monkeypatch.setattr(dialog.manager, "remove", lambda: removed.append("cod"))
    monkeypatch.setattr(dialog.mp_manager, "remove", lambda: removed.append("mp"))
    buttons = [b for b in dialog.findChildren(QPushButton) if "Remove" in b.text()]
    assert len(buttons) == 2 and all(b.property("danger") for b in buttons)
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.No)
    for button in buttons:
        button.click()
    assert not removed
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    for button in buttons:
        button.click()
    assert removed == ["cod", "mp"]


def test_sparse_candidates_relax_only_cutoff_and_maximize(qtbot, tmp_path):
    dataset = Dataset("Generated", ModuleType.XRD, {"x":np.linspace(10,90,100), "intensity":np.ones(100)})
    parent = QWidget()
    qtbot.addWidget(parent)
    window = PhaseSearchWindow(ProjectState(datasets=[dataset]), parent, store=PhaseStore(tmp_path / "store"))
    qtbot.addWidget(window)
    assert window.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint
    from materials_data_studio.modules.xrd.phase_models import Candidate
    candidate = Candidate(source="COD", identity="1000000", formula="NaCl")
    result = {"candidate":candidate.to_dict(), "match":{"score":42.34,"assignments":[{}], "components":{}}, "settings":{}, "pattern":{}}
    before = copy.deepcopy(result)
    window._ranked(([candidate],[result],[]), [], window.form.state(), "")
    assert window.form.numeric["min_score"].value() == 42.3
    assert window.display_candidates and result == before
    assert "automatically relaxed" in window.status.text()
