"""Non-modal XRD phase-search workflow using existing cancellable task services."""
from __future__ import annotations

import copy
import json
from contextlib import suppress
from dataclasses import asdict, replace
from pathlib import Path
from threading import Event
from typing import Any, cast

import numpy as np
from PySide6.QtCore import QItemSelectionModel, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from materials_data_studio.core.exceptions import InvalidAxisConversionError
from materials_data_studio.core.logging_config import user_data_directory
from materials_data_studio.core.models import Dataset, ModuleType, PlotStyle
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.modules.xrd.mp_database import MaterialsProjectBackend, MPDatabaseManager
from materials_data_studio.modules.xrd.phase_detection import (
    DOUBLET,
    DetectionSettings,
    analyze_peaks,
)
from materials_data_studio.modules.xrd.phase_forms import SearchForm, fill_peaks, fill_table, table
from materials_data_studio.modules.xrd.phase_models import CalculationSettings, Candidate
from materials_data_studio.modules.xrd.phase_overlays import (
    above_peaks,
    annotation_dataset,
    peak_owners,
    phase_color,
    phase_key,
    phase_label,
    reflections,
)
from materials_data_studio.modules.xrd.phase_plot import CandidateCheckFilter, StickScaleFilter
from materials_data_studio.modules.xrd.phase_reference import (
    ReferenceSearchEngine,
)
from materials_data_studio.modules.xrd.phase_search import (
    PhaseSearchService,
    experimental_data,
    experimental_signature,
    overlay_dataset,
    stick_segments,
)
from materials_data_studio.modules.xrd.phase_store import PhaseStore
from materials_data_studio.modules.xrd.qualx_database import ReferenceDatabaseManager
from materials_data_studio.modules.xrd.reference_dialog import ReferenceDatabaseDialog
from materials_data_studio.plotting.plot_spec import CurveSpec, PlotSpec
from materials_data_studio.plotting.pyqtgraph_backend import (
    apply_plot_theme,
    create_plot_widget,
    render_plot,
    update_rendered_plot_data,
)
from materials_data_studio.ui.action_layout import ActionBar, button_role
from materials_data_studio.ui.dialogs import show_error
from materials_data_studio.ui.localization import StatusLabel, tr
from materials_data_studio.ui.tasks import run_task


class PhaseSearchWindow(QDialog):
    progress = Signal(str)

    def __init__(self, state: ProjectState, parent: QWidget, *, saved_state: dict[str, Any] | None = None, store: PhaseStore | None = None, refresh_callback=lambda: None, selected_dataset_id: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("XRD Phase Search–Match")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMinMaxButtonsHint)
        self.resize(1400, 900)
        self.state, self.refresh_callback = state, refresh_callback
        self.service = PhaseSearchService(store or PhaseStore(user_data_directory() / "phase_search"))
        self.engine = ReferenceSearchEngine(self.service)
        self.reference_manager = ReferenceDatabaseManager(self.service.store)
        self.engine_metrics: dict[str, Any] = {}
        self.peaks: list[dict[str, Any]] = []
        self.candidates: list[Candidate] = []
        self.results: list[dict[str, Any]] = []
        self._checked_phases: set[str] = set()
        self._selecting_phases = False
        self._preview_cache: tuple[Any, ...] | None = None
        self._closed_view: tuple[Any, ...] | None = None
        self.matched_peaks: list[dict[str, Any]] = []
        self.matched_signature = ""
        self.analysis: dict[str, Any] = {}
        self.stick_scale = .6
        self.display_candidates: list[Candidate] = []
        self._preferred_dataset = selected_dataset_id
        self.search_conditions: dict[str, Any] = {}
        self.stop = Event()
        self._busy = False
        self._step1_ready = False
        self._restoring = True
        self.dataset_combo = QComboBox()
        self.form = SearchForm(1.540598)
        self.tabs = QTabWidget()
        self.peak_table = table(["Use", "2θ (deg)", "Intensity", "Width (deg)", "Origin"])
        peak_page = QWidget()
        peak_layout = QVBoxLayout(peak_page)
        self.detect_button = self._button("Detect peaks", self.detect)
        self.detect_button.setProperty("primary", True)
        self.fit_button = self._button("Use fitted peaks", self.use_fitted)
        self.detection_stop_button = self._button("Stop", self.cancel)
        self.detection_stop_button.setProperty("danger", True)
        policy = self.detection_stop_button.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.detection_stop_button.setSizePolicy(policy)
        peak_layout.addWidget(QLabel("Checked peaks are used for matching. Unchecked peaks remain visible."))
        peak_layout.addWidget(self.form.observation)
        detect_row = ActionBar([[self.detect_button, self.detection_stop_button]])
        detect_row.setMinimumHeight(32)
        peak_layout.addWidget(detect_row)
        peak_layout.addWidget(self.fit_button)
        peak_layout.addWidget(self.peak_table)
        self.tabs.addTab(self._scroll(peak_page), "Step 1 · Observed peaks")
        search_page = QWidget()
        search_layout = QVBoxLayout(search_page)
        search_layout.addWidget(self._scroll(self.form), 1)
        self.tabs.addTab(search_page, "Step 2 · Search settings")
        library_page = QWidget()
        library_layout = QVBoxLayout(library_page)
        library_layout.addWidget(QLabel("Registered CIFs are local snapshots. Originals are never changed or uploaded."))
        library_layout.addWidget(self._button("Register CIF files…", self.register_files))
        library_layout.addWidget(self._button("Register CIF folder…", self.register_folder))
        self.library_table = table(["Filename", "Formula", "Space group", "Identity"])
        library_layout.addWidget(self.library_table)
        library_layout.addWidget(self._button("Remove selected registrations", self.remove_local))
        self.tabs.addTab(library_page, "Local library")
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("Experimental dataset"))
        left_layout.addWidget(self.dataset_combo)
        self.database_button = self._button("Reference Databases…", self.manage_reference_databases)
        left_layout.addWidget(self.database_button)
        left_layout.addWidget(self.tabs)
        self.search_button = self._button("Search candidates · score and rank", self.search)
        self.search_button.setProperty("primary", True)
        self.cancel_button = self._button("Stop", self.cancel)
        self.cancel_button.setProperty("danger", False)
        self.cancel_button.setEnabled(False)
        search_layout.addWidget(ActionBar([[self.search_button, self.cancel_button]]))
        self.candidate_table = table(["Rank", "Formula", "Source", "ID", "Space group", "MDS Match Score", "Matched", "Peak coverage", "Selected", "Lattice (Å; deg)", "Source rank", "Possible group", "Hull (eV/atom)", "Cell scale", "DB version"])
        self.candidate_table.setMinimumHeight(170)
        self.candidate_table.itemChanged.connect(self._candidate_checked)
        self.candidate_check_filter = CandidateCheckFilter(self.candidate_table)
        self.candidate_table.viewport().installEventFilter(self.candidate_check_filter)
        self.plot = create_plot_widget()
        self.scale_filter = StickScaleFilter(self.plot)
        self.plot.viewport().installEventFilter(self.scale_filter)
        self.scale_filter.scaled.connect(self.scale_sticks)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(125)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(self.candidate_table, 2)
        right_layout.addWidget(self.plot, 4)
        self.add_button = self._button("Add calculated pattern to XRD", self.add_overlay)
        self.markers_button = self._button("Add matched markers to XRD", lambda: self.add_annotations("matched"))
        self.ticks_button = self._button("Add phase ticks to XRD", lambda: self.add_annotations("ticks"))
        self.adopt_button = self._button("Select / unselect candidate phase", self.adopt)
        self.url_button = self._button("Open source", self.open_source)
        self.cif_button = self._button("View CIF / structure", self.view_cif)
        button_role(self.add_button, "primary")
        use_results = QGroupBox("Working with results")
        use_layout = QVBoxLayout(use_results)
        use_layout.addWidget(ActionBar([[self.adopt_button, self.add_button]]))
        use_layout.addWidget(ActionBar([[self.markers_button, self.ticks_button]]))
        references = QGroupBox("Reference information")
        reference_layout = QVBoxLayout(references)
        reference_layout.addWidget(ActionBar([[self.url_button, self.cif_button]]))
        right_layout.addWidget(ActionBar([[use_results], [references]]))
        right_layout.addWidget(self.details)
        right_layout.addWidget(QLabel("Score is a search ranking. Ctrl + wheel changes calculated-stick intensity. Cu Kα1/2 fits a constrained doublet."))
        self.status = StatusLabel("Step 1: confirm radiation and detect observed peaks. Step 2: set chemical filters and search. Results are saved in the MDS session.")
        self.status.setWordWrap(True)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([420, 980])
        layout = QVBoxLayout(self)
        layout.addWidget(split)
        layout.addWidget(self.status)
        self.progress.connect(self._progress, Qt.ConnectionType.QueuedConnection)
        self.dataset_combo.currentIndexChanged.connect(self.dataset_changed)
        self.peak_table.itemChanged.connect(self.peak_changed)
        self.candidate_table.itemSelectionChanged.connect(self._candidate_selection_changed)
        for key, field in self.form.numeric.items():
            field.valueChanged.connect(self.refilter if key == "min_score" else self.settings_changed)
        self.form.radiation.currentTextChanged.connect(self.settings_changed)
        for field in self.form.fields.values():
            field.editingFinished.connect(self.changed)
        self.form.source.currentIndexChanged.connect(self.changed)
        self.form.mp_scale.valueChanged.connect(self.changed)
        self.form.crystal_system.currentIndexChanged.connect(self.changed)
        self.refresh_sources()
        self.refresh_library()
        self.restore(saved_state or {})
        self._restoring = False
        self._buttons()

        from materials_data_studio.ui.theme import current_theme
        self.apply_theme(current_theme())

    def apply_theme(self, theme: str) -> None:
        self._theme = theme
        apply_plot_theme(self.plot, theme)
        self.preview()

    @staticmethod
    def _button(label, callback) -> QPushButton:
        button = QPushButton(label)
        button.clicked.connect(callback)
        return button

    @staticmethod
    def _scroll(widget: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(widget)
        return scroll

    def refresh_sources(self) -> None:
        current = self._preferred_dataset or self.dataset_combo.currentData()
        self._preferred_dataset = None
        self.dataset_combo.blockSignals(True)
        self.dataset_combo.clear()
        for dataset in self.state.datasets:
            if dataset.module_type == ModuleType.XRD and not dataset.metadata.get("is_simulated_phase") and not dataset.metadata.get("phase_annotation"):
                self.dataset_combo.addItem(dataset.display_name, dataset.dataset_id)
        found = self.dataset_combo.findData(current)
        if found >= 0:
            self.dataset_combo.setCurrentIndex(found)
        self.dataset_combo.blockSignals(False)

    def dataset(self) -> Dataset:
        return next(d for d in self.state.datasets if d.dataset_id == self.dataset_combo.currentData())

    def dataset_changed(self) -> None:
        if self._restoring:
            return
        self.cancel()
        self.begin_workflow()
        self.peaks, self.results, self.matched_peaks = [], [], []
        self._checked_phases.clear()
        self._preview_cache = None
        self._closed_view = None
        self.analysis = {}
        try:
            wavelength = float(self.dataset().metadata.get("wavelength_a", 1.540598))
            self.form.numeric["wavelength"].setValue(wavelength)
            self.use_measured_range()
        except StopIteration:
            pass
        fill_peaks(self.peak_table, self.peaks)
        self._candidate_rows()
        self.preview()
        self.changed()

    def changed(self) -> None:
        if not self._restoring:
            self.state.modified = True
        self._buttons()

    def use_measured_range(self) -> None:
        try:
            x, _y = experimental_data(self.dataset(), self.form.numeric["wavelength"].value())
            finite = x[np.isfinite(x)]
            if finite.size:
                self.form.numeric["lower"].setValue(max(0.01, float(np.min(finite))))
                self.form.numeric["upper"].setValue(min(179.99, float(np.max(finite))))
        except (ValueError, StopIteration, InvalidAxisConversionError):
            pass  # Missing TOF calibration is explained when detection is requested.

    def settings_changed(self) -> None:
        if not self._restoring:
            if self._step1_ready and not self.detection_current():
                self.begin_workflow()
            self.changed()
            self.preview()

    def current_results(self, signature: str | None = None) -> bool:
        try:
            return bool(self.results) and self.detection_current(signature) and self.matched_peaks == self.peaks and asdict(CalculationSettings(**self.results[0]["settings"])) == asdict(self.form.settings()) and self.matched_signature == (signature if signature is not None else experimental_signature(self.dataset()))
        except (ValueError, StopIteration):
            return False

    def detection_current(self, signature: str | None = None) -> bool:
        if not self.analysis:
            return True  # Imported/manual fitted peaks have no detector settings.
        settings = DetectionSettings(**self.form.detection_values(), doublet=self.form.radiation.currentText() == DOUBLET)
        return self.analysis.get("settings") == asdict(settings) and self.analysis.get("wavelength") == self.form.numeric["wavelength"].value() and self.analysis.get("signature") == (signature if signature is not None else experimental_signature(self.dataset()))

    def _buttons(self, signature: str | None = None) -> None:
        result = self.selected_result()
        ready = self._step1_ready and not self._busy and self.current_results(signature) and bool(self.selected_results())
        self.add_button.setEnabled(ready)
        self.markers_button.setEnabled(ready)
        self.ticks_button.setEnabled(ready)
        self.adopt_button.setEnabled(ready)
        self.cif_button.setEnabled(not self._busy and result is not None)
        selected = self.selected_candidate()
        self.url_button.setEnabled(not self._busy and selected is not None and selected.source in {"COD", "Materials Project"})
        self.tabs.setTabEnabled(1, self._step1_ready)
        self.search_button.setEnabled(not self._busy and self._step1_ready and any(p.get("included", True) for p in self.peaks))
        self.detection_stop_button.setEnabled(self._busy)
        self.detection_stop_button.setVisible(self._busy)
        self.database_button.setEnabled(not self._busy)

    def manage_reference_databases(self) -> None:
        dialog = ReferenceDatabaseDialog(self.reference_manager, self)
        dialog.exec()
        self.changed()

    def begin_workflow(self) -> None:
        """A new opening or measurement requires explicit Step 1 confirmation."""
        self._step1_ready = False
        self.tabs.setCurrentIndex(0)
        self.tabs.setTabEnabled(1, False)
        message = "Step 1: run Detect peaks or Use fitted peaks before opening Step 2."
        if any(r.get("database", {}).get("source", "").startswith("QualX COD") for r in self.results):
            info = self.reference_manager.configuration()
            if not info or not Path(info["path"]).is_file():
                message += " Saved reference results remain visible; their database is unavailable for a new search."
        self.status.setText(message)
        self._buttons()

    def showEvent(self, event) -> None:
        self.begin_workflow()
        self.preview()
        if self._closed_view and self._preview_cache and self._closed_view[0] == self._preview_cache[0][0]:
            _signature, ranges, automatic = self._closed_view
            view = self.plot.getViewBox()
            view.setRange(xRange=ranges[0], yRange=ranges[1], padding=0)
            for axis, enabled in zip(("x", "y"), automatic, strict=True):
                view.enableAutoRange(axis, enabled)
        self._closed_view = None
        super().showEvent(event)

    @Slot(str)
    def _progress(self, message: str) -> None:
        self.status.setText(message)

    def _job(self, title, work, accept, *, dataset: Dataset | None = None) -> None:
        if self._busy:
            return
        self._busy = True
        self.stop = Event()
        self.tabs.tabBar().setEnabled(False)
        self.form.setEnabled(False)
        self.form.observation.setEnabled(False)
        self.detect_button.setEnabled(False)
        self.fit_button.setEnabled(False)
        self.dataset_combo.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.cancel_button.setProperty("danger", title == "Search, calculate and rank phases")
        self.cancel_button.style().unpolish(self.cancel_button)
        self.cancel_button.style().polish(self.cancel_button)
        self._buttons()
        task = run_task(self, title, lambda: work(self.stop), accept, state=self.state, dataset=dataset, cancelled=self.stop,
                        on_error=lambda message: self.status.setText(message))
        task.finished.connect(self._finished)

    def _finished(self) -> None:
        self._busy = False
        self.tabs.tabBar().setEnabled(True)
        self.form.setEnabled(True)
        self.form.observation.setEnabled(True)
        self.detect_button.setEnabled(True)
        self.fit_button.setEnabled(True)
        self.dataset_combo.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.cancel_button.setProperty("danger", False)
        self.cancel_button.style().unpolish(self.cancel_button)
        self.cancel_button.style().polish(self.cancel_button)
        if self.stop.is_set():
            self.status.setText("Stopped. Previous results were retained; no partial result was applied.")
        self._buttons()

    def cancel(self) -> None:
        self.stop.set()

    def closeEvent(self, event) -> None:
        self.cancel()
        if self._preview_cache:
            view = self.plot.getViewBox()
            self._closed_view = (self._preview_cache[0][0], view.viewRange(), list(view.state["autoRange"]))
        self._preview_cache = None
        render_plot(self.plot, PlotSpec("2θ (deg)", tr("Relative intensity"), []))
        super().closeEvent(event)

    def detect(self) -> None:
        try:
            dataset = self.dataset()
            x, y = experimental_data(dataset, self.form.settings().wavelength)
            settings = DetectionSettings(**self.form.detection_values(), doublet=self.form.radiation.currentText() == DOUBLET)
            signature = experimental_signature(dataset)
            def accept(analysis):
                self.analysis = {**analysis, "signature": signature, "wavelength": self.form.settings().wavelength}
                self._peaks_ready(analysis["peaks"])
            self._job("Background subtraction and peak fitting", lambda stop: analyze_peaks(x, y, settings, stop=stop), accept, dataset=dataset)
        except Exception as exc:
            self.status.setText(str(exc))

    def _peaks_ready(self, peaks) -> None:
        self.peaks = peaks
        self._step1_ready = any(p.get("included", True) for p in peaks)
        fill_peaks(self.peak_table, peaks)
        self.tabs.setCurrentIndex(0)
        self.status.setText(f"{len(peaks)} fitted reflections after automatic background subtraction. Check peak selection, then go to Step 2 and Search candidates.")
        self.preview()
        self.changed()

    def use_fitted(self) -> None:
        try:
            parent = self.parentWidget()
            saved = cast(Any, parent).peak_fitting_state()
            if saved.get("selected_dataset_id") != self.dataset().dataset_id:
                raise ValueError(tr("No saved fit for this experimental dataset. Use Detect peaks."))
            groups = (saved.get("last_result") or {}).get("groups", [])
            peaks = [{"position": float(g["fitted_two_theta_deg"]), "intensity": float(g["fitted_area"]),
                      "width": float(g["fwhm_deg"]), "included": bool(g.get("enabled", True)), "origin": "fitted"}
                     for g in groups if np.isfinite(g.get("fitted_two_theta_deg", np.nan)) and g.get("fitted_area", 0) > 0]
            if not peaks:
                raise ValueError(tr("No usable fitted groups. Use Detect peaks."))
            self.analysis = {}
            self._peaks_ready(peaks)
        except Exception as exc:
            self.status.setText(str(exc))

    def peak_changed(self, item) -> None:
        if item.column() == 0 and item.row() < len(self.peaks):
            self.peaks[item.row()]["included"] = item.checkState() == Qt.CheckState.Checked
            self.status.setText("Peak selection changed. Search candidates again to update scores.")
            self.preview()
            self.changed()

    def register_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Register local CIF snapshots", "", "CIF (*.cif)")
        self.register([Path(p) for p in paths])

    def register_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Register folder (top-level CIF files)")
        if folder:
            from itertools import islice
            paths = list(islice((p for p in Path(folder).iterdir() if p.is_file() and p.suffix.lower() == ".cif"), 1001))
            self.register(paths)

    def register(self, paths: list[Path]) -> None:
        if paths:
            self._job("Register local CIF library", lambda stop: self.service.store.register(paths, stop), self._registered)

    def _registered(self, outcome) -> None:
        self.refresh_library()
        self.status.setText(f"{len(outcome[0])} local CIFs registered. " + "\n".join(outcome[1]))

    def refresh_library(self) -> None:
        self.library = self.service.store.library()
        fill_table(self.library_table, [[Path(c.filename).name, c.formula, c.space_group, c.identity[:22]] for c in self.library])

    def remove_local(self) -> None:
        indices = {index.row() for index in self.library_table.selectionModel().selectedRows()}
        self.service.store.remove({self.library[i].identity for i in indices})
        self.refresh_library()

    def _request(self):
        dataset, settings = self.dataset(), self.form.settings()
        if not self._step1_ready:
            raise ValueError("Complete Step 1 with Detect peaks or Use fitted peaks before searching.")
        measured_x, _y = experimental_data(dataset, settings.wavelength)
        if not measured_x.size or settings.lower < float(np.min(measured_x)) - 1e-6 or settings.upper > float(np.max(measured_x)) + 1e-6:
            raise ValueError("Set the calculation range within the measured 2θ range.")
        if not any(p.get("included", True) for p in self.peaks):
            raise ValueError("Step 1: detect/select observed peaks before searching.")
        if not self.detection_current():
            raise ValueError("Radiation, input data or detection settings changed. Run Detect peaks again in Step 1.")
        return dataset, settings

    def search_mp(self) -> None:
        self.search(materials_project=True)

    def search(self, *, materials_project=False) -> None:
        try:
            dataset, settings = self._request()
            query, source = self.form.query(), self.form.source.currentIndex()
            peaks, conditions = copy.deepcopy(self.peaks), self.form.state()
            signature = experimental_signature(dataset)
            def work(stop):
                return self.engine.search(
                    peaks, query, settings,
                    cod=not materials_project and source in {0, 1},
                    user=not materials_project and source in {0, 3},
                    mp=materials_project or source in {0, 2},
                    mp_scale_limit=conditions["mp_scale_percent"] / 100,
                    candidate_limit=int(conditions["limits"]["candidate_limit"]),
                    result_limit=int(conditions["limits"]["result_limit"]),
                    stop=stop, report=self.progress.emit)
            self._job("Search, calculate and rank phases", work,
                      lambda outcome: self._ranked(outcome, peaks, conditions, signature), dataset=dataset)
        except Exception as exc:
            self.status.setText(str(exc))

    def calculate(self) -> None:
        """Re-evaluate retained candidates without another metadata request."""
        if any(r.get("database") and not r.get("cif") for r in self.results):
            self.search()
            return
        try:
            dataset, settings = self._request()
            selected = copy.deepcopy(self.candidates)
            peaks, conditions = copy.deepcopy(self.peaks), self.form.state()
            signature = experimental_signature(dataset)
            snapshots = {r["candidate"]["content_hash"]: r["cif"] for r in self.results}
            def work(stop):
                results, errors = self.service.calculate(selected, peaks, settings, stop, self.progress.emit, snapshots=snapshots)
                return selected, results, errors
            self._job("Recalculate retained phase matches", work,
                      lambda outcome: self._ranked(outcome, peaks, conditions, signature), dataset=dataset)
        except Exception as exc:
            self.status.setText(str(exc))

    def _ranked(self, outcome, peaks, conditions, signature):
        self.candidates, self.results, errors = outcome
        self.matched_peaks, self.matched_signature, self.search_conditions = peaks, signature, conditions
        self.engine_metrics = copy.deepcopy(self.engine.last_metrics) if any(r.get("database") for r in self.results) else {}
        threshold = self.form.numeric["min_score"]
        eligible = sorted((r["match"]["score"] for r in self.results if r["match"]["assignments"] and r["match"]["score"] > 0), reverse=True)
        target = min(10, len(eligible))
        old_threshold = threshold.value()
        if target and sum(score >= old_threshold for score in eligible) < target:
            # Lower only the display cutoff; never change scores or chemical filters.
            threshold.setValue(float(np.floor(eligible[target - 1] * 10) / 10))
        self.refilter()
        adjustment = f"Minimum match score automatically relaxed: {old_threshold:g} → {threshold.value():g}. " if threshold.value() < old_threshold else ""
        self.status.setText(adjustment + f"Scored {self.engine_metrics.get('scored', len(self.results))}/{len(self.candidates)} candidates; {len(self.display_candidates)} matched above threshold. " + "\n".join(errors))
        self.changed()

    def refilter(self) -> None:
        checked = set(self._checked_phases)
        self._candidate_rows()
        if self.display_candidates:
            self._selecting_phases = True
            try:
                for row in range(len(self.display_candidates)):
                    result = self._result_for_row(row)
                    if result is not None and phase_key(result) in checked:
                        self.candidate_table.selectionModel().select(self.candidate_table.model().index(row, 0), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
            finally:
                self._selecting_phases = False
            if not self.selected_results():
                self.candidate_table.selectRow(0)
        self.preview()
        self.changed()

    def scale_sticks(self, factor: float) -> None:
        self.stick_scale = float(np.clip(self.stick_scale * factor, .001, 1000))
        self.preview()
        self.changed()

    def _candidate_rows(self) -> None:
        threshold = self.form.numeric["min_score"].value()
        matches = [(i, r) for i, r in enumerate(self.results, 1) if r["match"]["score"] >= threshold and r["match"]["assignments"]]
        self.display_candidates = [Candidate(**r["candidate"]) for _, r in matches]
        rows = []
        for rank, result in matches:
            c = Candidate(**result["candidate"])
            rows.append([rank, c.formula, "MP — DFT-relaxed" if c.source == "Materials Project" else c.source,
                         c.identity if c.source in {"COD", "Materials Project"} else c.identity[:14] + "…", c.space_group,
                         f"{result['match']['score']:.2f}", len(result["match"]["assignments"]), f"{result['match']['components'].get('observed_coverage', 0):.1%}", "Yes" if result.get("adopted") else "", ", ".join(f"{v:.4g}" for v in c.lattice),
                         result.get("source_rank", ""), result.get("possible_group", ""), "" if result.get("mp_details", {}).get("energy_above_hull") is None else result["mp_details"]["energy_above_hull"], f"{result.get('lattice_scale', 1):.6f}", c.revision])
        fill_table(self.candidate_table, rows)
        self.candidate_table.blockSignals(True)
        try:
            for row, (_, result) in enumerate(matches):
                item = self.candidate_table.item(row, 0)
                assert item is not None
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if phase_key(result) in self._checked_phases else Qt.CheckState.Unchecked)
                item.setForeground(QColor(self.result_color(result)))
        finally:
            self.candidate_table.blockSignals(False)
        self._buttons()

    def result_color(self, result) -> str:
        key = phase_key(result)
        return phase_color(next((i for i, value in enumerate(self.results) if phase_key(value) == key), 0))

    def _candidate_checked(self, item) -> None:
        if item.column() != 0 or self._selecting_phases:
            return
        result = self._result_for_row(item.row())
        if result is None:
            return
        self._selecting_phases = True
        try:
            key = phase_key(result)
            checked = item.checkState() == Qt.CheckState.Checked
            self._checked_phases.discard(key)
            if checked:
                self._checked_phases.add(key)
            flags = QItemSelectionModel.SelectionFlag.Rows | (QItemSelectionModel.SelectionFlag.Select if checked else QItemSelectionModel.SelectionFlag.Deselect)
            self.candidate_table.selectionModel().select(self.candidate_table.model().index(item.row(), 0), flags)
        finally:
            self._selecting_phases = False
        self.preview()

    def _candidate_selection_changed(self) -> None:
        if self._selecting_phases:
            return
        rows = {index.row() for index in self.candidate_table.selectionModel().selectedRows()}
        self._selecting_phases = True
        self.candidate_table.blockSignals(True)
        try:
            self._checked_phases = {phase_key(result) for row in rows if (result := self._result_for_row(row)) is not None}
            for row in range(self.candidate_table.rowCount()):
                item = self.candidate_table.item(row, 0)
                if item is not None:
                    item.setCheckState(Qt.CheckState.Checked if row in rows else Qt.CheckState.Unchecked)
        finally:
            self.candidate_table.blockSignals(False)
            self._selecting_phases = False
        self.preview()

    def _result_for_row(self, row):
        if not 0 <= row < len(self.display_candidates):
            return None
        c = self.display_candidates[row]
        return next((result for result in self.results if result["candidate"]["source"] == c.source and result["candidate"]["identity"] == c.identity), None)

    def selected_results(self) -> list[dict[str, Any]]:
        visible = {c.source + ":" + c.identity for c in self.display_candidates}
        return [result for result in self.results if phase_key(result) in self._checked_phases & visible]

    def selected_candidate(self) -> Candidate | None:
        row = self.candidate_table.currentRow()
        return self.display_candidates[row] if 0 <= row < len(self.display_candidates) else None

    def selected_result(self) -> dict[str, Any] | None:
        return self._result_for_row(self.candidate_table.currentRow())

    def preview(self) -> None:
        signature = None
        try:
            dataset = self.dataset()
            signature = experimental_signature(dataset)
            wavelength = self.form.settings().wavelength
            main_background = bool(dataset.metadata.get("background_enabled") and "background" in dataset.processed_data)
            analysis_current = bool(self.analysis and self.analysis.get("signature") == signature)
            cache_key = (signature, wavelength, id(self.analysis), analysis_current)
            if self._preview_cache is None or self._preview_cache[0] != cache_key:
                x, y = experimental_data(dataset, wavelength)
                raw = experimental_data(dataset, wavelength, subtract_background=False)[1] if main_background else y
                if analysis_current:
                    y = y - np.interp(x, self.analysis["baseline_x"], self.analysis["baseline"])
                maximum = float(np.max(np.abs(y))) or 1.0
                y = y * 100 / maximum
                raw = raw * 100 / maximum if main_background or analysis_current else y
                for values in (x, y, raw):
                    values.setflags(write=False)
                self._preview_cache = (cache_key, x, y, raw)
            _, x, y, raw = self._preview_cache
            spec = PlotSpec("2θ (deg)", tr("Relative intensity"), [])
            if analysis_current or main_background:
                spec.curves.append(CurveSpec(x, raw, tr("Before automatic background"), PlotStyle(color="#aab3c2", marker="none")))
            spec.curves.append(CurveSpec(x, y, tr("Observed"), PlotStyle(color="#e2e8f0" if getattr(self, "_theme", "light") == "dark" else "#334155", marker="none")))
            results = self.selected_results() if self.current_results(signature) else []
            result = results[0] if results else None
            owners = peak_owners(results)
            markers = [("Matched" if len(results) == 1 else tr("Matched") + " · " + phase_label(item),
                        self.result_color(item), [peak for peak, owner in owners.items() if owner == index])
                       for index, item in enumerate(results)]
            peak_heights = above_peaks(x, y, np.array([peak["position"] for peak in self.peaks]))
            for label, color, mask in [*markers,
                                       ("Unexplained", "#dc2626", [i for i, p in enumerate(self.peaks) if p.get("included", True) and i not in owners]),
                                       ("Excluded", "#94a3b8", [i for i, p in enumerate(self.peaks) if not p.get("included", True)])]:
                if mask:
                    px = np.array([self.peaks[i]["position"] for i in mask])
                    py = peak_heights[mask]
                    spec.curves.append(CurveSpec(px, py, tr(label), PlotStyle(color=color, line_style="none", marker="o", marker_fill_color=color, marker_edge_color=color)))
            for item in results:
                positions, intensities = reflections(item)
                maximum = max(item["pattern"]["intensities"], default=0) or 1
                px, py = stick_segments(positions, intensities * (100 / maximum) * self.stick_scale)
                label = tr("Calculated sticks") + (" · " + phase_label(item) if len(results) > 1 else "")
                spec.curves.append(CurveSpec(px, py, label, PlotStyle(color=self.result_color(item), marker="none")))
            if result:
                s = result["settings"]
                m = result["match"]
                if len(results) == 1 and m["missing"]:
                    missing_x = np.array([m["groups"][i]["position"] for i in m["missing"]])
                    spec.curves.append(CurveSpec(missing_x, np.full(len(missing_x), -4.), tr("Predicted not observed"), PlotStyle(color="#9333ea", line_style="none", marker="^")))
                database = result.get("database", {})
                provenance = f"Reference: {database.get('source', 'CIF calculation')}; version {database.get('snapshot', '')}; identity {database.get('content_hash', '')[:12]}\n"
                self.details.setPlainText(tr("MDS Match Score {score}; matched {matched}; unexplained {unexplained}; predicted not observed {missing}; fixed shift {shift}°").format(score=f"{m['score']:.2f}", matched=len(m["assignments"]), unexplained=len(m["unmatched"]), missing=len(m["missing"]), shift=f"{s['zero_shift']:+.4f}") + "\n" + provenance + json.dumps(m["components"], indent=2) + "\n" + result["candidate"].get("title", ""))
                if len(results) > 1:
                    combined = tr("Selected phases: {count}; matched observed peaks: {matched}; unexplained: {unexplained}. Scores remain per phase.").format(count=len(results), matched=len(owners), unexplained=sum(p.get("included", True) and i not in owners for i, p in enumerate(self.peaks)))
                    self.details.setPlainText(combined + "\n" + "\n".join(f"{phase_label(item)}: {item['match']['score']:.2f}" for item in results))
            else:
                c = self.selected_candidate()
                self.details.setPlainText(json.dumps(c.to_dict(), ensure_ascii=False, indent=2) if c else tr("Detect peaks, then Search candidates to calculate and rank all retrieved phases. Only matches above Minimum match score are listed."))
            if not update_rendered_plot_data(self.plot, spec):
                render_plot(self.plot, spec)
            for index, item in enumerate(getattr(self.plot, "_materials_curve_items", [])):
                item.setZValue(index)
        except (StopIteration, ValueError, KeyError, InvalidAxisConversionError) as exc:
            self.details.setPlainText(str(exc))
        self._buttons(signature)

    def add_overlay(self) -> None:
        selected = self.selected_results()
        if not selected or not self.current_results():
            return
        results = copy.deepcopy(selected)
        colors = [self.result_color(result) for result in selected]
        def work(stop):
            from materials_data_studio.modules.xrd.cif_backend import (
                CifDocument,
                extract_cif_tables,
                update_cif_tables,
            )
            from materials_data_studio.modules.xrd.simulation import simulate_cif
            overlays = []
            for result, color in zip(results, colors, strict=True):
                if stop.is_set():
                    raise InterruptedError("Calculation stopped")
                candidate = Candidate(**result["candidate"])
                if not result.get("cif"):
                    if candidate.source == "Materials Project":
                        info = MPDatabaseManager(self.service.store).configuration()
                        reference = result.get("database", {})
                        if not info or info.get("snapshot") != candidate.revision or info.get("content_hash") != reference.get("content_hash"):
                            raise ValueError("Install the original MP index to simulate this saved structure")
                        backend = MaterialsProjectBackend(info, stop)
                        try:
                            text = backend.cif(candidate.identity)
                        finally:
                            backend.close()
                    else:
                        text = self.service.cod.cif(candidate, stop)
                    settings = result["settings"]
                    document = CifDocument(Path("phase.cif"), text, text)
                    scale = result.get("lattice_scale", 1.)
                    if scale != 1:
                        cell, sites, _warnings = extract_cif_tables(document)
                        update_cif_tables(document, replace(cell, a=cell.a * scale, b=cell.b * scale, c=cell.c * scale), sites)
                    calculated = simulate_cif(document, wavelength_a=settings["wavelength"],
                                               two_theta_range=(settings["lower"], settings["upper"]), occupancy_preview=False)
                    positions = calculated.two_theta_deg
                    result["cif"] = document.edited_text
                    result["pattern"] = {"positions": positions.tolist(), "intensities": calculated.intensity.tolist(),
                                         "d": calculated.d_spacing_a.tolist() if calculated.d_spacing_a is not None else [],
                                         "hkl": calculated.hkl_labels or []}
                dataset = overlay_dataset(result)
                dataset.plot_style.color = color
                overlays.append(dataset)
            if stop.is_set():
                raise InterruptedError("Calculation stopped")
            return overlays
        def accept(overlays):
            for dataset in overlays:
                self.state.add_dataset(dataset)
            self.refresh_callback()
            self.status.setText("Calculated phase added to XRD as a separate dataset. Use its checkbox to show/hide it.")
            self.changed()
        self._job("Simulate selected phase profiles", work, accept, dataset=self.dataset())

    def add_annotations(self, kind: str) -> None:
        results = self.selected_results()
        if not results or not self.current_results():
            return
        parent = self.dataset()
        for index, result in enumerate(results):
            assignments = result["match"]["assignments"]
            maximum = max(result["pattern"]["intensities"], default=0) or 1
            positions = [self.peaks[item["observed"]]["position"] for item in assignments]
            strengths = [result["match"]["groups"][item["predicted"]]["intensity"] * 100 / maximum for item in assignments]
            child = annotation_dataset(parent, result, self.result_color(result), kind, positions=positions, strengths=strengths, row=index)
            child.display_name = phase_label(result) + " · " + tr("Matched" if kind == "matched" else "Phase ticks")
            # Reapply without duplicating children or discarding custom styling.
            existing = next((item for item in self.state.datasets if item.metadata.get("phase_parent") == parent.dataset_id
                             and item.metadata.get("phase_annotation") == kind
                             and item.metadata.get("phase_identity") == phase_key(result)), None)
            if existing is None:
                self.state.add_dataset(child)
            else:
                existing.raw_data = child.raw_data
                existing.visible = True
        self.state.modified = True
        self.refresh_callback()
        self.status.setText("Phase annotations added below the measured dataset. Select each child to adjust its style and position.")
        self.changed()

    def adopt(self) -> None:
        result = self.selected_result()
        if result is not None and self.current_results():
            result["adopted"] = not result.get("adopted", False)
            self.refilter()
            self.changed()

    def open_source(self) -> None:
        c = self.selected_candidate()
        if c is not None and c.source == "COD" and c.identity.isdigit() and len(c.identity) == 7:
            QDesktopServices.openUrl(QUrl(f"https://www.crystallography.net/cod/{c.identity}.html"))
        elif c is not None and c.source == "Materials Project" and c.identity.startswith("mp-"):
            QDesktopServices.openUrl(QUrl("https://materialsproject.org/materials/" + c.identity))

    def view_cif(self) -> None:
        result = self.selected_result()
        if result is None:
            return
        if not result.get("cif"):
            candidate = Candidate(**result["candidate"])
            if candidate.source == "Materials Project":
                def read_structure(stop):
                    info = MPDatabaseManager(self.service.store).configuration()
                    if not info or info.get("snapshot") != candidate.revision or info.get("content_hash") != result.get("database", {}).get("content_hash"):
                        raise ValueError("Install the original MP index to view this saved structure")
                    backend = MaterialsProjectBackend(info, stop)
                    try:
                        return backend.cif(candidate.identity)
                    finally:
                        backend.close()
                def show_mp(text):
                    # Preserve the reference hash; CIF rendering is a separate representation.
                    self._show_cif({**result, "cif": text})
                self._job("Read selected MP structure", read_structure, show_mp, dataset=self.dataset())
                return
            def accept(text):
                result["cif"] = text
                result["cif_provenance"] = {"source": "COD selected CIF", "content_hash": candidate.content_hash, "retrieved_at": candidate.retrieved_at}
                self.changed()
                self._show_cif(result)
            self._job("Fetch selected CIF from COD", lambda stop: self.service.cod.cif(candidate, stop), accept, dataset=self.dataset())
            return
        self._show_cif(result)

    def _show_cif(self, result) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("CIF snapshot / structure information")
        dialog.resize(800, 650)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(json.dumps({"candidate": result["candidate"], "reference_database": result.get("database", {}),
                                     "mp_details": result.get("mp_details", {}), "lattice_scale": result.get("lattice_scale", 1),
                                     "scale_penalty": result.get("scale_penalty", 0), "cif_provenance": result.get("cif_provenance", {})}, indent=2, ensure_ascii=False) + "\n\n" + result["cif"])
        export = QPushButton("Export selected CIF…")
        def save_cif():
            name, _ = QFileDialog.getSaveFileName(dialog, "Export CIF", result["candidate"]["identity"] + ".cif", "CIF (*.cif)")
            if name:
                Path(name).write_text(result["cif"], encoding="utf-8")
        export.clicked.connect(save_cif)
        layout.addWidget(export)
        layout.addWidget(text)
        dialog.exec()

    def serialize_state(self) -> dict[str, Any]:
        try:
            dataset = self.dataset()
            identity = {"dataset_id": dataset.dataset_id, "source_hash": dataset.source_hash}
        except StopIteration:
            identity = {}
        return {**identity, "form": self.form.state(), "peaks": copy.deepcopy(self.peaks), "matched_peaks": copy.deepcopy(self.matched_peaks), "matched_signature": self.matched_signature,
                "checked_phases": sorted(self._checked_phases),
                "analysis": copy.deepcopy(self.analysis), "engine_metrics": copy.deepcopy(self.engine_metrics), "stick_scale": self.stick_scale, "candidates": [c.to_dict() for c in self.candidates], "results": copy.deepcopy(self.results), "search_conditions": copy.deepcopy(self.search_conditions)}

    def restore(self, saved: dict[str, Any]) -> None:
        try:
            if saved.get("form"):
                self.form.restore(saved["form"])
            index = self.dataset_combo.findData(saved.get("dataset_id"))
            if index >= 0:
                self.dataset_combo.setCurrentIndex(index)
            elif not saved:
                with suppress(StopIteration):
                    self.form.numeric["wavelength"].setValue(float(self.dataset().metadata.get("wavelength_a", 1.540598)))
                    self.use_measured_range()
            self.analysis = copy.deepcopy(saved.get("analysis", {}))
            self.engine_metrics = copy.deepcopy(saved.get("engine_metrics", {}))
            self.stick_scale = float(saved.get("stick_scale", .6))
            self.peaks = copy.deepcopy(saved.get("peaks", []))
            self.matched_peaks = copy.deepcopy(saved.get("matched_peaks", []))
            self.matched_signature = saved.get("matched_signature", "")
            self.candidates = [Candidate(**r) for r in saved.get("candidates", [])]
            self.results = copy.deepcopy(saved.get("results", []))
            self._checked_phases = set(saved.get("checked_phases", []))
            self.search_conditions = copy.deepcopy(saved.get("search_conditions", {}))
            fill_peaks(self.peak_table, self.peaks)
            self.refilter()
            self.preview()
            if any(r.get("database", {}).get("source", "").startswith("QualX COD") for r in self.results):
                installed = self.reference_manager.configuration()
                if not installed or not Path(installed["path"]).is_file():
                    self.status.setText("Saved reference sticks/results are available. The reference database is unavailable; import it to search again. Complete Step 1 before Step 2.")
        except Exception as exc:
            show_error(self, "Restore phase search", str(exc))
