"""Synthetic QualX-format fixtures and offline workflow regressions."""
from __future__ import annotations

import copy
import sqlite3
import zipfile
from pathlib import Path
from threading import Event

import numpy as np
import pytest

from materials_data_studio.core.models import Dataset, ModuleType
from materials_data_studio.core.packaging_smoke import _SMOKE_CIF
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.core.session import load_session, save_session
from materials_data_studio.modules.xrd.cif_backend import CifDocument
from materials_data_studio.modules.xrd.phase_detection import alpha2_position
from materials_data_studio.modules.xrd.phase_models import CalculationSettings, SearchQuery
from materials_data_studio.modules.xrd.phase_reference import ReferenceSearchEngine
from materials_data_studio.modules.xrd.phase_search import PhaseSearchService
from materials_data_studio.modules.xrd.phase_store import PhaseStore
from materials_data_studio.modules.xrd.qualx_database import (
    QualXCODInorganicBackend,
    ReferenceDatabaseManager,
)
from materials_data_studio.modules.xrd.reference_index import readonly
from materials_data_studio.modules.xrd.simulation import simulate_cif


@pytest.fixture
def reference(tmp_path):
    main = tmp_path / "generated.sq"
    pattern = simulate_cif(CifDocument(Path("generated.cif"), _SMOKE_CIF, _SMOKE_CIF), wavelength_a=1.540598, two_theta_range=(10, 90), occupancy_preview=False)
    assert pattern.d_spacing_a is not None
    ds, intensities = pattern.d_spacing_a, pattern.intensity
    with sqlite3.connect(main) as connection:
        connection.executescript("""CREATE TABLE infodb(date TEXT,ncard INTEGER,type TEXT);
            INSERT INTO infodb VALUES('2026-07-02',3,'COD');
            CREATE TABLE id(id INTEGER PRIMARY KEY,chemical_formula TEXT,spacegroup TEXT,name TEXT,mineralname TEXT,nd INTEGER,dvalue BLOB,intensita BLOB);
            CREATE TABLE chemical(id INTEGER,chemical_element TEXT,PRIMARY KEY(id,chemical_element));""")
        for identity, shift in ((1000000, 1.), (1000001, 1.08)):
            connection.execute("INSERT INTO id VALUES(?,?,?,?,?,?,?,?)", (identity, "Cl Na", "F m -3 m", "generated salt", "", len(ds),
                               ",".join(str(d * shift) for d in ds) + ",", ",".join(str(i * 10) for i in intensities) + ","))
            connection.executemany("INSERT INTO chemical VALUES(?,?)", [(identity, e) for e in ("Na", "Cl")])
        connection.execute("INSERT INTO id VALUES(1000002,'Si','F d -3 m','','',0,NULL,NULL)")
        connection.execute("INSERT INTO chemical VALUES(1000002,'Si')")
    with sqlite3.connect(str(main) + ".info") as connection:
        connection.execute("CREATE TABLE info(id TEXT PRIMARY KEY,a REAL,b REAL,c REAL,alpha REAL,beta REAL,gamma REAL,volume REAL,type TEXT,h TEXT,k TEXT,l TEXT)")
        for identity, shift in ((1000000, 1.), (1000001, 1.08)):
            connection.execute("INSERT INTO info VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (str(identity), *(5.6402 * shift for _ in range(3)), 90, 90, 90, (5.6402 * shift)**3, "Cubic", "", "", ""))
    with sqlite3.connect(str(main) + ".infostat") as connection:
        connection.execute("CREATE TABLE stat_type(val TEXT,ids TEXT,n INTEGER)")
    with sqlite3.connect(str(main) + ".search") as connection:
        connection.execute("CREATE TABLE top(id INTEGER PRIMARY KEY,n TEXT,dval TEXT)")
        top = np.argsort(intensities)[::-1][:3]
        for identity, shift in ((1000000, 1.), (1000001, 1.08)):
            connection.execute("INSERT INTO top VALUES(?,?,?)", (identity, "3", ",".join(str(ds[i] * shift) for i in top)))
    store = PhaseStore(tmp_path / "store")
    manager = ReferenceDatabaseManager(store)
    info = manager.register(main)
    peaks = [{"position": float(p), "intensity": float(i), "included": True} for p, i in zip(pattern.two_theta_deg, intensities, strict=True)]
    return store, manager, info, peaks, pattern


def test_register_read_only_schema_and_real_missing_entry_case(reference):
    _store, manager, info, _peaks, _pattern = reference
    assert info["phase_count"] == 3 and info["searchable_phase_count"] == 2
    assert info["unsearchable_phase_ids"] == ["1000002"]
    assert manager.verify()["content_hash"] == info["content_hash"]
    backend = QualXCODInorganicBackend(info)
    try:
        with pytest.raises(sqlite3.OperationalError):
            backend.main.execute("DELETE FROM id")
        assert backend.get_metadata("1000000").space_group_number == 225
        assert backend.get_metadata("1000002").lattice == []
        peaks = backend.get_reference_peaks("1000000")
        assert np.max(peaks["intensities"]) == pytest.approx(100)
        assert np.all(peaks["d"] > 0)
        with pytest.raises(ValueError, match="empty"):
            backend.get_reference_peaks("1000002")
    finally:
        backend.close()
    assert manager.configuration() == info


def test_zip_install_and_unsafe_zip_preserves_registration(reference, tmp_path):
    store, manager, info, _peaks, _pattern = reference
    archive = tmp_path / "generated.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        for file in tmp_path.glob("generated.sq*"):
            output.write(file, "generated/" + file.name)
    installed = manager.import_zip(archive)
    assert Path(installed["path"]).is_relative_to(store.root)
    assert installed["content_hash"] == info["content_hash"]
    original = copy.deepcopy(manager.configuration())
    bad = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(bad, "w") as output:
        for name in ("../bad.sq", "bad.sq.info", "bad.sq.infostat", "bad.sq.search"):
            output.writestr(name, "bad")
    with pytest.raises(ValueError, match="Unsafe"):
        manager.import_zip(bad)
    assert manager.configuration() == original
    cancelled = Event()
    cancelled.set()
    with pytest.raises(InterruptedError):
        manager.register(Path(info["path"]), cancelled)
    assert manager.configuration() == original
    manager.remove()
    assert not manager.configuration() and Path(info["path"]).exists() and Path(installed["path"]).exists()


def test_zip_bilingual_notices_are_allowed_without_extracting_arbitrary_files(reference, tmp_path):
    _store, manager, info, _peaks, _pattern = reference
    archive = tmp_path / "with_notices.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for file in tmp_path.glob("generated.sq*"):
            output.write(file, "generated/" + file.name)
        output.writestr("NOTICE.txt", "Source, attribution, CC BY 4.0")
        output.writestr("NOTICE_JA.txt", "出典と利用条件")
    installed = manager.import_zip(archive)
    assert installed["content_hash"] == info["content_hash"]
    with zipfile.ZipFile(archive, "a") as output:
        output.writestr("unrelated.txt", "must not be extracted")
    before = manager.configuration()
    with pytest.raises(ValueError, match="exactly the four"):
        manager.import_zip(archive)
    assert manager.configuration() == before


def test_oversized_notice_is_rejected_before_registration_changes(reference, tmp_path):
    _store, manager, _info, _peaks, _pattern = reference
    archive = tmp_path / "oversized_notice.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        for file in tmp_path.glob("generated.sq*"):
            output.write(file, file.name)
        output.writestr("NOTICE.txt", "x" * (128 * 1024 + 1))
    before = manager.configuration()
    with pytest.raises(ValueError, match="oversized database notice"):
        manager.import_zip(archive)
    assert manager.configuration() == before


def test_changed_corrupt_missing_and_fallback_top(reference, tmp_path):
    _store, manager, info, _peaks, _pattern = reference
    path = Path(info["path"])
    with sqlite3.connect(str(path) + ".search") as connection:
        connection.execute("DELETE FROM top WHERE id=1000000")
    with pytest.raises(ValueError, match="missing/changed"):
        QualXCODInorganicBackend(info)
    new = manager.register(path)
    assert new["searchable_phase_count"] == 2  # Missing top recovered from stored d/intensity.
    with readonly(Path(new["index_path"])) as connection:
        assert connection.execute("SELECT COUNT(*) FROM peaks WHERE phase_id='1000000'").fetchone()[0] == 3
    Path(str(path) + ".info").write_bytes(b"invalid sqlite" * 100)
    with pytest.raises((ValueError, sqlite3.DatabaseError)):
        manager.verify()
    assert manager.configuration() == new


def test_offline_all_elements_optional_and_merged_user_cif(reference, tmp_path, monkeypatch):
    store, _manager, _info, peaks, _pattern = reference
    service = PhaseSearchService(store)
    service.cod.transport = lambda *_: pytest.fail("Normal reference search must not download a CIF")
    engine = ReferenceSearchEngine(service)
    settings = CalculationSettings(lower=10)
    candidates, results, errors = engine.search(peaks, SearchQuery(), settings, cod=True, user=False)
    assert not errors and candidates and results[0]["candidate"]["identity"] == "1000000"
    assert results[0]["match"]["score"] > 99.99 and not results[0]["cif"]
    assert results[0]["database"]["content_hash"] == _info["content_hash"]
    for query in (SearchQuery(allowed="Na Cl O Ca Mg Si Al Fe Zn Ti"), SearchQuery(required="Na Cl"), SearchQuery(formula="NaCl"), SearchQuery(cod_ids="1000000")):
        _c, found, errors = engine.search(peaks, query, settings, cod=True, user=False)
        assert found and found[0]["candidate"]["identity"] == "1000000" and not errors
    _c, rejected, _e = engine.search(peaks, SearchQuery(excluded="Cl"), settings, cod=True, user=False)
    assert not rejected
    cif = tmp_path / "user.cif"
    cif.write_text(_SMOKE_CIF)
    store.register([cif])
    _c, merged, errors = engine.search(peaks, SearchQuery(), settings)
    assert not errors and {r["candidate"]["source"] for r in merged} == {"COD", "Local"}
    import materials_data_studio.modules.xrd.phase_search as original
    monkeypatch.setattr(original, "simulate_cif", lambda *_a, **_k: pytest.fail("Unchanged user CIF should reuse its pattern"))
    _c, cached, _e = engine.search(peaks, SearchQuery(), settings)
    assert len(cached) == len(merged)
    store.remove({c.identity for c in store.library()})
    _c, removed, _e = engine.search(peaks, SearchQuery(), settings)
    assert all(r["candidate"]["source"] == "COD" for r in removed)


def test_step_gate_buttons_radiation_reopen_and_offline_session(qtbot, reference, tmp_path, monkeypatch):
    from materials_data_studio.modules.xrd.phase_window import PhaseSearchWindow
    from materials_data_studio.modules.xrd.widget import XrdWidget
    store, manager, _info, _peaks, pattern = reference
    x = np.linspace(10, 90, 8001)
    y = 20 + sum(i * np.exp(-.5 * ((x - p) / .08)**2) + .5 * i * np.exp(-.5 * ((x - alpha2_position(p)) / .08)**2)
                 for p, i in zip(pattern.two_theta_deg, pattern.intensity, strict=True))
    datasets = [Dataset(name, ModuleType.XRD, {"x": x, "intensity": y}, metadata={"source_axis": "two_theta"}) for name in ("Generated first", "Generated second")]
    state = ProjectState(datasets=datasets)
    parent = XrdWidget(state)
    qtbot.addWidget(parent)
    window = PhaseSearchWindow(state, parent, store=store)
    qtbot.addWidget(window)
    window.show()
    assert window.form.radiation.itemText(0) == window.form.radiation.currentText() == "Cu Kα1/2"
    assert not window.tabs.isTabEnabled(1) and window.tabs.currentIndex() == 0
    assert window.tabs.widget(1).isAncestorOf(window.search_button)
    assert window.tabs.widget(1).isAncestorOf(window.cancel_button)
    assert window.detect_button.property("primary") and window.search_button.property("primary")
    assert not window.cancel_button.property("danger")
    window.search()
    assert not window._busy and "Complete Step 1" in window.status.text()
    window.form.numeric["prominence"].setValue(.001)
    window.detect()
    assert window.detection_stop_button.isEnabled()
    qtbot.waitUntil(lambda: not window._busy, timeout=30000)
    assert window.tabs.isTabEnabled(1)
    window.tabs.setCurrentIndex(1)
    window.search()
    assert window.cancel_button.isEnabled()  # Its parent page must not disable Stop.
    qtbot.waitUntil(lambda: not window._busy, timeout=30000)
    assert window.results and window.selected_result() is not None
    window.service.cod.transport = lambda *_: _SMOKE_CIF.encode()
    window.add_overlay()
    qtbot.waitUntil(lambda: not window._busy, timeout=30000)
    assert state.datasets[-1].metadata.get("is_simulated_phase")
    assert not state.datasets[-1].metadata.get("is_reference_pattern")
    assert state.datasets[-1].source_bytes == _SMOKE_CIF.encode()
    assert np.isfinite(state.datasets[-1].array("intensity")).all()
    shown = []
    monkeypatch.setattr(window, "_show_cif", lambda result: shown.append(result["cif"]))
    window.service.cod.transport = lambda *_: _SMOKE_CIF.encode()
    window.view_cif()
    qtbot.waitUntil(lambda: not window._busy, timeout=30000)
    selected = window.selected_result()
    assert selected is not None and shown == [_SMOKE_CIF]
    assert selected["cif_provenance"]["content_hash"] != selected["candidate"]["content_hash"]
    from materials_data_studio.modules.xrd.phase_search import overlay_dataset
    from materials_data_studio.modules.xrd.phase_store import digest
    fetched_overlay = overlay_dataset(selected)
    assert fetched_overlay.source_bytes is not None and fetched_overlay.source_hash == digest(fetched_overlay.source_bytes)
    snapshot = window.serialize_state()
    state.module_states["xrd"] = {"phase_search": snapshot}
    path = tmp_path / "reference.mdss"
    save_session(state, path)
    retained = load_session(path)
    assert retained.module_states["xrd"]["phase_search"]["results"][0]["database"]
    window.close()
    window.show()
    assert not window.tabs.isTabEnabled(1) and window.tabs.currentIndex() == 0
    assert not window.add_button.isEnabled()
    window._peaks_ready(window.peaks)
    window.dataset_combo.setCurrentIndex(1)
    assert not window.tabs.isTabEnabled(1) and window.tabs.currentIndex() == 0
    parent.peak_fitting_state = lambda: {"selected_dataset_id": datasets[1].dataset_id, "last_result": {"groups": [
        {"fitted_two_theta_deg": 30., "fitted_area": 50., "fwhm_deg": .2, "enabled": True}]}}
    window.use_fitted()
    assert window.tabs.isTabEnabled(1)
    manager.remove()
    reopened = PhaseSearchWindow(retained, parent, store=store, saved_state=snapshot)
    qtbot.addWidget(reopened)
    assert "unavailable" in reopened.status.text()
    assert any(item.name() == "Calculated sticks" for item in reopened.plot.getPlotItem().listDataItems())
    assert not reopened.tabs.isTabEnabled(1)
