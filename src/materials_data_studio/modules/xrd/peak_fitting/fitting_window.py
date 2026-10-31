"""Non-modal Qt window for CIF-constrained whole-pattern peak fitting."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from threading import Event
from typing import Any, cast

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from materials_data_studio.core.models import (
    AxisType,
    Dataset,
    ModuleType,
    NormalizationMode,
    PlotStyle,
)
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.core.workers import Worker
from materials_data_studio.modules.xrd.cif_backend import (
    CifDocument,
    extract_cif_symmetry,
    extract_cif_tables,
)
from materials_data_studio.modules.xrd.peak_fitting.export import (
    export_analysis_log,
    export_fit_data,
    export_fit_figure,
    result_from_state,
    result_to_state,
)
from materials_data_studio.modules.xrd.peak_fitting.models import (
    FitConfiguration,
    FitParameter,
    FitProgress,
    PeakFitResult,
)
from materials_data_studio.modules.xrd.peak_fitting.optimizer import (
    correlation_safeguards,
    fit_pattern,
    preview_pattern,
)
from materials_data_studio.modules.xrd.peak_fitting.reflections import (
    group_reflections,
    independent_lattice_parameters,
    reflections_from_metadata,
)
from materials_data_studio.modules.xrd.transforms import (
    normalization_factor,
    process_intensity,
)
from materials_data_studio.plotting.matplotlib_backend import (
    FIGURE_FILE_FILTERS,
    resolve_figure_export_path,
)
from materials_data_studio.plotting.plot_spec import CurveSpec, PlotSpec
from materials_data_studio.plotting.pyqtgraph_backend import (
    apply_plot_theme,
    create_plot_widget,
    render_plot,
)
from materials_data_studio.ui.action_layout import ActionBar
from materials_data_studio.ui.dialogs import show_error
from materials_data_studio.ui.numeric_inputs import (
    FlexibleDoubleSpinBox as QDoubleSpinBox,
)

LOGGER = logging.getLogger(__name__)

_PARAMETER_LABELS = {
    "a": "Lattice a",
    "b": "Lattice b",
    "c": "Lattice c",
    "alpha": "Lattice α",
    "beta": "Lattice β",
    "gamma": "Lattice γ",
    "wavelength": "Wavelength",
    "zero_shift": "Zero shift",
    "displacement": "Displacement coefficient",
    "fwhm": "Common FWHM",
    "eta": "Pseudo-Voigt η",
    "phase_scale": "Phase scale",
}


class ApplyPeakFitDialog(QDialog):
    """Choose which fitted values are copied into the in-memory simulation."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Apply Fit Result")
        layout = QVBoxLayout(self)
        explanation = QLabel(
            "Select values to copy into the in-memory simulation. "
            "The original CIF file will not be overwritten."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.options: dict[str, QCheckBox] = {}
        for key, label in (
            ("lattice", "Lattice parameters"),
            ("wavelength", "Wavelength"),
            ("shifts", "Zero/displacement shifts"),
            ("profile", "Profile FWHM"),
            ("scale", "Phase scale"),
        ):
            checkbox = QCheckBox(label)
            checkbox.setChecked(key in {"lattice", "profile"})
            self.options[key] = checkbox
            layout.addWidget(checkbox)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Ok
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected(self) -> set[str]:
        return {
            name for name, checkbox in self.options.items() if checkbox.isChecked()
        }


class XrdPeakFittingWindow(QMainWindow):
    """CIF-constrained whole-pattern fitting without modifying raw XRD data."""

    def __init__(
        self,
        state: ProjectState,
        *,
        apply_callback: Callable[[str, PeakFitResult, set[str]], None],
        saved_state: dict[str, Any] | None = None,
        theme: str = "light",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.state = state
        self.apply_callback = apply_callback
        self._background_ready = False
        self._background_busy = False
        self._background_stop = Event()
        self._fit_worker: Worker | None = None
        self._fit_cancel_event: Event | None = None
        self._pending_fit_state: dict[str, Any] | None = None
        self._undo_fit_state: dict[str, Any] | None = None
        self._result: PeakFitResult | None = None
        self._parameter_rows: dict[str, int] = {}
        self._parameter_errors: dict[str, float | None] = {}
        self._area_refine: dict[int, bool] = {}
        self._preview_groups = []
        self._updating_parameters = False
        self._updating_reflections = False
        self._updating_range = False
        self._theme = theme
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(80)
        self._preview_timer.timeout.connect(self._refresh_plots)
        self.setWindowTitle("CIF-Constrained XRD Peak Fitting")
        self.resize(1320, 850)
        self._build_controls()
        self._build_layout()
        self._populate_datasets()
        self._populate_phases()
        self._initialize_selection()
        dataset = self._dataset()
        if dataset is not None:
            self.radiation.setCurrentText(str(dataset.metadata.get("radiation", "Cu Kα1")))
        if saved_state:
            self.restore_state(saved_state)
        self.apply_theme(theme)

    @staticmethod
    def compatible_datasets(state: ProjectState) -> list[Dataset]:
        """Return experimental angle-dispersive datasets only."""
        return [
            dataset
            for dataset in state.datasets
            if dataset.module_type is ModuleType.XRD
            and not dataset.metadata.get("is_simulated_phase")
            and not dataset.metadata.get("phase_annotation")
            and str(dataset.metadata.get("source_axis", "two_theta"))
            == AxisType.TWO_THETA.value
        ]

    @staticmethod
    def phase_datasets(state: ProjectState) -> list[Dataset]:
        """Return simulated CIF phase datasets with reflection metadata."""
        return [
            dataset
            for dataset in state.datasets
            if dataset.module_type is ModuleType.XRD
            and dataset.metadata.get("is_simulated_phase")
            and dataset.metadata.get("stick_hkl_labels")
        ]

    def _spin(
        self,
        minimum: float,
        maximum: float,
        value: float,
        decimals: int = 6,
    ) -> QDoubleSpinBox:
        field = QDoubleSpinBox()
        field.setDecimals(decimals)
        field.setRange(minimum, maximum)
        field.setValue(value)
        return field

    def _build_controls(self) -> None:
        from materials_data_studio.modules.xrd.constants import WAVELENGTH_PRESETS_A
        self.radiation = QComboBox()
        for name in WAVELENGTH_PRESETS_A:
            self.radiation.addItem(name, name)
        self.alpha2_ratio = self._spin(0, 1, .5, 3)
        self.radiation.currentIndexChanged.connect(self._radiation_changed)
        self.alpha2_ratio.valueChanged.connect(self._model_options_changed)
        self.dataset_combo = QComboBox()
        self.phase_combo = QComboBox()
        self.dataset_combo.currentIndexChanged.connect(self._dataset_changed)
        self.phase_combo.currentIndexChanged.connect(self._phase_changed)
        self.phase_enabled = QCheckBox("Enable selected phase")
        self.phase_enabled.setChecked(True)
        self.fit_min = self._spin(-1.0e9, 1.0e9, 1.0)
        self.fit_max = self._spin(-1.0e9, 1.0e9, 90.0)
        self.fit_min.valueChanged.connect(self._range_spin_changed)
        self.fit_max.valueChanged.connect(self._range_spin_changed)
        self.full_range = QPushButton("Use Full Range")
        self.full_range.clicked.connect(self._set_full_range)
        self.exclusions = QLineEdit()
        self.exclusions.setPlaceholderText("e.g. 25-27, 43.5-44.2")
        self.exclusions.textChanged.connect(self._range_options_changed)
        self.background_mode = QComboBox()
        self.background_mode.addItem("Use current processed background", "current")
        self.background_mode.setToolTip(
            "Apply arPLS background in Step 1 before fitting. Raw data are retained."
        )
        self.background_order = QSpinBox()
        self.background_order.setRange(0, 8)
        self.background_order.setValue(2)
        self.intensity_mode = QComboBox()
        self.intensity_mode.addItem(
            "Le Bail iterative intensities", "independent"
        )
        self.intensity_mode.addItem("CIF intensity-ratio mode", "cif_ratio")
        self.grouping_factor = self._spin(0.0, 10.0, 0.5, 3)
        self.le_bail_cycles = QSpinBox()
        self.le_bail_cycles.setRange(1, 500)
        self.le_bail_cycles.setValue(20)
        self.weighting = QComboBox()
        self.weighting.addItem("Uniform", "uniform")
        self.weighting.addItem("Poisson-like", "poisson")
        self.weighting.addItem("Custom uncertainty", "custom")
        self.loss = QComboBox()
        self.loss.addItem("Linear least squares", "linear")
        self.loss.addItem("Soft L1", "soft_l1")
        self.loss.addItem("Huber", "huber")
        for control in (
            self.intensity_mode,
            self.grouping_factor,
        ):
            if isinstance(control, QComboBox):
                control.currentIndexChanged.connect(self._model_options_changed)
            else:
                control.valueChanged.connect(self._model_options_changed)

        self.parameter_table = QTableWidget(0, 8)
        self.parameter_table.setHorizontalHeaderLabels(
            [
                "Refine",
                "Parameter",
                "Current value",
                "Standard error",
                "Lower bound",
                "Upper bound",
                "Unit",
                "Status",
            ]
        )
        self.parameter_table.setAlternatingRowColors(True)
        self.parameter_table.setMinimumHeight(320)
        self.parameter_table.horizontalHeader().setStretchLastSection(True)
        self.parameter_table.itemChanged.connect(self._parameter_item_changed)
        self.reflection_table = QTableWidget(0, 12)
        self.reflection_table.setHorizontalHeaderLabels(
            [
                "Refine area",
                "Group",
                "Phase",
                "hkl",
                "Calc. 2θ",
                "Fit 2θ",
                "Calc. d",
                "Fit d",
                "Calc. I",
                "Fit area",
                "FWHM",
                "Status",
            ]
        )
        self.reflection_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self.status = QPlainTextEdit()
        self.status.setReadOnly(True)
        self.status.setMaximumHeight(150)
        self.status.setPlainText("Select an experimental dataset and CIF phase.")

        self.run_button = QPushButton("Run Fit")
        self.run_button.setProperty("primary", True)
        self.run_button.setProperty("danger", False)
        self.undo_fit_button = QPushButton("Undo Last Fit")
        self.undo_fit_button.setToolTip(
            "Restore parameters, options, plots, and results to the state "
            "immediately before the last completed fit."
        )
        self.undo_fit_button.setEnabled(False)
        self.reset_button = QPushButton("Reset to Initial")
        self.apply_button = QPushButton("Apply Result to Simulation")
        self.save_button = QPushButton("Save Fit Data")
        self.figure_button = QPushButton("Export Figure")
        self.close_button = QPushButton("Close")
        self.run_button.clicked.connect(self._run_or_stop_fit)
        self.undo_fit_button.clicked.connect(self._undo_last_fit)
        self.reset_button.clicked.connect(self._reset_initial)
        self.apply_button.clicked.connect(self._apply_result)
        self.save_button.clicked.connect(self._save_fit_data)
        self.figure_button.clicked.connect(self._export_figure)
        self.close_button.clicked.connect(self.close)
        self.apply_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.figure_button.setEnabled(False)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_label = QLabel("Idle")
        self.progress_details = QPlainTextEdit()
        self.progress_details.setReadOnly(True)
        self.progress_details.setMaximumHeight(150)
        self.progress_details.setPlaceholderText(
            "Le Bail cycle progress and parameter changes appear here."
        )

        self.main_plot = create_plot_widget()
        self.residual_plot = create_plot_widget()
        self.residual_plot.setMaximumHeight(280)
        self.residual_plot.setXLink(self.main_plot)
        self.reflection_hover = pg.TextItem(anchor=(0.5, 1.15))
        self.reflection_hover.setZValue(1000)
        self.reflection_hover.hide()
        cast(Any, self.main_plot.scene()).sigMouseMoved.connect(
            self._show_reflection_hover
        )

    def _build_layout(self) -> None:
        toolbar = QWidget()
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(6, 4, 6, 4)
        toolbar_layout.addWidget(ActionBar([[self.undo_fit_button], [self.reset_button], [self.save_button, self.figure_button], [self.close_button]]))

        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        selection = QGroupBox("Fit Workspace")
        selection_form = QFormLayout(selection)
        selection_form.addRow("CIF phase", self.phase_combo)
        selection_form.addRow(self.phase_enabled)
        range_row = QHBoxLayout()
        range_row.addWidget(self.fit_min)
        range_row.addWidget(QLabel("to"))
        range_row.addWidget(self.fit_max)
        selection_form.addRow("Fit range (2θ deg)", range_row)
        selection_form.addRow(self.full_range)
        selection_form.addRow("Excluded ranges", self.exclusions)
        selection_form.addRow("Background", self.background_mode)
        selection_form.addRow("Radiation", self.radiation)
        selection_form.addRow("Cu Kα2 / Kα1 intensity", self.alpha2_ratio)
        selection_form.addRow("Intensity mode", self.intensity_mode)
        selection_form.addRow("Grouping factor", self.grouping_factor)
        selection_form.addRow("Le Bail cycles", self.le_bail_cycles)
        selection_form.addRow("Weighting", self.weighting)
        selection_form.addRow("Robust loss", self.loss)
        panel_layout.addWidget(selection)
        parameters = QGroupBox("Parameters")
        parameters_layout = QVBoxLayout(parameters)
        parameters_layout.addWidget(self.parameter_table)
        panel_layout.addWidget(parameters, 2)
        panel_layout.addWidget(self.run_button)
        panel_layout.addWidget(self.apply_button)
        progress_row = QHBoxLayout()
        progress_row.addWidget(self.progress_label)
        progress_row.addWidget(self.progress_bar, 1)
        panel_layout.addLayout(progress_row)
        panel_layout.addWidget(self.progress_details)
        panel_layout.addWidget(self.status)

        panel_scroll = QScrollArea()
        panel_scroll.setWidgetResizable(True)
        panel_scroll.setWidget(panel)
        plots = QWidget()
        plots_layout = QVBoxLayout(plots)
        plots_layout.addWidget(self.main_plot, 3)
        plots_layout.addWidget(self.residual_plot, 1)
        reflections = QGroupBox("Reflection Groups")
        reflection_layout = QVBoxLayout(reflections)
        reflection_layout.addWidget(self.reflection_table)
        plots_layout.addWidget(reflections, 2)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.steps = QTabWidget()
        background_page = QWidget()
        background_layout = QFormLayout(background_page)
        background_layout.addRow("Experimental dataset", self.dataset_combo)
        background_layout.addRow(QLabel("Apply arPLS background before configuring the fit. Raw data are retained."))
        self.arpls_smoothness = self._spin(1, 1e12, 1e5, 0)
        self.arpls_ratio = self._spin(.000001, .99, .01, 6)
        self.arpls_iterations = QSpinBox()
        self.arpls_iterations.setRange(1, 10000)
        self.arpls_iterations.setValue(50)
        background_layout.addRow("Smoothness λ", self.arpls_smoothness)
        background_layout.addRow("Convergence ratio", self.arpls_ratio)
        background_layout.addRow("Maximum iterations", self.arpls_iterations)
        self.background_button = QPushButton("Apply arPLS Background")
        self.background_button.setProperty("primary", True)
        self.background_button.clicked.connect(self._apply_arpls)
        background_layout.addRow(self.background_button)
        self.background_status = QLabel("Step 1: apply arPLS background before opening Step 2.")
        self.background_status.setWordWrap(True)
        background_layout.addRow(self.background_status)
        self.steps.addTab(background_page, "Step 1 · arPLS Background")
        self.steps.addTab(panel_scroll, "Step 2 · Fit settings")
        self.steps.setTabEnabled(1, False)
        splitter.addWidget(self.steps)
        splitter.addWidget(plots)
        splitter.setSizes([430, 890])
        splitter.setStretchFactor(1, 1)
        self.splitter = splitter

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(toolbar)
        layout.addWidget(splitter, 1)
        self.setCentralWidget(central)

    def _radiation_changed(self) -> None:
        from materials_data_studio.modules.xrd.constants import WAVELENGTH_PRESETS_A
        name = self.radiation.currentData()
        self._result = None
        self._set_result_actions(False)
        value = WAVELENGTH_PRESETS_A.get(name)
        if value is not None and "wavelength" in self._parameter_rows:
            parameters = self._current_table_parameters()
            parameters["wavelength"].value = value
            self._rebuild_parameters(parameters)
        self._range_options_changed()

    def _apply_arpls(self) -> None:
        from materials_data_studio.modules.xrd.background import arpls_background
        from materials_data_studio.ui.tasks import run_task
        dataset = self._dataset()
        if dataset is None or self._background_busy or self._fit_worker is not None:
            return
        self._background_busy = True
        self._background_ready = False
        self.steps.setTabEnabled(1, False)
        self.run_button.setEnabled(False)
        self._background_stop = Event()
        self.background_button.setEnabled(False)
        self.dataset_combo.setEnabled(False)
        options: dict[str, Any] = dict(smoothness=self.arpls_smoothness.value(), convergence_ratio=self.arpls_ratio.value(), max_iterations=self.arpls_iterations.value())
        cancelled = self._background_stop
        def complete(background):
            from materials_data_studio.core.processing import record_operation
            dataset.processed_data["background"] = background
            dataset.metadata.update(background_method="arpls", background_enabled=True, background_preview=False,
                                    arpls_smoothness=options["smoothness"], arpls_convergence_ratio=options["convergence_ratio"], arpls_max_iterations=options["max_iterations"])
            record_operation(dataset, "background", options)
            self._result = None
            self._set_result_actions(False)
            self._background_ready = True
            self.steps.setTabEnabled(1, True)
            self.steps.setCurrentIndex(1)
            self.run_button.setEnabled(self.phase_combo.count() > 0)
            self.background_status.setText("arPLS background applied. Continue in Step 2.")
            self.state.modified = True
            self._refresh_plots()
        task = run_task(self, "Apply arPLS Background", lambda: arpls_background(dataset.array("intensity"), **options, cancel_event=cancelled),
                        complete, state=self.state, dataset=dataset, cancelled=cancelled,
                        on_error=self.background_status.setText)
        task.finished.connect(self._background_finished)

    def _background_finished(self) -> None:
        self._background_busy = False
        self.background_button.setEnabled(True)
        self.dataset_combo.setEnabled(True)

    def _populate_datasets(self) -> None:
        self.dataset_combo.blockSignals(True)
        self.dataset_combo.clear()
        for dataset in self.compatible_datasets(self.state):
            wavelength = float(dataset.metadata.get("wavelength_a", 1.540598))
            self.dataset_combo.addItem(
                f"{dataset.display_name} — {wavelength:.6g} Å — "
                f"{len(dataset.array('x'))} points",
                dataset.dataset_id,
            )
        self.dataset_combo.blockSignals(False)

    def _populate_phases(self) -> None:
        self.phase_combo.blockSignals(True)
        self.phase_combo.clear()
        for phase in self.phase_datasets(self.state):
            self.phase_combo.addItem(phase.display_name, phase.dataset_id)
        self.phase_combo.blockSignals(False)

    def _dataset(self) -> Dataset | None:
        dataset_id = self.dataset_combo.currentData()
        return next(
            (
                dataset
                for dataset in self.state.datasets
                if dataset.dataset_id == dataset_id
            ),
            None,
        )

    def _phase(self) -> Dataset | None:
        phase_id = self.phase_combo.currentData()
        return next(
            (
                dataset
                for dataset in self.state.datasets
                if dataset.dataset_id == phase_id
            ),
            None,
        )

    def _experimental_arrays(
        self, dataset: Dataset
    ) -> tuple[np.ndarray, np.ndarray]:
        if (
            str(dataset.metadata.get("source_axis", AxisType.TWO_THETA.value))
            != AxisType.TWO_THETA.value
        ):
            raise ValueError("TOF datasets are not supported by this Bragg-angle fit.")
        x = np.asarray(dataset.array("x"), dtype=float)
        raw = np.asarray(dataset.array("intensity"), dtype=float)
        y = process_intensity(
            x,
            raw,
            normalization=NormalizationMode(
                str(dataset.metadata.get("normalization", "none"))
            ),
            scale=float(dataset.metadata.get("scale", 1.0)),
            offset=float(dataset.metadata.get("offset", 0.0)),
            background=dataset.processed_data.get("background"),
            subtract_background=bool(
                dataset.metadata.get("background_enabled", False)
            ),
        )
        return x, y

    def _initialize_selection(self) -> None:
        enabled = self.dataset_combo.count() > 0 and self.phase_combo.count() > 0
        self.run_button.setEnabled(enabled and self._background_ready)
        if enabled:
            self._set_full_range()
            self._rebuild_parameters()
            self._refresh_plots()
        else:
            self.status.setPlainText(
                "Peak fitting requires one angle-dispersive experimental XRD "
                "dataset and one simulated CIF phase."
            )

    def _dataset_changed(self) -> None:
        self._background_stop.set()
        self._background_ready = False
        self.steps.setCurrentIndex(0)
        self.steps.setTabEnabled(1, False)
        self.run_button.setEnabled(False)
        dataset = self._dataset()
        if dataset is not None:
            self.radiation.setCurrentText(str(dataset.metadata.get("radiation", "Cu Kα1")))
        if not self._confirm_clear_result():
            return
        self._set_full_range()
        self._rebuild_parameters()
        self._refresh_plots()

    def _phase_changed(self) -> None:
        if not self._confirm_clear_result():
            return
        self._rebuild_parameters()
        self._refresh_plots()

    def _model_options_changed(self) -> None:
        self._result = None
        self._set_result_actions(False)
        self._rebuild_parameters()
        self._refresh_plots()

    def _range_options_changed(self) -> None:
        """Refresh parameters, reflection rows, and plots after range edits."""
        if self._updating_range:
            return
        try:
            self._excluded_ranges()
        except ValueError:
            return
        self._result = None
        self._set_result_actions(False)
        self._rebuild_parameters()
        self._refresh_plots()

    def _confirm_clear_result(self) -> bool:
        if self._result is None:
            return True
        response = QMessageBox.question(
            self,
            "Clear Incompatible Fit",
            "Changing the selected dataset or phase clears the current fit result. Continue?",
        )
        if response != QMessageBox.StandardButton.Yes:
            return False
        self._result = None
        self._set_result_actions(False)
        return True

    def _set_full_range(self) -> None:
        dataset = self._dataset()
        if dataset is None:
            return
        x, _y = self._experimental_arrays(dataset)
        finite = x[np.isfinite(x)]
        if not len(finite):
            return
        self._updating_range = True
        self.fit_min.setValue(float(np.min(finite)))
        self.fit_max.setValue(float(np.max(finite)))
        self._updating_range = False
        self._refresh_plots()

    def _excluded_ranges(self) -> tuple[tuple[float, float], ...]:
        text = self.exclusions.text().strip()
        if not text:
            return ()
        ranges = []
        for part in text.split(","):
            pieces = [value.strip() for value in part.split("-")]
            if len(pieces) != 2:
                raise ValueError(
                    "Excluded ranges must use comma-separated minimum-maximum pairs."
                )
            lower, upper = (float(value) for value in pieces)
            if lower >= upper:
                raise ValueError("Excluded range minimum must be smaller than maximum.")
            ranges.append((lower, upper))
        return tuple(ranges)

    def _initial_parameters(self) -> dict[str, FitParameter]:
        dataset, phase = self._dataset(), self._phase()
        if dataset is None or phase is None:
            return {}
        document = self._phase_document(phase)
        cell, _sites, _warnings = extract_cif_tables(document)
        symmetry = extract_cif_symmetry(document)
        parameters = {}
        for name in independent_lattice_parameters(symmetry.crystal_system):
            value = float(getattr(cell, name))
            if name in {"a", "b", "c"}:
                lower, upper, unit = value * 0.9, value * 1.1, "Å"
            else:
                lower, upper, unit = max(0.1, value - 10), min(179.9, value + 10), "deg"
            parameters[name] = FitParameter(
                name, value, lower, upper, unit, False
            )
        wavelength = float(dataset.metadata.get("wavelength_a", 1.540598))
        fwhm_value = phase.metadata.get("fwhm_deg", 0.15)
        fwhm = 0.15 if fwhm_value is None else float(fwhm_value)
        _x, y = self._experimental_arrays(dataset)
        phase_scale = max(float(np.nanmax(y) - np.nanmin(y)) * fwhm, 1.0)
        parameters.update(
            {
                "wavelength": FitParameter(
                    "wavelength",
                    wavelength,
                    wavelength * 0.5,
                    wavelength * 1.5,
                    "Å",
                    False,
                ),
                "zero_shift": FitParameter(
                    "zero_shift",
                    float(phase.metadata.get("simulation_zero_shift_deg", 0.0)),
                    -1.0,
                    1.0,
                    "deg 2θ",
                    True,
                ),
                "displacement": FitParameter(
                    "displacement", 0.0, -1.0, 1.0, "deg", False
                ),
                "fwhm": FitParameter(
                    "fwhm", fwhm, 0.001, 5.0, "deg", True
                ),
                "eta": FitParameter("eta", 0.3, 0.0, 1.0, "", False),
                "phase_scale": FitParameter(
                    "phase_scale",
                    phase_scale,
                    0.0,
                    max(phase_scale * 1000, 1.0e6),
                    "intensity·deg",
                    True,
                ),
            }
        )
        return parameters

    def _phase_document(self, phase: Dataset) -> CifDocument:
        original = (
            phase.source_bytes.decode("utf-8", errors="replace")
            if phase.source_bytes is not None
            else str(phase.metadata.get("edited_cif", ""))
        )
        return CifDocument(
            Path(phase.source_filename or f"{phase.display_name}.cif"),
            original,
            str(phase.metadata.get("edited_cif", original)),
        )

    def _reflections(self):
        phase = self._phase()
        if phase is None:
            return ()
        return reflections_from_metadata(
            phase_id=phase.dataset_id,
            phase_name=phase.display_name,
            labels=list(phase.metadata.get("stick_hkl_labels", [])),
            positions=list(phase.metadata.get("stick_two_theta_deg", [])),
            d_spacings=list(phase.metadata.get("stick_d_spacing_a", [])),
            intensities=list(phase.metadata.get("stick_intensity", [])),
        )

    def _current_table_parameters(self) -> dict[str, FitParameter]:
        values = {}
        for name, row in self._parameter_rows.items():
            checkbox = self.parameter_table.cellWidget(row, 0)
            current_item = self.parameter_table.item(row, 2)
            lower_item = self.parameter_table.item(row, 4)
            upper_item = self.parameter_table.item(row, 5)
            unit_item = self.parameter_table.item(row, 6)
            status_item = self.parameter_table.item(row, 7)
            assert current_item is not None
            assert lower_item is not None
            assert upper_item is not None
            assert unit_item is not None
            assert status_item is not None
            values[name] = FitParameter(
                name,
                float(current_item.text()),
                float(lower_item.text()),
                float(upper_item.text()),
                unit_item.text(),
                (
                    self._area_refine.get(
                        int(name.removeprefix("area_")), True
                    )
                    if name.startswith("area_")
                    else isinstance(checkbox, QCheckBox) and checkbox.isChecked()
                ),
                status_item.text(),
                self._parameter_errors.get(name),
            )
        return values

    def _rebuild_parameters(
        self, supplied: dict[str, FitParameter] | None = None
    ) -> None:
        try:
            preserved = (
                self._current_table_parameters()
                if self._parameter_rows and supplied is None
                else supplied or {}
            )
        except (TypeError, ValueError):
            preserved = {}
        parameters = self._initial_parameters()
        parameters.update(
            {
                name: value
                for name, value in preserved.items()
                if name in parameters
            }
        )
        try:
            groups = group_reflections(
                self._reflections(),
                fwhm_deg=parameters["fwhm"].value,
                grouping_factor=self.grouping_factor.value(),
                fit_range=(self.fit_min.value(), self.fit_max.value()),
                excluded_ranges=self._excluded_ranges(),
            )
        except Exception:
            groups = []
        if str(self.intensity_mode.currentData()) == "independent":
            for group in groups:
                name = f"area_{group.group_id}"
                parameters[name] = preserved.get(
                    name,
                    FitParameter(
                        name,
                        max(group.calculated_intensity, 0.0),
                        0.0,
                        1.0e15,
                        "intensity·deg",
                        self._area_refine.get(group.group_id, True),
                    ),
                )
        self._fill_parameter_table(parameters)

    def _fill_parameter_table(
        self, parameters: dict[str, FitParameter]
    ) -> None:
        self._updating_parameters = True
        self.parameter_table.blockSignals(True)
        try:
            self.parameter_table.setRowCount(len(parameters))
            self._parameter_rows.clear()
            self._parameter_errors.clear()
            for row, (name, parameter) in enumerate(parameters.items()):
                self._parameter_rows[name] = row
                self._parameter_errors[name] = parameter.standard_error
                if name.startswith("area_"):
                    group_id = int(name.removeprefix("area_"))
                    self._area_refine[group_id] = parameter.refine
                    refine_item = QTableWidgetItem("Reflection Groups")
                    refine_item.setToolTip(
                        "Choose area refinement in the Reflection Groups table."
                    )
                    refine_item.setFlags(
                        refine_item.flags() & ~Qt.ItemFlag.ItemIsEditable
                    )
                    self.parameter_table.setItem(row, 0, refine_item)
                else:
                    checkbox = QCheckBox()
                    checkbox.setChecked(parameter.refine)
                    checkbox.toggled.connect(self._parameter_checkbox_changed)
                    self.parameter_table.setCellWidget(row, 0, checkbox)
                label = (
                    f"Group {name.removeprefix('area_')} area"
                    if name.startswith("area_")
                    else f"Background c{name.removeprefix('background_')}"
                    if name.startswith("background_")
                    else _PARAMETER_LABELS.get(name, name)
                )
                name_item = QTableWidgetItem(label)
                name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.parameter_table.setItem(row, 1, name_item)
                error = (
                    "—"
                    if parameter.standard_error is None
                    else f"{parameter.standard_error:.6g}"
                )
                for column, value in (
                    (2, f"{parameter.value:.12g}"),
                    (3, error),
                    (4, f"{parameter.lower:.12g}"),
                    (5, f"{parameter.upper:.12g}"),
                    (6, parameter.unit),
                    (7, parameter.status),
                ):
                    item = QTableWidgetItem(value)
                    if column in {3, 6, 7}:
                        item.setFlags(
                            item.flags() & ~Qt.ItemFlag.ItemIsEditable
                        )
                    self.parameter_table.setItem(row, column, item)
        finally:
            self.parameter_table.blockSignals(False)
            self._updating_parameters = False

    def _parameter_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating_parameters or item.column() not in {2, 4, 5}:
            return
        name = next(
            (
                parameter_name
                for parameter_name, row in self._parameter_rows.items()
                if row == item.row()
            ),
            None,
        )
        if name is not None:
            self._parameter_errors[name] = None
            error_item = self.parameter_table.item(item.row(), 3)
            if error_item is not None:
                error_item.setText("—")
        self._parameters_modified()

    def _parameter_checkbox_changed(self) -> None:
        if not self._updating_parameters:
            self._parameters_modified()

    def _parameters_modified(self) -> None:
        self._result = None
        self._set_result_actions(False)
        for name, row in self._parameter_rows.items():
            self._parameter_errors[name] = None
            error_item = self.parameter_table.item(row, 3)
            if error_item is not None and error_item.text() != "—":
                error_item.setText("—")
        self.progress_label.setText("Preview updated")
        self._preview_timer.start()

    def _configuration(self) -> FitConfiguration:
        dataset, phase = self._dataset(), self._phase()
        if dataset is None or phase is None or not self.phase_enabled.isChecked():
            raise ValueError("Select and enable an experimental dataset and CIF phase.")
        x, y = self._experimental_arrays(dataset)
        document = self._phase_document(phase)
        cell, _sites, _warnings = extract_cif_tables(document)
        symmetry = extract_cif_symmetry(document)
        current_background = dataset.processed_data.get("background")
        if current_background is not None:
            normalization = NormalizationMode(
                str(dataset.metadata.get("normalization", "none"))
            )
            scale = float(dataset.metadata.get("scale", 1.0))
            offset = float(dataset.metadata.get("offset", 0.0))
            raw = np.asarray(dataset.array("intensity"), dtype=float)
            if dataset.metadata.get("background_enabled", False):
                current_background = np.full_like(raw, offset)
            else:
                current_background = (
                    np.asarray(current_background, dtype=float)
                    * normalization_factor(x, raw, normalization)
                    * scale
                    + offset
                )
        else:
            current_background = np.zeros_like(y)
        uncertainty = next(
            (
                np.asarray(dataset.array(name), dtype=float)
                for name in ("intensity_error", "uncertainty", "sigma")
                if name in dataset.raw_data or name in dataset.processed_data
            ),
            None,
        )
        return FitConfiguration(
            dataset.dataset_id,
            dataset.display_name,
            phase.dataset_id,
            phase.display_name,
            phase.source_filename,
            x,
            y,
            self._reflections(),
            cell,
            symmetry.crystal_system,
            self._current_table_parameters(),
            (self.fit_min.value(), self.fit_max.value()),
            self._excluded_ranges(),
            str(self.intensity_mode.currentData()),
            self.grouping_factor.value(),
            "current",
            0,
            (
                np.asarray(current_background, dtype=float)
                if current_background is not None
                else None
            ),
            str(self.weighting.currentData()),
            uncertainty,
            str(self.loss.currentData()),
            le_bail_cycles=self.le_bail_cycles.value(),
            radiation=self.radiation.currentData(),
            alpha2_ratio=self.alpha2_ratio.value(),
        )

    def _run_or_stop_fit(self) -> None:
        if self._fit_worker is not None:
            self._request_stop()
        else:
            self._run_fit()

    def _run_fit(self) -> None:
        if not self._background_ready or self._background_busy:
            self.background_status.setText("Step 1: apply arPLS background before opening Step 2.")
            return
        try:
            configuration = self._configuration()
            groups = group_reflections(
                configuration.reflections,
                fwhm_deg=configuration.parameters["fwhm"].value,
                grouping_factor=configuration.grouping_factor,
                fit_range=configuration.fit_range,
                excluded_ranges=configuration.excluded_ranges,
            )
            warnings = correlation_safeguards(configuration, len(groups))
            if warnings:
                response = QMessageBox.question(
                    self,
                    "Parameter Correlation Warning",
                    "\n\n".join(warnings) + "\n\nProceed with the fit?",
                )
                if response != QMessageBox.StandardButton.Yes:
                    return
        except Exception as exc:
            show_error(self, "Peak Fitting", str(exc))
            return
        self._pending_fit_state = self.serialize_state()
        self._set_fitting(True)
        self.status.setPlainText("Le Bail fitting in a background worker…")
        self.progress_bar.setValue(0)
        self.progress_label.setText(
            f"Starting 0/{configuration.le_bail_cycles} cycles"
        )
        self.progress_details.clear()
        cancel_event = Event()
        progress_emitter: Any = None

        def report(progress: FitProgress) -> None:
            if progress_emitter is None:
                return
            percent = round(100 * progress.cycle / progress.total_cycles)
            changes = ", ".join(
                f"{name} {value:+.4g}"
                for name, value in progress.parameter_changes.items()
            )
            detail = (
                f"Cycle {progress.cycle}/{progress.total_cycles} | "
                f"Rwp {progress.r_wp:.6g} | "
                f"max parameter Δ {progress.maximum_parameter_change:.4g} | "
                f"max area Δ {progress.maximum_area_change:.4g}"
            )
            if changes:
                detail += f"\n  {changes}"
            progress_emitter(percent, detail)

        worker = Worker(
            fit_pattern,
            configuration,
            progress_callback=report,
            cancel_event=cancel_event,
        )
        progress_emitter = worker.signals.progress.emit
        worker.signals.progress.connect(self._fit_progress)
        worker.signals.result.connect(self._fit_finished)
        worker.signals.error.connect(self._fit_error)
        worker.signals.finished.connect(lambda: self._set_fitting(False))
        self._fit_cancel_event = cancel_event
        self._fit_worker = worker
        QThreadPool.globalInstance().start(worker)

    def _request_stop(self) -> None:
        if self._fit_cancel_event is None:
            return
        self._fit_cancel_event.set()
        self.run_button.setText("Stopping…")
        self.run_button.setEnabled(False)
        self.progress_label.setText("Stopping after the current calculation…")

    def _fit_progress(self, percent: int, detail: str) -> None:
        self.progress_bar.setValue(percent)
        first_line = detail.splitlines()[0]
        self.progress_label.setText(first_line)
        self.progress_details.appendPlainText(detail)

    def _fit_finished(self, result: object) -> None:
        if not isinstance(result, PeakFitResult):
            self._fit_error("The fitting worker returned an invalid result.", "")
            return
        self._result = result
        if self._pending_fit_state is not None:
            self._undo_fit_state = self._pending_fit_state
            self.undo_fit_button.setEnabled(True)
        self._pending_fit_state = None
        self._rebuild_parameters(result.parameters)
        self._fill_reflection_table(result)
        self._refresh_plots()
        self._set_result_actions(True)
        statistics = result.statistics
        self.progress_bar.setValue(100)
        self.progress_label.setText(
            f"Completed {statistics.cycles} Le Bail cycles"
        )
        lines = [
            f"Success: {statistics.success}",
            statistics.message,
            f"Points: {statistics.data_points}; excluded: {statistics.excluded_points}",
            f"Nonlinear parameters: {statistics.nonlinear_parameters}; "
            f"linear parameters: {statistics.linear_parameters}; "
            f"groups: {statistics.active_groups}",
            f"SSR: {statistics.sum_squared_residuals:.7g}; "
            f"RMS: {statistics.rms_residual:.7g}",
            f"Rp: {statistics.r_p:.7g}; Rwp residual indicator: {statistics.r_wp:.7g}",
            (
                "Reduced χ² residual indicator: "
                + (
                    f"{statistics.reduced_chi_square:.7g}"
                    if statistics.reduced_chi_square is not None
                    else "not meaningful"
                )
            ),
            f"Optimizer evaluations: {statistics.evaluations}",
            f"Le Bail cycles: {statistics.cycles}",
        ]
        if result.warnings:
            lines.extend(["", "Warnings:", *result.warnings])
        self.status.setPlainText("\n".join(lines))
        self.state.modified = True

    def _fit_error(self, message: str, traceback_text: str) -> None:
        self._pending_fit_state = None
        if message == "Fit cancelled by user.":
            self.status.setPlainText(message)
            self.progress_label.setText("Stopped by user")
            self.progress_details.appendPlainText("Fit stopped by user.")
            return
        LOGGER.error("Peak fitting failed: %s\n%s", message, traceback_text)
        self.status.setPlainText(f"Fit failed: {message}")
        show_error(self, "Peak Fitting Error", message)

    def _set_fitting(self, fitting: bool) -> None:
        self.run_button.setText("Stop Fit" if fitting else "Run Fit")
        self.run_button.setProperty("primary", not fitting)
        self.run_button.setProperty("danger", fitting)
        style = self.run_button.style()
        style.unpolish(self.run_button)
        style.polish(self.run_button)
        self.run_button.update()
        self.run_button.setEnabled(True)
        self.undo_fit_button.setEnabled(
            not fitting and self._undo_fit_state is not None
        )
        self.reset_button.setEnabled(not fitting)
        self.parameter_table.setEnabled(not fitting)
        self.le_bail_cycles.setEnabled(not fitting)
        self.dataset_combo.setEnabled(not fitting)
        self.phase_combo.setEnabled(not fitting)
        self.radiation.setEnabled(not fitting)
        self.alpha2_ratio.setEnabled(not fitting)
        self.background_button.setEnabled(not fitting)
        if not fitting:
            self._fit_worker = None
            self._fit_cancel_event = None

    def _undo_last_fit(self) -> None:
        if self._fit_worker is not None or self._undo_fit_state is None:
            return
        state = self._undo_fit_state
        self._undo_fit_state = None
        self.undo_fit_button.setEnabled(False)
        self._result = None
        self._set_result_actions(False)
        self.reflection_table.setRowCount(0)
        self.restore_state(state)
        self.progress_bar.setValue(0)
        self.progress_label.setText("Restored pre-fit state")
        self.progress_details.clear()
        self.status.setPlainText(
            "Restored the complete workspace state from immediately before "
            "the last completed fit."
        )

    def _set_result_actions(self, enabled: bool) -> None:
        self.apply_button.setEnabled(enabled)
        self.save_button.setEnabled(enabled)
        self.figure_button.setEnabled(enabled)

    def _range_spin_changed(self) -> None:
        if not self._updating_range:
            self._range_options_changed()

    def _range_region_changed(self, region: pg.LinearRegionItem) -> None:
        if self._updating_range:
            return
        selected_range = np.asarray(region.getRegion(), dtype=float).reshape(-1)
        lower, upper = selected_range[0], selected_range[1]
        self._updating_range = True
        self.fit_min.setValue(float(lower))
        self.fit_max.setValue(float(upper))
        self._updating_range = False
        self._range_options_changed()

    def _refresh_plots(self) -> None:
        dataset = self._dataset()
        if dataset is None:
            return
        try:
            x, observed = self._experimental_arrays(dataset)
            exclusions = self._excluded_ranges()
        except Exception:
            return
        try:
            calculated, background, groups = preview_pattern(
                self._configuration()
            )
        except Exception:
            if self._result is not None and np.array_equal(self._result.x, x):
                calculated = self._result.calculated
                background = self._result.background
                groups = self._result.groups
            else:
                calculated = np.zeros_like(observed)
                background = np.zeros_like(observed)
                groups = []
        self._preview_groups = groups
        self._fill_reflection_table(groups)
        residual = observed - calculated
        main_spec = PlotSpec(
            "2θ (deg)",
            "Intensity",
            [
                CurveSpec(
                    x,
                    observed,
                    "Observed",
                    PlotStyle(
                        color="#334155",
                        line_width=0.8,
                        marker="none",
                        legend_text="Observed",
                    ),
                ),
                CurveSpec(
                    x,
                    calculated,
                    "Calculated",
                    PlotStyle(
                        color="#DC2626",
                        line_width=1.5,
                        marker="none",
                        legend_text="Calculated",
                    ),
                ),
                CurveSpec(
                    x,
                    background,
                    "Background",
                    PlotStyle(
                        color="#F59E0B",
                        line_style="--",
                        line_width=1.2,
                        marker="none",
                        legend_text="Background",
                    ),
                ),
            ],
            title="Observed, calculated, background, and CIF reflection ticks",
        )
        residual_spec = PlotSpec(
            "2θ (deg)",
            "Observed − calculated",
            [
                CurveSpec(
                    x,
                    residual,
                    "Residual",
                    PlotStyle(color="#475569", line_width=0.9, marker="none"),
                )
            ],
            legend_enabled=False,
        )
        render_plot(self.main_plot, main_spec)
        render_plot(self.residual_plot, residual_spec)
        self.residual_plot.addItem(
            pg.InfiniteLine(
                pos=0,
                angle=0,
                pen=pg.mkPen("#94A3B8", style=Qt.PenStyle.DashLine),
            )
        )
        fit_region = pg.LinearRegionItem(
            values=(self.fit_min.value(), self.fit_max.value()),
            orientation="vertical",
            movable=True,
            brush=pg.mkBrush(37, 99, 235, 18),
            pen=pg.mkPen("#2563EB"),
        )
        fit_region.setZValue(-20)
        fit_region.sigRegionChangeFinished.connect(
            lambda: self._range_region_changed(fit_region)
        )
        self.main_plot.addItem(fit_region)
        residual_fit_region = pg.LinearRegionItem(
            values=(self.fit_min.value(), self.fit_max.value()),
            orientation="vertical",
            movable=False,
            brush=pg.mkBrush(37, 99, 235, 12),
            pen=pg.mkPen("#2563EB"),
        )
        residual_fit_region.setZValue(-20)
        self.residual_plot.addItem(residual_fit_region)
        for lower, upper in exclusions:
            for plot in (self.main_plot, self.residual_plot):
                excluded_region = pg.LinearRegionItem(
                    values=(lower, upper),
                    movable=False,
                    brush=pg.mkBrush(220, 38, 38, 35),
                    pen=pg.mkPen(None),
                )
                excluded_region.setZValue(-10)
                plot.addItem(excluded_region)
        if groups:
            minimum = float(np.nanmin(observed))
            span = max(float(np.nanmax(observed) - minimum), 1.0)
            tick_x = []
            tick_y = []
            for group in groups:
                position = (
                    group.fitted_two_theta_deg
                    if np.isfinite(group.fitted_two_theta_deg)
                    else group.calculated_two_theta_deg
                )
                tick_x.extend([position, position, np.nan])
                tick_y.extend(
                    [minimum - 0.08 * span, minimum - 0.02 * span, np.nan]
                )
            self.main_plot.plot(
                np.asarray(tick_x),
                np.asarray(tick_y),
                pen=pg.mkPen("#2563EB", width=1),
            )
        self.main_plot.addItem(self.reflection_hover)
        self.reflection_hover.hide()
        apply_plot_theme(self.main_plot, self._theme)
        apply_plot_theme(self.residual_plot, self._theme)

    def _fill_reflection_table(
        self, source: PeakFitResult | list[Any]
    ) -> None:
        """Show every currently eligible CIF reflection group and its area control."""
        groups = source.groups if isinstance(source, PeakFitResult) else source
        self._updating_reflections = True
        try:
            self.reflection_table.setRowCount(len(groups))
            independent = str(self.intensity_mode.currentData()) == "independent"
            for row, group in enumerate(groups):
                parameter = (
                    source.parameters.get(f"area_{group.group_id}")
                    if isinstance(source, PeakFitResult)
                    else None
                )
                if parameter is not None:
                    self._area_refine[group.group_id] = parameter.refine
                checkbox = QCheckBox()
                checkbox.setChecked(
                    self._area_refine.get(group.group_id, True) and independent
                )
                checkbox.setEnabled(independent)
                if not independent:
                    checkbox.setToolTip(
                        "Area refinement is controlled by Phase scale in CIF "
                        "intensity-ratio mode."
                    )
                checkbox.toggled.connect(
                    lambda checked, group_id=group.group_id: (
                        self._reflection_area_refine_changed(group_id, checked)
                    )
                )
                self.reflection_table.setCellWidget(row, 0, checkbox)
                hkl = "; ".join(
                    " ".join(str(value) for value in member)
                    for reflection in group.reflections
                    for member in reflection.member_hkls
                )
                values = (
                    str(group.group_id),
                    group.phase_name,
                    hkl,
                    f"{group.calculated_two_theta_deg:.7g}",
                    f"{group.fitted_two_theta_deg:.7g}",
                    f"{group.calculated_d_a:.7g}",
                    f"{group.fitted_d_a:.7g}",
                    f"{group.calculated_intensity:.7g}",
                    f"{group.fitted_area:.7g}",
                    f"{group.fwhm_deg:.7g}",
                    group.status,
                )
                for column, value in enumerate(values, start=1):
                    self.reflection_table.setItem(
                        row, column, QTableWidgetItem(value)
                    )
        finally:
            self._updating_reflections = False

    def _reflection_area_refine_changed(
        self, group_id: int, checked: bool
    ) -> None:
        if self._updating_reflections:
            return
        self._area_refine[group_id] = checked
        self._parameters_modified()

    def _show_reflection_hover(self, position) -> None:  # type: ignore[no-untyped-def]
        plot_item = self.main_plot.getPlotItem()
        if plot_item is None or not plot_item.sceneBoundingRect().contains(position):
            self.reflection_hover.hide()
            return
        groups = self._preview_groups
        if not groups:
            self.reflection_hover.hide()
            return
        view_box = plot_item.getViewBox()
        cursor = view_box.mapSceneToView(position)
        positions = np.asarray(
            [
                group.fitted_two_theta_deg
                if np.isfinite(group.fitted_two_theta_deg)
                else group.calculated_two_theta_deg
                for group in groups
            ]
        )
        index = int(np.argmin(np.abs(positions - cursor.x())))
        scene_peak = view_box.mapViewToScene(
            pg.Point(positions[index], cursor.y())
        )
        if abs(scene_peak.x() - position.x()) > 12:
            self.reflection_hover.hide()
            return
        group = groups[index]
        hkl = "; ".join(
            " ".join(str(value) for value in member)
            for reflection in group.reflections
            for member in reflection.member_hkls
        )
        fitted_position = (
            group.fitted_two_theta_deg
            if np.isfinite(group.fitted_two_theta_deg)
            else group.calculated_two_theta_deg
        )
        fitted_d = (
            group.fitted_d_a
            if np.isfinite(group.fitted_d_a)
            else group.calculated_d_a
        )
        self.reflection_hover.setColor(
            "#E5E7EB" if self._theme == "dark" else "#172033"
        )
        self.reflection_hover.setText(
            f"{group.phase_name} — Group {group.group_id}\n"
            f"hkl: {hkl}\n"
            f"2θ calc/fit: {group.calculated_two_theta_deg:.7g} / "
            f"{fitted_position:.7g} deg\n"
            f"d calc/fit: {group.calculated_d_a:.7g} / {fitted_d:.7g} Å\n"
            f"Icalc: {group.calculated_intensity:.7g}; "
            f"area: {group.fitted_area:.7g}"
        )
        self.reflection_hover.setPos(float(positions[index]), float(cursor.y()))
        self.reflection_hover.show()

    def _reset_initial(self) -> None:
        response = QMessageBox.question(
            self,
            "Reset Peak Fit?",
            "Reset the fit range, exclusions, model options, parameters, and "
            "current fit result to their initial values?",
        )
        if response != QMessageBox.StandardButton.Yes:
            return
        self._result = None
        self._undo_fit_state = None
        self._pending_fit_state = None
        self._area_refine.clear()
        self.undo_fit_button.setEnabled(False)
        self._set_result_actions(False)
        self.exclusions.clear()
        self.background_mode.setCurrentIndex(
            self.background_mode.findData("current")
        )
        self.intensity_mode.setCurrentIndex(
            self.intensity_mode.findData("independent")
        )
        self.grouping_factor.setValue(0.5)
        self.weighting.setCurrentIndex(self.weighting.findData("uniform"))
        self.loss.setCurrentIndex(self.loss.findData("linear"))
        self._set_full_range()
        self._rebuild_parameters({})
        self.status.setPlainText("Restored original CIF and dataset starting values.")
        self._refresh_plots()

    def _apply_result(self) -> None:
        if self._result is None:
            return
        dialog = ApplyPeakFitDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.apply_callback(
            str(self.phase_combo.currentData()), self._result, dialog.selected()
        )
        self.status.appendPlainText(
            "\nSelected fitted values were applied to the in-memory simulation. "
            "Use Save CIF As for a permanent structure file."
        )

    def _save_fit_data(self) -> None:
        if self._result is None:
            return
        name, _selected = QFileDialog.getSaveFileName(
            self,
            "Save Peak Fit Data",
            "xrd_peak_fit.csv",
            "CSV (*.csv);;TSV (*.tsv);;DAT (*.dat);;Igor ITX (*.itx)",
        )
        if not name:
            return
        path = Path(name)
        companions = [
            path.with_name(f"{path.stem}_parameters{path.suffix}"),
            path.with_name(f"{path.stem}_reflections{path.suffix}"),
            path.with_name(f"{path.stem}_analysis_log.txt"),
        ]
        existing = [candidate.name for candidate in companions if candidate.exists()]
        if existing:
            response = QMessageBox.question(
                self,
                "Overwrite Fit Files",
                "The following companion files exist:\n"
                + "\n".join(existing)
                + "\n\nOverwrite them?",
            )
            if response != QMessageBox.StandardButton.Yes:
                return
        context = self._analysis_context()
        metadata = [
            "CIF-constrained whole-pattern peak fitting",
            f"Source dataset: {context['source_dataset']}",
            f"Source CIF: {context['source_cif']}",
            "Residual definition: observed - calculated",
        ]
        try:
            export_fit_data(path, self._result, metadata=metadata)
            export_analysis_log(companions[-1], self._result, context=context)
            self.status.appendPlainText(f"\nSaved fit data to {path}.")
        except Exception as exc:
            show_error(self, "Peak Fit Export", str(exc))

    def _analysis_context(self) -> dict[str, Any]:
        dataset, phase = self._dataset(), self._phase()
        initial_parameters = self._initial_parameters()
        return {
            "source_dataset": dataset.display_name if dataset else "",
            "source_dataset_uuid": dataset.dataset_id if dataset else "",
            "source_cif": phase.source_filename if phase else "",
            "source_phase_uuid": phase.dataset_id if phase else "",
            "fit_range": [self.fit_min.value(), self.fit_max.value()],
            "excluded_ranges": [list(value) for value in self._excluded_ranges()],
            "fitting_model": "CIF-constrained symmetric pseudo-Voigt peak groups",
            "radiation": self.radiation.currentData(),
            "alpha2_ratio": self.alpha2_ratio.value(),
            "shift_model": "2theta_Bragg + zero_shift + displacement*cos(theta_B)",
            "background_model": "current",
            "background_algorithm": "fixed current processed background",
            "background_order": 0,
            "intensity_mode": str(self.intensity_mode.currentData()),
            "weighting_mode": str(self.weighting.currentData()),
            "loss_function": str(self.loss.currentData()),
            "initial_parameters": {
                name: parameter.value
                for name, parameter in initial_parameters.items()
            },
        }

    def _export_figure(self) -> None:
        if self._result is None:
            return
        name, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Peak Fit Figure",
            "xrd_peak_fit.png",
            FIGURE_FILE_FILTERS,
        )
        if not name:
            return
        path = resolve_figure_export_path(name, selected_filter)
        try:
            export_fit_figure(path, self._result)
            self.status.appendPlainText(f"\nExported figure to {path}.")
        except Exception as exc:
            show_error(self, "Peak Fit Figure Export", str(exc))

    def serialize_state(self) -> dict[str, Any]:
        """Return all configuration and optional fitted arrays for a session."""
        try:
            parameters = {
                name: {
                    "name": parameter.name,
                    "value": parameter.value,
                    "lower": parameter.lower,
                    "upper": parameter.upper,
                    "unit": parameter.unit,
                    "refine": parameter.refine,
                    "status": parameter.status,
                    "standard_error": parameter.standard_error,
                }
                for name, parameter in self._current_table_parameters().items()
            }
            exclusions = [list(value) for value in self._excluded_ranges()]
        except Exception:
            parameters = {}
            exclusions = []
        return {
            "selected_dataset_id": self.dataset_combo.currentData(),
            "selected_phase_id": self.phase_combo.currentData(),
            "phase_enabled": self.phase_enabled.isChecked(),
            "fit_range": [self.fit_min.value(), self.fit_max.value()],
            "excluded_ranges": exclusions,
            "parameters": parameters,
            "intensity_mode": self.intensity_mode.currentData(),
            "grouping_factor": self.grouping_factor.value(),
            "le_bail_cycles": self.le_bail_cycles.value(),
            "radiation": self.radiation.currentData(),
            "alpha2_ratio": self.alpha2_ratio.value(),
            "background_mode": "current",
            "background_order": 0,
            "weighting": self.weighting.currentData(),
            "loss": self.loss.currentData(),
            "splitter_sizes": self.splitter.sizes(),
            "last_result": (
                result_to_state(self._result) if self._result is not None else None
            ),
        }

    def restore_state(self, state: dict[str, Any]) -> None:
        """Restore compatible optional state while leaving the main session usable."""
        try:
            dataset_index = self.dataset_combo.findData(
                state.get("selected_dataset_id")
            )
            phase_index = self.phase_combo.findData(state.get("selected_phase_id"))
            if dataset_index >= 0:
                self.dataset_combo.setCurrentIndex(dataset_index)
            if phase_index >= 0:
                self.phase_combo.setCurrentIndex(phase_index)
            self.phase_enabled.setChecked(bool(state.get("phase_enabled", True)))
            fit_range = state.get("fit_range", [])
            if isinstance(fit_range, list) and len(fit_range) == 2:
                self._updating_range = True
                self.fit_min.setValue(float(fit_range[0]))
                self.fit_max.setValue(float(fit_range[1]))
                self._updating_range = False
            excluded = state.get("excluded_ranges", [])
            self.exclusions.setText(
                ", ".join(f"{item[0]}-{item[1]}" for item in excluded)
            )
            for combo, key in (
                (self.intensity_mode, "intensity_mode"),
                (self.background_mode, "background_mode"),
                (self.weighting, "weighting"),
                (self.loss, "loss"),
            ):
                index = combo.findData(state.get(key))
                if index >= 0:
                    combo.setCurrentIndex(index)
            self.grouping_factor.setValue(
                float(state.get("grouping_factor", 0.5))
            )
            self.le_bail_cycles.setValue(
                int(state.get("le_bail_cycles", 20))
            )
            self.background_order.setValue(int(state.get("background_order", 2)))
            self.radiation.setCurrentText(state.get("radiation", "Cu Kα1"))
            self.alpha2_ratio.setValue(float(state.get("alpha2_ratio", .5)))
            stored_parameters = {
                name: FitParameter(**value)
                for name, value in dict(state.get("parameters", {})).items()
            }
            self._rebuild_parameters(stored_parameters)
            sizes = state.get("splitter_sizes")
            if isinstance(sizes, list):
                self.splitter.setSizes([int(value) for value in sizes])
            stored_result = state.get("last_result")
            if isinstance(stored_result, dict):
                self._result = result_from_state(stored_result)
                self._rebuild_parameters(self._result.parameters)
                self._fill_reflection_table(self._result)
                self._set_result_actions(True)
            self._refresh_plots()
        except Exception as exc:
            LOGGER.warning("Ignoring incompatible peak-fitting session state: %s", exc)
            self.status.setPlainText(
                "Saved peak-fitting state was incompatible and was ignored. "
                "The rest of the session remains available."
            )

    def apply_theme(self, theme: str) -> None:
        """Update both fit plots to the application light/dark theme."""
        self._theme = theme if theme in {"light", "dark"} else "light"
        apply_plot_theme(self.main_plot, self._theme)
        apply_plot_theme(self.residual_plot, self._theme)

    def refresh_sources(self) -> None:
        """Refresh source selectors while preserving compatible UUID selections."""
        dataset_id = self.dataset_combo.currentData()
        phase_id = self.phase_combo.currentData()
        self._populate_datasets()
        self._populate_phases()
        dataset_index = self.dataset_combo.findData(dataset_id)
        phase_index = self.phase_combo.findData(phase_id)
        if dataset_index >= 0:
            self.dataset_combo.setCurrentIndex(dataset_index)
        if phase_index >= 0:
            self.phase_combo.setCurrentIndex(phase_index)
        self._initialize_selection()

    def closeEvent(self, event) -> None:
        self._background_stop.set()
        if self._fit_worker is not None:
            self._request_stop()
        self._background_ready = False
        self.steps.setCurrentIndex(0)
        self.steps.setTabEnabled(1, False)
        self.run_button.setEnabled(False)
        super().closeEvent(event)
