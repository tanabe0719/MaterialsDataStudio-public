"""Synthetic offline fixtures, bounded COD protocol and real GUI/session workflow."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from threading import Event
from urllib.parse import parse_qs, urlparse

import numpy as np
import pytest
from PySide6.QtCore import Qt

from materials_data_studio.core.models import Dataset, ModuleType
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.core.session import load_session, save_session
from materials_data_studio.modules.xrd.cif_backend import CifDocument, extract_cif_symmetry
from materials_data_studio.modules.xrd.phase_cod import CODProvider, _CodRedirect
from materials_data_studio.modules.xrd.phase_models import (
    CalculationSettings,
    Candidate,
    SearchQuery,
    detect_peaks,
    match_peaks,
)
from materials_data_studio.modules.xrd.phase_search import (
    PhaseSearchService,
    experimental_data,
    overlay_dataset,
)
from materials_data_studio.modules.xrd.phase_store import PhaseStore, cif_metadata
from materials_data_studio.modules.xrd.simulation import simulate_cif

CIF = """data_synthetic_NaCl
_chemical_formula_sum 'Na Cl'
_symmetry_space_group_name_H-M 'F m -3 m'
_cell_length_a 5.6402
_cell_length_b 5.6402
_cell_length_c 5.6402
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
_atom_site_occupancy
Na1 Na 0 0 0 1
Cl1 Cl 0.5 0.5 0.5 1
"""


@pytest.fixture
def store(tmp_path):
    return PhaseStore(tmp_path / "store")


@pytest.fixture
def local(store, tmp_path):
    paths = []
    for name, a in (("salt", "5.6402"), ("different", "6.2402")):
        path = tmp_path / f"{name}.cif"
        path.write_text(CIF.replace("5.6402", a))
        paths.append(path)
    candidates, errors = store.register(paths)
    assert not errors
    return candidates


@pytest.fixture
def pattern():
    return simulate_cif(CifDocument(Path("synthetic.cif"), CIF, CIF), wavelength_a=1.540598,
                        two_theta_range=(10, 90), occupancy_preview=False)


def peak_list(pattern, shift=0):
    return [{"position": float(x + shift), "intensity": float(y), "included": True, "width": 0.1}
            for x, y in zip(pattern.two_theta_deg, pattern.intensity, strict=True)]


def test_registration_snapshots_deduplicate_and_bad_files(store, local, tmp_path):
    duplicate = tmp_path / "copy.cif"
    duplicate.write_text(CIF)
    broken = tmp_path / "broken.cif"
    broken.write_text("not a cif")
    incomplete = tmp_path / "incomplete.cif"
    incomplete.write_text(CIF.replace("_atom_site_fract_z\n", ""))
    rows, errors = store.register([duplicate, broken, incomplete])
    assert len(rows) == 2 and len(errors) == 2
    original = Path(local[0].filename)
    original.unlink()
    assert store.cif(local[0].content_hash) == CIF
    assert store.local_search(SearchQuery(required="Na Cl"))
    store.remove({local[0].identity})
    assert len(store.library()) == 1 and duplicate.exists()
    assert store.cif(local[0].content_hash) == CIF


@pytest.mark.parametrize("query,expected", [(SearchQuery(required="Na Cl"), True), (SearchQuery(required="Fe"), False),
    (SearchQuery(allowed="Na Cl Fe"), True), (SearchQuery(allowed="Na"), False), (SearchQuery(excluded="Cl"), False),
    (SearchQuery(formula="NaCl"), True), (SearchQuery(formula="SiO2"), False), (SearchQuery(crystal_system="cubic"), True),
    (SearchQuery(space_group="225"), True), (SearchQuery(space_group="P1"), False), (SearchQuery(volume_max=1), False)])
def test_local_filters(store, local, query, expected):
    assert bool(store.local_search(query)) is expected


@pytest.mark.parametrize("query", [SearchQuery(required="fe"), SearchQuery(required="Fe", excluded="Fe"),
    SearchQuery(required="Fe", allowed="Te"), SearchQuery(cod_ids="1%"), SearchQuery(volume_min=3, volume_max=2)])
def test_invalid_queries(query):
    with pytest.raises(ValueError):
        query.validate()


def test_reduced_formula_and_symmetry_expanded_fallback(store):
    from pymatgen.core import Composition
    rutile = """data_synthetic_rutile
_symmetry_space_group_name_H-M 'P 42/m n m'
_cell_length_a 4.594
_cell_length_b 4.594
_cell_length_c 2.959
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
Ti1 Ti 0 0 0
O1 O 0.305 0.305 0
"""
    candidate = cif_metadata(rutile, "synthetic-rutile", "Local")
    assert Composition(candidate.formula).reduced_composition == Composition("TiO2")
    assert SearchQuery(formula="Ti2O4").accepts(candidate)
    assert not SearchQuery(formula="TiO").accepts(candidate)
    candidate.formula = "Ti2 O4"
    assert SearchQuery(formula="TiO2").accepts(candidate)


def test_numerical_single_shift_missing_unrelated_mixed(store, local, pattern):
    service = PhaseSearchService(store)
    settings = CalculationSettings(lower=10, upper=90)
    peaks = peak_list(pattern)
    results, errors = service.calculate(local, peaks, settings)
    assert not errors and results[0]["candidate"]["content_hash"] == local[0].content_hash
    assert results[0]["match"]["score"] == pytest.approx(100)
    assert results[1]["match"]["score"] < 45
    settings.zero_shift = 0.12
    shifted, _ = service.calculate(local[:1], peak_list(pattern, 0.12), settings)
    assert shifted[0]["match"]["score"] == pytest.approx(100)
    settings.zero_shift = 0
    weak = int(np.argmin(pattern.intensity))
    reduced = copy.deepcopy(peaks)
    reduced.pop(weak)
    missing, _ = service.calculate(local[:1], reduced, settings)
    assert missing[0]["match"]["score"] > 85
    assert missing[0]["match"]["missing"]
    mixed = [*peaks, {"position": 41.234, "intensity": 20, "included": True}]
    mixture, _ = service.calculate(local, mixed, settings)
    assert mixture[0]["candidate"]["identity"] == local[0].identity
    assert len(peaks) in mixture[0]["match"]["unmatched"]
    assert mixture[0]["match"]["score"] < 100
    dataset = overlay_dataset(mixture[0])
    assert dataset.metadata["is_simulated_phase"] and dataset.source_bytes == CIF.encode()
    assert dataset.raw_data["x"].size > 3 * len(pattern.two_theta_deg)
    assert np.isfinite(dataset.raw_data["intensity"]).all()
    assert np.all(np.diff(dataset.raw_data["x"]) > 0)
    assert dataset.metadata["stick_two_theta_deg"] == pattern.two_theta_deg.tolist()


def test_assignment_grouping_intensity_penalty_and_exclusions():
    settings = CalculationSettings(lower=10, upper=80, tolerance=0.15, resolution=0.05)
    peaks = [{"position": p, "intensity": 100, "included": True} for p in (20, 20.1)]
    match = match_peaks(peaks, [20], [100], settings)
    assert len(match["assignments"]) == 1 and len(match["unmatched"]) == 1
    grouped = match_peaks(peaks[:1], [19.99, 20.01], [50, 50], settings)
    assert len(grouped["groups"]) == 1 and grouped["score"] == pytest.approx(100)
    missing_strong = match_peaks(peaks[:1], [20, 40], [10, 100], settings)
    assert missing_strong["score"] < 45
    peaks[1]["included"] = False
    assert match_peaks(peaks, [20], [100], settings)["unmatched"] == []


def test_detection_processing_axis_and_fitted_symmetry(pattern):
    from materials_data_studio.modules.xrd.transforms import two_theta_to_d
    x = np.linspace(10, 90, 8001)
    y = sum(i * np.exp(-0.5 * ((x - p) / 0.08) ** 2) for p, i in zip(pattern.two_theta_deg, pattern.intensity, strict=True))
    peaks = detect_peaks(x, y, prominence=0.001)
    assert len(peaks) == len(pattern.two_theta_deg)
    assert np.allclose([p["position"] for p in peaks], pattern.two_theta_deg, atol=0.011)
    data = Dataset("synthetic", ModuleType.XRD, {"x": two_theta_to_d(x, 1.540598), "intensity": y + 10},
                   metadata={"source_axis": "d_spacing", "background_enabled": True}, processed_data={"background": np.full_like(x, 10)})
    actual_x, actual_y = experimental_data(data, 1.540598)
    assert np.allclose(actual_x, x) and np.allclose(actual_y, y)
    text = CIF.replace("_symmetry_space_group_name_H-M 'F m -3 m'", "_space_group_IT_number 225")
    assert extract_cif_symmetry(CifDocument(Path("only-number.cif"), text, text)).number == 225
    without_species = CIF.replace("_atom_site_type_symbol\n", "").replace("Na1 Na ", "Na1 ").replace("Cl1 Cl ", "Cl1 ")
    inferred = cif_metadata(without_species, "synthetic", "Local")
    assert inferred.elements == ["Cl", "Na"] and inferred.site_count == 2


class FakeCOD:
    def __init__(self, *, count=1, rows=None, fail=False):
        self.calls = []
        self.count = count
        self.rows = rows if rows is not None else [{"file": "1000001", "formula": "- Na Cl -", "sg": "F m -3 m", "a": "5.6402", "vol": "179.4", "svnrevision": "5"}]
        self.fail = fail

    def __call__(self, url, stop):
        self.calls.append(url)
        assert url.startswith("https://www.crystallography.net/cod/")
        if self.fail:
            raise TimeoutError("synthetic timeout")
        if stop.is_set():
            raise InterruptedError("stopped")
        params = parse_qs(urlparse(url).query)
        if params.get("format") == ["count"]:
            return str(self.count).encode()
        if params.get("format") == ["json"]:
            return json.dumps(self.rows).encode()
        return CIF.encode()


def test_selected_cod_cif_cache_and_revision(store):
    transport = FakeCOD()
    provider = CODProvider(store, transport)
    candidate = Candidate("1000001", "COD", revision="5")
    assert provider.cif(candidate, Event()) == CIF
    count = len(transport.calls)
    assert provider.cif(candidate, Event()) == CIF and len(transport.calls) == count
    (store.root / "cif" / (candidate.content_hash + ".json")).write_text('"damaged"')
    assert provider.cif(candidate, Event()) == CIF and len(transport.calls) == count + 1
    candidate.revision = "6"
    assert provider.cif(candidate, Event()) == CIF and len(transport.calls) == count + 2
    with pytest.raises(ValueError, match="Invalid COD ID"):
        provider.cif(Candidate("../bad", "COD"), Event())
    with pytest.raises(ValueError, match="HTTPS"):
        _CodRedirect().redirect_request(None, None, 302, "", {}, "http://example.com/")


def test_online_metadata_search_retired_without_requests(store, local):
    transport = FakeCOD()
    provider = CODProvider(store, transport)
    for query in (SearchQuery(), SearchQuery(required="Na"), SearchQuery(allowed="Na Ca Cl O Si Mg Al Zn Fe Ti")):
        with pytest.raises(ValueError, match="offline COD Inorganic"):
            provider.search(query, Event())
    assert not transport.calls
    candidates, errors = PhaseSearchService(store, provider).search(SearchQuery(), online=True)
    assert len(candidates) == 2 and errors and not transport.calls
    stop = Event()
    stop.set()
    with pytest.raises(InterruptedError):
        provider.search(SearchQuery(), stop)


def test_http_response_size_retry_and_cancel(monkeypatch):
    import materials_data_studio.modules.xrd.phase_cod as cod
    class Response:
        def __init__(self):
            self.headers = {"Content-Length": str(cod.MAX_BYTES + 1)}
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
    class Opener:
        def open(self, request, timeout):
            assert timeout == 15
            return Response()
    monkeypatch.setattr(cod, "build_opener", lambda *a: Opener())
    monkeypatch.setattr(cod, "_LAST_REQUEST", 0)
    with pytest.raises(ValueError, match="4 MiB"):
        cod.fetch(cod.BASE + "1000001.cif", Event())
    stopped = Event()
    stopped.set()
    with pytest.raises(InterruptedError):
        cod.fetch(cod.BASE + "1000001.cif", stopped)


def test_calculation_dedup_partial_failure_cache_and_cancel(store, local, pattern, monkeypatch):
    service = PhaseSearchService(store)
    duplicate = copy.deepcopy(local[0])
    duplicate.identity = "local:alias"
    broken = Candidate("broken", "Local", content_hash="0" * 64)
    settings = CalculationSettings(lower=10, upper=90)
    results, errors = service.calculate([local[0], duplicate, broken], peak_list(pattern), settings)
    assert len(results) == 1 and results[0]["candidate"]["aliases"] == ["local:alias"] and errors
    import materials_data_studio.modules.xrd.phase_search as search
    original = search.simulate_cif
    monkeypatch.setattr(search, "simulate_cif", lambda *a, **k: pytest.fail("Cached calculation must not repeat"))
    assert service.calculate(local[:1], peak_list(pattern), settings)[0]
    monkeypatch.setattr(search, "simulate_cif", original)
    settings.wavelength = 1.6
    results, _ = service.calculate(local[:1], peak_list(pattern), settings)
    assert results and len(list((store.root / "patterns").glob("*.json"))) == 2
    stop = Event()
    stop.set()
    with pytest.raises(InterruptedError):
        service.calculate(local[:1], peak_list(pattern), settings, stop)
    with pytest.raises(ValueError, match="1–200"):
        service.calculate(local * 101, [], settings)


def test_gui_workflow_session_and_cancel(qtbot, store, local, pattern, tmp_path):
    from materials_data_studio.modules.xrd.module import XrdModule
    from materials_data_studio.modules.xrd.phase_window import PhaseSearchWindow
    x = np.linspace(10, 90, 8001)
    y = sum(i * np.exp(-0.5 * ((x - p) / 0.08) ** 2) for p, i in zip(pattern.two_theta_deg, pattern.intensity, strict=True))
    experimental = Dataset("Synthetic salt", ModuleType.XRD, {"x": x, "intensity": y}, metadata={"wavelength_a": 1.540598, "source_axis": "two_theta"})
    state = ProjectState(datasets=[experimental])
    module = XrdModule(state)
    parent = module.create_widget()
    qtbot.addWidget(parent)
    window = PhaseSearchWindow(state, parent, store=store, refresh_callback=parent.refresh)
    parent._phase_search_window = window
    qtbot.addWidget(window)
    window.show()
    assert window.form.numeric["lower"].value() == 10
    assert window.form.numeric["upper"].value() == 90
    window.form.numeric["lower"].setValue(10)
    window.form.numeric["prominence"].setValue(0.001)
    window.detect()
    qtbot.waitUntil(lambda: not window._busy, timeout=30000)
    assert len(window.peaks) == len(pattern.two_theta_deg)
    window.form.source.setCurrentIndex(3)
    window.search()
    qtbot.waitUntil(lambda: not window._busy, timeout=30000)
    assert len(window.candidates) == 1  # Peak index rejects the unrelated lattice.
    window.candidate_table.selectAll()
    window.form.numeric["lower"].setValue(5)
    window.calculate()
    assert not window._busy and "within the measured" in window.status.text()
    window.form.numeric["lower"].setValue(10)
    window.calculate()
    qtbot.waitUntil(lambda: not window._busy, timeout=30000)
    assert len(window.results) == 1 and window.add_button.isEnabled()
    assert "predicted not observed" in window.details.toPlainText()
    window.adopt()
    window.add_overlay()
    qtbot.waitUntil(lambda: not window._busy, timeout=30000)
    assert len(state.datasets) == 2 and experimental.raw_data["intensity"].flags.writeable is False
    main_curves = parent.build_plot_spec().curves
    assert not any(c.label.startswith("Phase search:") for c in main_curves)
    assert any(c.label == state.datasets[1].display_name for c in main_curves)
    assert any(c.label == "Matched" for c in window.plot._materials_plot_spec.curves)
    state.module_states["xrd"] = module.serialize_state()
    saved = tmp_path / "phase.mdss"
    save_session(state, saved)
    restored = load_session(saved)
    result = restored.module_states["xrd"]["phase_search"]["results"][0]
    assert result["adopted"] and result["cif"] == CIF
    assert restored.module_states["xrd"]["phase_search"]["dataset_id"] == experimental.dataset_id
    assert result["match"]["assignments"]
    snapshot = window.serialize_state()
    experimental.processed_data["intensity"] = y * 2
    assert not window.current_results()
    experimental.processed_data.clear()
    assert window.current_results()
    reopened = PhaseSearchWindow(restored, parent, store=PhaseStore(tmp_path / "empty-store"), saved_state=snapshot)
    qtbot.addWidget(reopened)
    assert reopened.results[0]["match"]["score"] == window.results[0]["match"]["score"]
    assert not reopened.add_button.isEnabled()  # A new opening starts at Step 1.
    reopened.form.numeric["wavelength"].setValue(1.55)
    reopened.detect()
    qtbot.waitUntil(lambda: not reopened._busy, timeout=30000)
    reopened.calculate()
    qtbot.waitUntil(lambda: not reopened._busy, timeout=30000)
    assert reopened.results and reopened.results[0]["settings"]["wavelength"] == 1.55
    assert reopened.results[0]["cif"] == CIF and not reopened.service.store.library()
    window.peak_table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
    assert not window.add_button.isEnabled()
    before = copy.deepcopy(window.results)
    def stopped(stop):
        stop.wait(1)
        return ([], [])
    window._job("synthetic cancel", stopped, lambda result: window.results.clear())
    window.cancel()
    qtbot.waitUntil(lambda: not window._busy, timeout=5000)
    assert window.results == before
    # Incomplete form edits must still be serializable by automatic Undo/Save.
    window.form.numeric["lower"].setValue(100)
    assert window.serialize_state()["form"]["settings"]["lower"] == 100
    assert state.datasets[0] is experimental


def test_uncalibrated_tof_explains_conversion_without_crashing(qtbot, store):
    from PySide6.QtWidgets import QWidget

    from materials_data_studio.modules.xrd.phase_window import PhaseSearchWindow
    parent = QWidget()
    qtbot.addWidget(parent)
    dataset = Dataset("Synthetic TOF", ModuleType.XRD, {"x": np.arange(10.) + 1, "intensity": np.ones(10)}, metadata={"source_axis": "tof"})
    window = PhaseSearchWindow(ProjectState(datasets=[dataset]), parent, store=store)
    qtbot.addWidget(window)
    window.detect()
    assert not window._busy and "calibration" in window.status.text().lower()


def test_noise_background_and_manual_detection_parameters():
    from materials_data_studio.modules.xrd.phase_detection import DetectionSettings, analyze_peaks
    x = np.linspace(10, 90, 8001)
    baseline = 100 + .4 * x + 25 * np.exp(-((x - 50) / 30) ** 2)
    y = baseline + np.random.default_rng(42).normal(0, 2, len(x))
    for center, height, sigma in ((30, 80, .08), (55, 60, .12), (75, 100, .07)):
        y += height * np.exp(-.5 * ((x - center) / sigma) ** 2)
    y[np.argmin(abs(x - 45))] += 20  # Single-channel spike, not a reflection.
    original = y.copy()
    result = analyze_peaks(x, y, DetectionSettings(snr=6, min_fwhm=.05))
    assert np.array_equal(y, original)
    assert len(result["peaks"]) == 3
    assert np.allclose([p["position"] for p in result["peaks"]], [30, 55, 75], atol=.02)
    assert np.median(abs(np.array(result["baseline"]) - baseline)) < 3
    assert 1.5 < result["noise_sigma"] < 2.5
    stricter = analyze_peaks(x, y, DetectionSettings(noise_sigma=40, snr=6))
    assert not stricter["peaks"]
    stopped = Event()
    stopped.set()
    with pytest.raises(InterruptedError):
        analyze_peaks(x, y, DetectionSettings(), stop=stopped)


def test_cu_doublet_joint_fit_does_not_count_alpha2_twice():
    from materials_data_studio.modules.xrd.phase_detection import (
        DetectionSettings,
        alpha2_position,
        analyze_peaks,
    )
    x = np.linspace(40, 90, 10001)
    y = 20 + .1 * x + np.random.default_rng(14).normal(0, .3, len(x))
    for position, height in ((55, 100), (82, 70)):
        y += height * np.exp(-.5 * ((x - position) / .035) ** 2)
        y += .5 * height * np.exp(-.5 * ((x - alpha2_position(position)) / .035) ** 2)
    settings = DetectionSettings(doublet=True, min_fwhm=.02, max_fwhm=.5, smoothing_sigma=.01, peak_sigma=.035)
    result = analyze_peaks(x, y, settings)
    assert len(result["peaks"]) == 2
    assert np.allclose([p["position"] for p in result["peaks"]], [55, 82], atol=.01)
    assert all(p["sigma"] == .035 and p["alpha2_position"] > p["position"] for p in result["peaks"])


def test_plot_repeated_updates_and_clear_recover(qtbot):
    from materials_data_studio.core.models import PlotStyle
    from materials_data_studio.plotting.plot_spec import CurveSpec, PlotSpec
    from materials_data_studio.plotting.pyqtgraph_backend import create_plot_widget, render_plot
    plot = create_plot_widget()
    qtbot.addWidget(plot)
    spec = PlotSpec("2θ", "Intensity", [CurveSpec(np.arange(10.), np.arange(10.), "Observed", PlotStyle())])
    render_plot(plot, spec)
    for clear in (False, False, True, False):
        if clear:
            plot.clear()
        render_plot(plot, spec)
        assert len(plot.getPlotItem().listDataItems()) == 1
        assert plot.getPlotItem().listDataItems()[0].scene() is not None


def test_phase_ui_order_selection_automatic_ranking_and_scale(qtbot, store, local, pattern):
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication, QPushButton

    from materials_data_studio.modules.xrd.phase_window import PhaseSearchWindow
    from materials_data_studio.modules.xrd.widget import XrdWidget
    x = np.linspace(10, 90, 8001)
    y = sum(i * np.exp(-.5 * ((x - p) / .08) ** 2) for p, i in zip(pattern.two_theta_deg, pattern.intensity, strict=True))
    first = Dataset("Generated first", ModuleType.XRD, {"x": x, "intensity": y}, metadata={"source_axis": "two_theta"})
    second = Dataset("Generated selected", ModuleType.XRD, {"x": x, "intensity": y}, metadata={"source_axis": "two_theta"})
    state = ProjectState(datasets=[first, second])
    parent = XrdWidget(state)
    qtbot.addWidget(parent)
    window = PhaseSearchWindow(state, parent, store=store, selected_dataset_id=second.dataset_id)
    qtbot.addWidget(window)
    assert window.dataset() is second and window.form.source.currentText() == "COD + MP + User CIF"
    assert window.tabs.tabText(0).startswith("Step 1") and window.tabs.tabText(1).startswith("Step 2")
    assert not any("Calculate / match selected" in b.text() for b in window.findChildren(QPushButton))
    assert "Ba" not in window.form.fields["allowed"].placeholderText()
    window._peaks_ready(peak_list(pattern))
    window.form.source.setCurrentIndex(3)
    window.search()
    qtbot.waitUntil(lambda: not window._busy, timeout=30000)
    assert len(window.results) == 1 and window.candidate_table.rowCount() == 1
    selected = window.selected_result()
    assert selected is not None and selected["match"]["score"] > 99
    for name in ("Cu Kα1", "Mo Kα1", "Cu Kα1/2"):
        window.form.radiation.setCurrentText(name)
        assert any(item.name() == "Observed" for item in window.plot.getPlotItem().listDataItems())
    window.candidate_table.selectRow(0)
    previous = window.stick_scale
    window.scale_sticks(1.5)
    assert window.stick_scale == pytest.approx(previous * 1.5)
    curves = window.plot.getPlotItem().listDataItems()
    sticks = next(c for c in curves if c.name() == "Calculated sticks")
    assert np.isnan(sticks.yData[2::3]).all() and np.isfinite(sticks.xData).all()
    assert any(c.name() == "Observed" for c in curves)
    assert window.serialize_state()["stick_scale"] == window.stick_scale
    event = QWheelEvent(QPointF(20, 20), QPointF(20, 20), QPoint(0, 0), QPoint(0, 120), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.ControlModifier, Qt.ScrollPhase.NoScrollPhase, False)
    previous = window.stick_scale
    QApplication.sendEvent(window.plot.viewport(), event)
    assert window.stick_scale > previous and event.isAccepted()
    window.form.numeric["min_score"].setValue(100)
    assert window.candidate_table.rowCount() <= 1
    assert any(c.name() == "Observed" for c in window.plot.getPlotItem().listDataItems())


def test_open_phase_search_tracks_current_xrd_selection(qtbot, tmp_path, monkeypatch):
    import materials_data_studio.modules.xrd.phase_window as phase_window
    from materials_data_studio.modules.xrd.widget import XrdWidget
    monkeypatch.setattr(phase_window, "user_data_directory", lambda: tmp_path)
    datasets = [Dataset(name, ModuleType.XRD, {"x": np.linspace(10, 90, 100), "intensity": np.ones(100)},
                        metadata={"source_axis": "two_theta"}) for name in ("First generated", "Selected generated")]
    parent = XrdWidget(ProjectState(datasets=datasets))
    qtbot.addWidget(parent)
    parent.dataset_list.list_widget.setCurrentItem(parent.dataset_list._items[datasets[1].dataset_id])
    parent._open_phase_search()
    window = parent._phase_search_window
    assert window is not None and window.dataset() is datasets[1]
    window.close()
    parent.dataset_list.list_widget.setCurrentItem(parent.dataset_list._items[datasets[0].dataset_id])
    parent._open_phase_search()
    assert window.dataset() is datasets[0] and not window.peaks


def test_detection_baseline_persistence_and_stale_settings(qtbot, store):
    from materials_data_studio.modules.xrd.phase_window import PhaseSearchWindow
    from materials_data_studio.modules.xrd.widget import XrdWidget
    x = np.linspace(10, 90, 8001)
    y = 100 + 80 * np.exp(-.5 * ((x - 45) / .08) ** 2)
    dataset = Dataset("Generated background", ModuleType.XRD, {"x": x, "intensity": y}, metadata={"source_axis": "two_theta"})
    state = ProjectState(datasets=[dataset])
    parent = XrdWidget(state)
    qtbot.addWidget(parent)
    window = PhaseSearchWindow(state, parent, store=store)
    qtbot.addWidget(window)
    window.detect()
    qtbot.waitUntil(lambda: not window._busy, timeout=30000)
    assert len(window.peaks) == 1 and window.detection_current()
    saved = window.serialize_state()
    restored = PhaseSearchWindow(state, parent, store=store, saved_state=saved)
    qtbot.addWidget(restored)
    assert restored.analysis == window.analysis and restored.detection_current()
    restored.form.numeric["noise_sigma"].setValue(3)
    with pytest.raises(ValueError, match="Step 1"):
        restored._request()
    observed = next(c for c in restored.plot.getPlotItem().listDataItems() if c.name() == "Observed")
    assert np.max(observed.yData) == pytest.approx(100)
    assert np.median(observed.yData) < 1
