"""Independent synthetic structures exercise the MP builder and shared ranking."""
from __future__ import annotations

import copy
import json
import sqlite3
from threading import Event

import numpy as np
import pytest
from pymatgen.core import Lattice, Structure
from PySide6.QtWidgets import QPushButton
from scripts.build_mp_index import build, calculate, json_source

from materials_data_studio.modules.xrd.mp_database import MaterialsProjectBackend, MPDatabaseManager
from materials_data_studio.modules.xrd.phase_models import (
    CalculationSettings,
    Candidate,
    SearchQuery,
)
from materials_data_studio.modules.xrd.phase_reference import (
    ReferenceSearchEngine,
    annotate_results,
)
from materials_data_studio.modules.xrd.phase_search import PhaseSearchService
from materials_data_studio.modules.xrd.phase_store import PhaseStore


def record(identity="mp-test-silicon", lattice=5.47, **changes):
    structure = Structure.from_spacegroup(227, Lattice.cubic(lattice), ["Si"], [[0, 0, 0]])
    return {"material_id": identity, "structure": structure.as_dict(), "formula_pretty": "Si",
            "builder_meta": {"license": "BY-C", "batch_id": "test-fixture", "database_version": "2026.09.28"},
            "symmetry": {"symbol": "Fd-3m", "number": 227, "crystal_system": "cubic"},
            "energy_above_hull": 0., "theoretical": False, **changes}


@pytest.fixture
def mp_index(tmp_path):
    path = tmp_path / "mp.sqlite"
    rows = [record(), record("mp-test-other", 6.7), record("mp-test-gnome", builder_meta={"license": "BY-NC", "batch_id": "gnome_r2scan_statics", "database_version": "2026.09.28"})]
    source = {"fixture": "synthetic; not actual MP IDs"}
    build(path, source, lambda: iter(rows), "2026-09-28", workers=1)
    store = PhaseStore(tmp_path / "store")
    manager = MPDatabaseManager(store)
    info = manager.register(path)
    return path, store, manager, info, rows, source


def test_mp_builder_resume_license_and_offline_metadata(mp_index):
    path, store, manager, info, rows, source = mp_index
    assert info["included"] == 2 and info["excluded"] == 1 and info["failed"] == 0
    original = path.read_bytes()
    manifest = build(path, source, lambda: iter(rows), "2026-09-28", workers=1)
    assert manifest["included"] == 2
    with pytest.raises(ValueError, match="identical"):
        build(path, {"different": True}, lambda: iter(rows), "2026-09-28", workers=1)
    assert path.read_bytes() != original  # elapsed/checkpoint changes are recorded
    info = manager.verify()
    backend = MaterialsProjectBackend(info, Event())
    try:
        assert backend.get_metadata("mp-test-silicon").revision == "2026-09-28"
        assert backend.details("mp-test-silicon")["energy_above_hull"] == 0
        assert "Si" in backend.cif("mp-test-silicon")
        assert backend.get_database_info()["phase_count"] == 2
        with pytest.raises(ValueError, match="not found"):
            backend.get_reference_peaks("mp-test-absent")
    finally:
        backend.close()
    manager.remove()
    assert path.is_file() and not manager.configuration()
    service = PhaseSearchService(store)
    with pytest.raises(ValueError, match="unavailable"):
        ReferenceSearchEngine(service).search([{"position": 28.4, "intensity": 100}], SearchQuery(), CalculationSettings(), cod=False, user=False, mp=True)


def test_mp_matching_scale_filters_grouping_and_cancellation(mp_index):
    _path, store, _manager, info, _rows, _source = mp_index
    backend = MaterialsProjectBackend(info, Event())
    reference = backend.get_reference_peaks("mp-test-silicon")
    backend.close()
    ds = np.array(reference["d"]) * (5.431 / 5.47)
    x = np.rad2deg(2 * np.arcsin(1.540598 / (2 * ds)))
    peaks = [{"position": float(p), "intensity": float(i)} for p, i in zip(x, reference["intensities"], strict=True) if 10 < p < 90]
    engine = ReferenceSearchEngine(PhaseSearchService(store))
    settings = CalculationSettings(lower=10, upper=90, tolerance=.1)
    _, results, errors = engine.search(peaks, SearchQuery(), settings, cod=False, user=False, mp=True)
    assert not errors and results[0]["candidate"]["identity"] == "mp-test-silicon"
    assert results[0]["match"]["score"] > 95
    assert results[0]["lattice_scale"] == pytest.approx(5.431 / 5.47, abs=.0001)
    assert results[0]["scale_penalty"] > 0 and results[0]["settings"]["zero_shift"] == 0
    assert results[0]["source_rank"] == 1 and results[0]["mp_details"]["origin"] == "DFT-relaxed"
    assert results[0]["database"]["calculator"] == info["calculator"]
    assert results[0]["database"]["format"] == info["format"]
    _, filtered, _ = engine.search(peaks, SearchQuery(excluded="Si"), settings, cod=False, user=False, mp=True)
    assert not filtered
    _, exact, _ = engine.search(peaks, SearchQuery(formula="Si2"), settings, cod=False, user=False, mp=True)
    assert exact and exact[0]["candidate"]["identity"] == "mp-test-silicon"
    _, absent, _ = engine.search(peaks, SearchQuery(formula="NaCl"), settings, cod=False, user=False, mp=True)
    assert not absent
    with pytest.raises(ValueError, match="scale"):
        engine.search(peaks, SearchQuery(), settings, mp_scale_limit=.04)
    stop = Event()
    stop.set()
    with pytest.raises((InterruptedError, sqlite3.OperationalError)):
        engine.search(peaks, SearchQuery(), settings, cod=False, user=False, mp=True, stop=stop)
    same = copy.deepcopy(results[0])
    same["candidate"].update(source="COD", identity="1000000")
    merged = [same, results[0]]
    annotate_results(merged)
    assert merged[0]["possible_group"] == merged[1]["possible_group"] != ""
    different = copy.deepcopy(same)
    different["candidate"]["space_group_number"] = 225
    annotate_results([same, different])
    assert not same["possible_group"] and not different["possible_group"]


def test_builder_rejects_unknown_licenses_versions_and_bad_structures(tmp_path):
    r = record()
    for meta in ({}, {"license": "BY-NC"}, {"license": "BY-C", "database_version": "2025.01.01"}):
        assert calculate({**r, "builder_meta": meta}, "2026-09-28", 0)[1] == "excluded"
    assert calculate({**r, "deprecated": True}, "2026-09-28", 0)[1] == "excluded"
    assert calculate(r, "2026-09-28", 1)[1] == "excluded"
    assert calculate({**r, "structure": {"sites": []}}, "2026-09-28", 0)[1] == "failed"
    path = tmp_path / "source.jsonl"
    path.write_text(json.dumps(r) + "\n", encoding="utf-8")
    source, records = json_source(path)
    assert len(source["sha256"]) == 64 and next(iter(records()))["material_id"] == r["material_id"]


def test_batch_xrd_matches_standard_model_for_disorder_and_low_symmetry():
    from pymatgen.analysis.diffraction.xrd import XRDCalculator
    from scripts.mp_diffraction import batch_pattern
    structures = [Structure.from_dict(record()["structure"]),
                  Structure.from_spacegroup(186, Lattice.hexagonal(3.2495, 5.2069), ["Zn", "O"], [[1/3, 2/3, 0], [1/3, 2/3, .3826]]),
                  Structure(Lattice.monoclinic(5.1, 6.2, 7.3, 103), [{"Na": .6, "K": .4}, "Cl", "O"], [[.13, .27, .42], [.49, .03, .82], [.64, .75, .36]])]
    for structure in structures:
        standard = XRDCalculator(wavelength=1.540598).get_pattern(structure, two_theta_range=(1, 150))
        batch = batch_pattern(structure)
        np.testing.assert_allclose(batch.x, standard.x, atol=1e-9, rtol=0)
        np.testing.assert_allclose(batch.y, standard.y, atol=1e-8, rtol=0)
        np.testing.assert_allclose(batch.d_hkls, standard.d_hkls, atol=1e-9, rtol=0)
        assert batch.hkls == standard.hkls
    assert calculate(record(), "2026-09-28", 0, "batch")[1] == "included"


def test_mp_changed_index_and_invalid_manifest(mp_index):
    path, _store, manager, info, _rows, _source = mp_index
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="changed"):
        MaterialsProjectBackend(info, Event())
    bad = path.with_name("bad.sqlite")
    with sqlite3.connect(bad) as connection:
        connection.execute("CREATE TABLE metadata(key TEXT,value TEXT)")
        connection.execute("INSERT INTO metadata VALUES('manifest',?)", (json.dumps({"format": "other"}),))
    with pytest.raises(ValueError, match="format"):
        manager.register(bad)


@pytest.mark.parametrize("binary", [False, True])
def test_compact_preserves_plain_and_compressed_patterns(mp_index, tmp_path, binary):
    import zlib

    from scripts.compact_mp_index import compact

    from materials_data_studio.modules.xrd.mp_database import decode_json
    path, _store, manager, info, _rows, _source = mp_index
    backend = MaterialsProjectBackend(info, Event())
    before = backend.get_reference_peaks("mp-test-silicon")
    backend.close()
    with sqlite3.connect(path) as connection:
        encoded = connection.execute("SELECT pattern FROM cards WHERE phase_id='mp-test-silicon'").fetchone()[0]
        connection.execute("UPDATE cards SET pattern=? WHERE phase_id='mp-test-silicon'", (zlib.decompress(encoded).decode(),))
    connection.close()
    output = tmp_path / "compact.sqlite"
    compact(path, output, workers=2, binary=binary)
    new_info = manager.register(output)
    backend = MaterialsProjectBackend(new_info, Event())
    try:
        assert backend.get_reference_peaks("mp-test-silicon") == before
        assert "Si" in backend.cif("mp-test-silicon")
        assert new_info["included"] == info["included"]
        assert new_info["pattern_encoding"] == ("float64-shuffle-hkl-varint-zlib-v2" if binary else "zlib-json-v1")
        assert backend.details("mp-test-silicon")["license"] == "CC BY 4.0"
        np.testing.assert_array_equal(backend.get_search_peaks("mp-test-silicon")["d"], before["d"])
        if binary:
            from scripts.package_mp_index import package
            packed = package(output, tmp_path / "distribution.zip")
            assert packed["format"] == "mds-mp-reference-2"
            assert packed["database_sha256"] == new_info["content_hash"]
    finally:
        backend.close()
    with pytest.raises(ValueError, match="new output"):
        compact(path, output)
    with pytest.raises(ValueError, match="size limit"):
        decode_json(zlib.compress(b"1234567"), limit=4)


def test_mp_window_supplement_fallback_stop_and_local_structure(qtbot, monkeypatch, mp_index):
    from materials_data_studio.core.models import Dataset, ModuleType
    from materials_data_studio.core.project_state import ProjectState
    from materials_data_studio.modules.xrd.phase_window import PhaseSearchWindow
    from materials_data_studio.modules.xrd.widget import XrdWidget
    _path, store, _manager, _info, _rows, _source = mp_index
    _, _, (_card, pattern, _structure, _details) = calculate(record(), "2026-09-28", 0)
    positions = np.rad2deg(2 * np.arcsin(1.540598 / (2 * np.array(pattern["d"]))))
    peaks = [{"position": float(x), "intensity": float(y), "origin": "fitted"} for x, y in zip(positions, pattern["intensities"], strict=True) if 10 < x < 90]
    dataset = Dataset("Synthetic Si", ModuleType.XRD, {"x": np.linspace(10, 90, 801), "intensity": np.ones(801)}, metadata={"source_axis": "two_theta", "wavelength_a": 1.540598})
    state = ProjectState(datasets=[dataset])
    parent = XrdWidget(state)
    window = PhaseSearchWindow(state, parent, store=store)
    parent._phase_search_window = window
    refreshes = []
    window.refresh_callback = lambda: refreshes.append(True)
    qtbot.addWidget(parent)
    qtbot.addWidget(window)
    window.form.numeric["lower"].setValue(10)
    window._peaks_ready(peaks)
    assert window.form.source.currentIndex() == 0
    assert not hasattr(window.form, "mp_fallback")
    assert not any("Search Materials Project" in b.text() for b in window.findChildren(QPushButton))
    assert not refreshes
    window.form.source.setCurrentIndex(2)
    window.search()
    assert window.cancel_button.property("danger") and window.cancel_button.isEnabled()
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)
    assert not window.cancel_button.property("danger") and not window.cancel_button.isEnabled()
    assert window.results and window.results[0]["candidate"]["source"] == "Materials Project"
    assert not refreshes
    assert not any(c.label.startswith("Phase search:") for c in parent.build_plot_spec().curves)
    original = copy.deepcopy(window.results[0])
    cod = copy.deepcopy(original)
    cod["candidate"].update(source="COD", identity="1000000")
    window.results.append(cod)
    window.form.source.setCurrentIndex(2)
    window.search()
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)
    assert {r["candidate"]["source"] for r in window.results} == {"Materials Project"}
    calls = []
    def search(_peaks, _query, _settings, **options):
        calls.append(options)
        return ([], [copy.deepcopy(original)] if options.get("mp") else [], [])
    monkeypatch.setattr(window.engine, "search", search)
    window.form.source.setCurrentIndex(0)
    window.search()
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)
    assert len(calls) == 1 and calls[0]["cod"] and calls[0]["mp"] and calls[0]["user"]
    window._ranked(([], [copy.deepcopy(original)], []), peaks, window.form.state(), window.matched_signature)
    shown = []
    monkeypatch.setattr(window, "_show_cif", shown.append)
    window.candidate_table.selectRow(0)
    window.view_cif()
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)
    assert shown and "Si" in shown[0]["cif"]
    assert not window.results[0]["cif"]  # no redundant structure embedded into the session
    saved = window.serialize_state()
    assert saved["results"][0]["mp_details"]["license"] == "CC BY 4.0"
    window.close()
    parent.close()


def test_metadata_filters_equal_original_predicate_and_rebuild_corrupt_cache(mp_index):
    from pathlib import Path

    from materials_data_studio.modules.xrd.reference_index import retrieve

    _path, _store, _manager, info, _rows, _source = mp_index
    backend = MaterialsProjectBackend(info, Event())
    settings = CalculationSettings(lower=10)
    peaks = [{"position": 28.4, "intensity": 100}, {"position": 47.3, "intensity": 60}]
    try:
        ranked = retrieve(backend.connection, peaks, settings, Event(), scale_limit=.01)
        for query in (SearchQuery(), SearchQuery(formula="Si2"), SearchQuery(allowed="Si O"), SearchQuery(required="O"),
                      SearchQuery(excluded="Si"), SearchQuery(space_group="F d -3 m"), SearchQuery(space_group="227"),
                      SearchQuery(crystal_system="CUBIC"), SearchQuery(volume_min=200), SearchQuery(volume_max=200), SearchQuery(allowed="O")):
            expected = [key for key, _ in ranked if query.accepts(backend.get_metadata(key))]
            actual = backend.retrieve_candidates(peaks, query, settings, Event(), 50, scale_limit=.01)
            assert [c.identity for c in actual] == expected
        Path(info["metadata_path"]).write_bytes(b"disposable cache corruption")
        assert backend.retrieve_candidates(peaks, SearchQuery(), settings, Event(), 50, scale_limit=.01)
    finally:
        backend.close()


def test_split_distribution_roundtrip_missing_mixed_corrupt_cancel(mp_index, tmp_path):
    import zipfile

    from scripts.package_reference_distributions import package_split

    path, _store, _manager, info, _rows, _source = mp_index
    parts = package_split(path, tmp_path / "parts")
    manager = MPDatabaseManager(PhaseStore(tmp_path / "installed"))
    installed = manager.import_archive(parts[1])  # Either part can be selected.
    assert installed["content_hash"] == info["content_hash"]
    assert installed["phase_count"] == info["phase_count"]
    original = manager.config.read_bytes()
    missing = parts[1].with_suffix(".missing")
    parts[1].rename(missing)
    with pytest.raises(ValueError, match="both MP ZIP"):
        manager.import_archive(parts[0])
    missing.rename(parts[1])
    stop = Event()
    stop.set()
    with pytest.raises(InterruptedError):
        manager.import_archive(parts[0], stop)
    with zipfile.ZipFile(parts[1]) as archive:
        entries = {i.filename: archive.read(i) for i in archive.infolist()}
    manifest = json.loads(entries["manifest.json"])
    manifest["snapshot"] = "different"
    with zipfile.ZipFile(parts[1], "w") as archive:
        for name, value in entries.items():
            archive.writestr(name, json.dumps(manifest) if name == "manifest.json" else value)
    with pytest.raises(ValueError, match="different distributions"):
        manager.import_archive(parts[0])
    with zipfile.ZipFile(parts[1], "w") as archive:
        for name, value in entries.items():
            archive.writestr(name, b"x" * len(value) if name.startswith("database.part") else value)
    with pytest.raises(ValueError, match="SHA-256"):
        manager.import_archive(parts[0])
    assert manager.config.read_bytes() == original


def test_combined_search_scores_all_sources_and_keeps_missing_optional(mp_index, monkeypatch):
    from materials_data_studio.modules.xrd import phase_reference

    _path, store, _manager, _info, _rows, _source = mp_index
    settings = CalculationSettings(lower=10)
    _, _, (card, pattern, _structure, _details) = calculate(record(), "2026-09-28", 0)
    positions = np.rad2deg(2 * np.arcsin(settings.wavelength / (2 * np.array(pattern["d"]))))
    peaks = [{"position":float(x), "intensity":float(y)} for x,y in zip(positions,pattern["intensities"],strict=True) if 10 < x < 90]
    class CODBackend:
        def __init__(self, *_):
            pass
        def get_database_info(self):
            return {"source":"COD"}
        def retrieve_candidates(self, *_):
            return [Candidate(**{**card, "source":"COD", "identity":"1000000"})]
        def get_reference_peaks(self, _):
            return pattern
        def close(self):
            pass
    monkeypatch.setattr(phase_reference, "QualXCODInorganicBackend", CODBackend)
    monkeypatch.setattr(phase_reference.ReferenceDatabaseManager, "configuration", lambda _self: {"synthetic": True})
    engine = ReferenceSearchEngine(PhaseSearchService(store))
    _, results, errors = engine.search(peaks, SearchQuery(), settings, cod=True, mp=True, user=False)
    assert not errors and {r["candidate"]["source"] for r in results} == {"COD", "Materials Project"}
    assert [r["match"]["score"] for r in results] == sorted((r["match"]["score"] for r in results), reverse=True)
    MPDatabaseManager(store).remove()
    _, results, errors = engine.search(peaks, SearchQuery(), settings, cod=True, mp=True, user=False)
    assert results and all(r["candidate"]["source"] == "COD" for r in results)
    assert any("Materials Project unavailable" in error for error in errors)
