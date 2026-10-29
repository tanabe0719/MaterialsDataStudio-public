"""Interactive MPMS M–T/Curie-Weiss and M–H workspace."""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path
from threading import Event
from typing import Any

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from materials_data_studio.core.export import export_numeric
from materials_data_studio.core.measurement_branches import share_sample
from materials_data_studio.core.models import Dataset, ModuleType, PlotStyle, source_sha256
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.core.source_io import ImportedSource, import_source
from materials_data_studio.core.units import (
    FieldUnit,
    MagnetizationUnit,
    convert_field,
    convert_moment,
)
from materials_data_studio.core.workers import Worker
from materials_data_studio.modules.mpms.curie_weiss import (
    c_from_mu_eff,
    curie_weiss_inverse,
    fit_curie_weiss,
)
from materials_data_studio.modules.mpms.formula import calculate_molar_mass
from materials_data_studio.modules.mpms.mh_analysis import center_field, detect_sweep_segments
from materials_data_studio.modules.mpms.models import BranchSplit, FitResult, SweepSegment
from materials_data_studio.modules.mpms.mt_analysis import (
    automatic_split,
    calculate_susceptibility,
    split_branches,
)
from materials_data_studio.modules.mpms.parser import MpmsParseResult, parse_mpms_dc
from materials_data_studio.plotting.matplotlib_backend import (
    FIGURE_FILE_FILTERS,
    export_figure,
    resolve_figure_export_path,
)
from materials_data_studio.plotting.plot_spec import CurveSpec, PlotSpec
from materials_data_studio.plotting.pyqtgraph_backend import (
    apply_plot_theme,
    create_plot_widget,
    install_legend_toggle,
    render_plot,
)
from materials_data_studio.plotting.styles import default_plot_style
from materials_data_studio.ui.dialogs import show_error, show_warning
from materials_data_studio.ui.measurement_tree import MeasurementTree
from materials_data_studio.ui.numeric_inputs import (
    FlexibleDoubleSpinBox as QDoubleSpinBox,
)
from materials_data_studio.ui.panels import dataset_context, install_settings_panel
from materials_data_studio.ui.style_inputs import ColorEdit, StyleCombo
from materials_data_studio.ui.tasks import run_task
from materials_data_studio.ui.workspace_layout import (
    WorkspaceSplitter,
    install_workspace,
    set_residual_available,
    with_residual_controls,
)

LOGGER = logging.getLogger(__name__)


class MpmsWidget(QWidget):
    """Multi-dataset MPMS workspace backed by testable numerical functions."""

    def __init__(self, state: ProjectState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._workers: set[Worker] = set()
        self.current_spec = PlotSpec("Temperature (K)", "χ⁻¹ (mol Oe emu⁻¹)")
        self.dataset_list = MeasurementTree(state, ModuleType.MPMS)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setProperty("navigation", "mode")
        self.tabs.tabBar().setExpanding(False)
        self.mt_plot = create_plot_widget()
        self.mh_plot = create_plot_widget()
        self.tabs.addTab(self.mt_plot, "M–T / Curie–Weiss")
        self.tabs.addTab(self.mh_plot, "M–H")
        self._build_controls()
        self.legend_context_actions = [
            install_legend_toggle(
                plot,
                self.legend_enabled.setChecked,
                checked=self.legend_enabled.isChecked(),
            )
            for plot in (self.mt_plot, self.mh_plot)
        ]
        self.legend_enabled.toggled.connect(self._sync_legend_context_actions)
        from materials_data_studio.ui.branch_controls import BranchControls
        self.branch_controls = BranchControls(self.state, {"fit_t_min": self.fit_t_min, "fit_t_max": self.fit_t_max, "mu_eff": self.mu_eff, "chi0": self.chi0, "fix_c": self.fix_c, "fix_chi0": self.fix_chi0})
        self._build_layout()
        from materials_data_studio.ui.analysis_ranges import install_fit_range
        install_fit_range(self.mt_plot, self.fit_t_min, self.fit_t_max)
        self.dataset_list.current_dataset_changed.connect(self._load_selected)
        self.dataset_list.datasets_changed.connect(self.refresh_plot)
        self.tabs.currentChanged.connect(self.refresh_plot)
        self.refresh()

    def _build_controls(self) -> None:
        self.formula = QLineEdit()
        self.molar_mass = QDoubleSpinBox()
        self.molar_mass.setRange(0.000001, 1e7)
        self.molar_mass.setDecimals(6)
        self.molar_mass.setValue(1.0)
        calculate = QPushButton("Calculate Molar Mass")
        calculate.clicked.connect(self._calculate_mass)
        self.apply_sample = QPushButton("Apply Sample Information")
        self.apply_sample.clicked.connect(self._apply_sample_information)
        self.weight_label = QLabel("Sample weight: —")
        self.sample_weight = QDoubleSpinBox()
        self.sample_weight.setRange(0.000001, 1e9)
        self.sample_weight.setDecimals(6)

        self.mt_mode = QComboBox()
        self.mt_mode.addItem("χ⁻¹–T", "chi_inverse")
        self.mt_mode.addItem("χ–T", "chi")
        self.mt_mode.addItem("Raw M–T", "moment")
        self.mt_mode.currentIndexChanged.connect(self.refresh_plot)
        self.manual_split = QSpinBox()
        self.manual_split.setRange(-1, 10_000_000)
        self.manual_split.setSpecialValueText("Automatic")
        self.manual_split.setValue(-1)
        self.manual_split.valueChanged.connect(self._apply_manual_split)
        self.fit_branch = QComboBox()
        self.fit_branch.addItems(["FC", "ZFC"])
        self.fit_t_min = QDoubleSpinBox()
        self.fit_t_min.setRange(-1e6, 1e6)
        self.fit_t_min.setValue(100)
        self.fit_t_max = QDoubleSpinBox()
        self.fit_t_max.setRange(-1e6, 1e6)
        self.fit_t_max.setValue(350)
        self.mu_eff = QDoubleSpinBox()
        self.mu_eff.setRange(0, 1000)
        self.mu_eff.setDecimals(6)
        self.mu_eff.setValue(4.9)
        self.chi0 = QDoubleSpinBox()
        self.chi0.setRange(-1e6, 1e6)
        self.chi0.setDecimals(9)
        self.fix_c = QCheckBox("Fix C")
        self.fix_chi0 = QCheckBox("Fix χ₀")
        run_fit = QPushButton("Run Curie-Weiss Fit")
        run_fit.setProperty("primary", True)
        run_fit.clicked.connect(self._run_fit)
        self.fit_result = QPlainTextEdit()
        self.fit_result.setReadOnly(True)
        self.fit_result.setMaximumHeight(190)
        self.residual_plot = create_plot_widget()

        self.field_unit = QComboBox()
        for unit in FieldUnit:
            self.field_unit.addItem(unit.value, unit.value)
        self.magnetization_unit = QComboBox()
        for unit in MagnetizationUnit:
            self.magnetization_unit.addItem(unit.value, unit.value)
        self.center_zero = QCheckBox("Center field around zero")
        self.segment_list = QListWidget()
        self.segment_list.setMinimumHeight(150)
        self.field_unit.currentIndexChanged.connect(self.refresh_plot)
        self.magnetization_unit.currentIndexChanged.connect(self.refresh_plot)
        self.center_zero.toggled.connect(self.refresh_plot)
        self.segment_list.itemChanged.connect(self.refresh_plot)

        self.style_branch = QComboBox()
        self.style_branch.addItems(["ZFC", "FC"])
        self.style_branch.currentTextChanged.connect(self._load_branch_style)
        self.branch_color = QPushButton("Choose…")
        self.branch_color.clicked.connect(self._choose_branch_color)
        self.branch_line_style = StyleCombo("line")
        self.branch_line_style.addItems(["-", "--", ":", "-.", "none"])
        self.branch_line_width = QDoubleSpinBox()
        self.branch_line_width.setRange(0, 20)
        self.branch_line_width.setValue(1.6)
        self.branch_marker = StyleCombo("marker")
        self.branch_marker.addItems(["none", "o", "s", "^", "v", "d", "x", "+"])
        self.branch_marker_size = QDoubleSpinBox()
        self.branch_marker_size.setRange(0, 50)
        self.branch_marker_size.setValue(6)
        self.branch_marker_fill = ColorEdit("#FFFFFF")
        self.branch_legend = QLineEdit()
        for control in (
            self.branch_line_style,
            self.branch_line_width,
            self.branch_marker,
            self.branch_marker_size,
            self.branch_marker_fill,
            self.branch_legend,
        ):
            if isinstance(control, QComboBox):
                control.currentIndexChanged.connect(self._apply_branch_style)
            elif isinstance(control, QLineEdit):
                control.editingFinished.connect(self._apply_branch_style)
            else:
                control.valueChanged.connect(self._apply_branch_style)
        self.reset_branch_style = QPushButton("Reset Branch Style")
        self.reset_branch_style.clicked.connect(self._reset_branch_style)

        self.legend_enabled = QCheckBox("Show legend")
        self.legend_enabled.setChecked(True)
        self.legend_enabled.toggled.connect(self.refresh_plot)
        self.figure_width = QDoubleSpinBox()
        self.figure_height = QDoubleSpinBox()
        for field, value in ((self.figure_width, 14), (self.figure_height, 10)):
            field.setRange(1, 100)
            field.setValue(value)
        self.figure_dpi = QSpinBox()
        self.figure_dpi.setRange(50, 2400)
        self.figure_dpi.setValue(300)
        self.transparent = QCheckBox("Transparent background")
        self.export_current_view = QCheckBox("Export current view")

    def _build_layout(self) -> None:
        left = QGroupBox("Datasets")
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(self.dataset_list)
        import_button = QPushButton("Import MPMS Data")
        import_button.setProperty("primary", True)
        import_button.clicked.connect(self.choose_import_files)
        left_layout.addWidget(import_button)

        plot_splitter = QSplitter(Qt.Orientation.Vertical)
        plot_splitter.addWidget(self.tabs)
        plot_splitter.addWidget(self.residual_plot)
        plot_splitter.setSizes([620, 180])
        center = with_residual_controls(plot_splitter, self.residual_plot, "mpms")

        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        sample_card = QGroupBox("Sample Information")
        sample_form = QFormLayout(sample_card)
        sample_form.addRow(self.weight_label)
        sample_form.addRow("Sample weight (mg)", self.sample_weight)
        sample_form.addRow("Formula", self.formula)
        sample_form.addRow("Molar mass (g/mol)", self.molar_mass)
        calculate = QPushButton("Calculate Molar Mass")
        calculate.clicked.connect(self._calculate_mass)
        sample_form.addRow(calculate)
        sample_form.addRow(self.apply_sample)

        mt_card = QGroupBox("M–T and Curie-Weiss")
        mt_form = QFormLayout(mt_card)
        mt_form.addRow("Plot", self.mt_mode)
        self.manual_split.hide()
        self.fit_branch.hide()
        mt_form.addRow("T min (K)", self.fit_t_min)
        mt_form.addRow("T max (K)", self.fit_t_max)
        mt_form.addRow("Initial μ_eff", self.mu_eff)
        mt_form.addRow("Initial χ₀", self.chi0)
        fixed_row = QHBoxLayout()
        fixed_row.addWidget(self.fix_c)
        fixed_row.addWidget(self.fix_chi0)
        mt_form.addRow(fixed_row)
        run_fit = QPushButton("Run Curie-Weiss Fit")
        run_fit.setProperty("primary", True)
        run_fit.clicked.connect(self._run_fit)
        mt_form.addRow(run_fit)
        mt_form.addRow(self.fit_result)

        mh_card = QGroupBox("M–H Sweeps")
        mh_form = QFormLayout(mh_card)
        mh_form.addRow("Field unit", self.field_unit)
        mh_form.addRow("Vertical unit", self.magnetization_unit)
        mh_form.addRow(self.center_zero)
        self.segment_list.hide()
        style_card = QGroupBox("ZFC / FC Style")
        style_form = QFormLayout(style_card)
        self.style_branch.hide()
        style_form.addRow("Color", self.branch_color)
        style_form.addRow("Line style", self.branch_line_style)
        style_form.addRow("Line width", self.branch_line_width)
        style_form.addRow("Marker", self.branch_marker)
        style_form.addRow("Marker size", self.branch_marker_size)
        style_form.addRow("Marker fill", self.branch_marker_fill)
        style_form.addRow("Legend text", self.branch_legend)
        style_form.addRow(self.reset_branch_style)
        figure_card = QGroupBox("Export")
        figure_form = QFormLayout(figure_card)
        figure_form.addRow(self.legend_enabled)
        figure_form.addRow("Width (cm)", self.figure_width)
        figure_form.addRow("Height (cm)", self.figure_height)
        figure_form.addRow("DPI", self.figure_dpi)
        figure_form.addRow(self.transparent)
        figure_form.addRow(self.export_current_view)
        analysis_log = QPushButton("Export Analysis Log")
        analysis_log.clicked.connect(self.export_analysis_log)
        figure_form.addRow(analysis_log)
        controls_layout.addWidget(sample_card)
        controls_layout.addWidget(mt_card)
        controls_layout.addWidget(mh_card)
        controls_layout.addWidget(style_card)
        controls_layout.addWidget(figure_card)
        controls_layout.addStretch(1)
        controls_scroll = QScrollArea()
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        install_settings_panel(controls_scroll, controls, "mpms", "File-shared sample")

        splitter = WorkspaceSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(center)
        splitter.addWidget(controls_scroll)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 4)
        splitter.setStretchFactor(2, 1)
        splitter.setSizes([280, 900, 340])
        self.splitter = splitter
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        install_workspace(layout, splitter, "mpms")

    def choose_import_files(self) -> None:
        """Open a multi-file MPMS chooser."""
        names, _ = QFileDialog.getOpenFileNames(
            self,
            "Import MPMS Data",
            "",
            "MPMS data (*.dat *.dc.dat);;All files (*)",
        )
        if names:
            self.import_files([Path(name) for name in names])

    def import_files(self, paths: list[Path]) -> None:
        """Import MPMS datasets without letting one failure affect existing data."""
        for path in paths:
            run_task(
                self,
                f"Import {path.name}",
                lambda source=path: import_source(source, parse_mpms_dc),
                lambda result, source=path: self._finish_import(source, result),
                state=self.state,
            )

    def _finish_import(self, path: Path, result: object) -> None:
        """Create a dataset on the GUI thread after background parsing."""
        imported = result if isinstance(result, ImportedSource) else None
        result = imported.result if imported else result
        if not isinstance(result, MpmsParseResult):
            self._import_error(path, "The parser returned an invalid result.", "")
            return
        parsed = result
        split = automatic_split(parsed.temperature_k)
        segments = detect_sweep_segments(parsed.temperature_k, parsed.field_oe)
        dataset = Dataset(
            display_name=path.name.removesuffix(".dc.dat"),
            module_type=ModuleType.MPMS,
            source_filename=str(path),
            source_hash=imported.sha256 if imported else source_sha256(path),
            source_bytes=imported.data if imported else path.read_bytes(),
            raw_data={
                "time_s": parsed.time_s,
                "field_oe": parsed.field_oe,
                "temperature_k": parsed.temperature_k,
                "moment_emu": parsed.long_moment_emu,
            },
            metadata={
                "sample_weight_mg": parsed.sample_weight_mg,
                "molar_mass_g_mol": 1.0,
                "molar_mass_confirmed": False,
                "formula": "",
                "automatic_split_index": split.automatic_index,
                "manual_split_index": None,
                "sweep_segments": [dataclasses.asdict(item) for item in segments],
            },
            warnings=parsed.warnings,
            plot_style=default_plot_style(len(self.dataset_list.datasets()), path.stem),
        )
        self.state.add_dataset(dataset)
        if parsed.invalid_rows:
            LOGGER.warning(
                "%s: imported %d valid rows and rejected %d rows",
                path,
                parsed.valid_rows,
                parsed.invalid_rows,
            )
        self.refresh()

    def _import_error(self, path: Path, message: str, traceback_text: str) -> None:
        LOGGER.error("Import failed for %s: %s\n%s", path, message, traceback_text)
        show_error(self, "MPMS Import Error", f"{path.name}: {message}")

    def _load_selected(self, dataset: Dataset | None) -> None:
        self.branch_controls.load(dataset)
        dataset_context(self, dataset)
        from materials_data_studio.ui.branch_controls import branch_context
        branch_context(self, dataset)
        if dataset is None:
            return
        controls = (self.formula, self.molar_mass, self.manual_split)
        for control in controls:
            control.blockSignals(True)
        self.weight_label.setText(
            f"Sample weight: {float(dataset.metadata['sample_weight_mg']):g} mg"
        )
        self.sample_weight.setValue(float(dataset.metadata.get("sample_weight_mg", 1)))
        self.formula.setText(str(dataset.metadata.get("formula", "")))
        self.molar_mass.setValue(float(dataset.metadata.get("molar_mass_g_mol", 1.0)))
        manual = dataset.metadata.get("manual_split_index")
        self.manual_split.setMaximum(len(dataset.raw_data["temperature_k"]) - 1)
        self.manual_split.setValue(-1 if manual is None else int(manual))
        for control in controls:
            control.blockSignals(False)
        fit_data = dataset.analysis_results.get("curie_weiss_fit")
        self.fit_result.setPlainText(
            FitResult(**fit_data).to_text() if isinstance(fit_data, dict) else ""
        )
        self._load_segments(dataset)
        self._load_branch_style()
        residual_curves = []
        if isinstance(fit_data, dict):
            result = FitResult(**fit_data)
            temperature = np.asarray(dataset.raw_data["temperature_k"], dtype=float)
            _, inverse = calculate_susceptibility(dataset.raw_data["moment_emu"], dataset.raw_data["field_oe"],
                sample_weight_mg=float(dataset.metadata["sample_weight_mg"]), molar_mass_g_mol=float(dataset.metadata["molar_mass_g_mol"]))
            if not dataset.metadata.get("measurement_parent"):
                position = 1 if result.branch == "FC" else 0
                split = self._split_for(dataset)
                temperature = np.asarray(split_branches(temperature, split)[position], dtype=float)
                inverse = np.asarray(split_branches(inverse, split)[position], dtype=float)
            mask = (temperature >= result.t_min) & (temperature <= result.t_max)
            residual_curves = [CurveSpec(temperature[mask], inverse[mask] - curie_weiss_inverse(temperature[mask], result.c, result.theta, result.chi0), "Residual", PlotStyle(color="#475569", marker="o", line_style="none"))]
        render_plot(self.residual_plot, PlotSpec("Temperature (K)", "Residual", residual_curves, legend_enabled=False))
        set_residual_available(self.residual_plot, bool(residual_curves))
        self.refresh_plot()

    def _calculate_mass(self) -> None:
        try:
            self.molar_mass.setValue(calculate_molar_mass(self.formula.text()))
        except ValueError as exc:
            show_error(self, "Formula Error", str(exc))

    def _apply_sample_information(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return
        from materials_data_studio.core.processing import invalidate_results
        if dataset.metadata.get("formula", "") != self.formula.text().strip() or float(dataset.metadata.get("molar_mass_g_mol", 1)) != self.molar_mass.value() or float(dataset.metadata.get("sample_weight_mg", 1)) != self.sample_weight.value():
            invalidate_results(dataset, "Sample information changed")
            self.fit_result.clear()
        dataset.metadata["sample_weight_mg"] = self.sample_weight.value()
        dataset.metadata["formula"] = self.formula.text().strip()
        dataset.metadata["molar_mass_g_mol"] = self.molar_mass.value()
        dataset.metadata["molar_mass_confirmed"] = True
        share_sample(self.state, dataset)
        self.state.modified = True
        self.refresh_plot()

    def _apply_manual_split(self, value: int) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return
        from materials_data_studio.core.processing import invalidate_results
        if dataset.metadata.get("manual_split_index") == (None if value < 0 else value):
            return
        invalidate_results(dataset, "ZFC/FC boundary changed")
        self.fit_result.clear()
        dataset.metadata["manual_split_index"] = None if value < 0 else value
        self.state.modified = True
        self.refresh_plot()

    @staticmethod
    def _split_for(dataset: Dataset) -> BranchSplit:
        if dataset.metadata.get("measurement_parent"):
            return BranchSplit(len(dataset.raw_data["temperature_k"]) - 1)
        corrected = automatic_split(dataset.raw_data["temperature_k"]).automatic_index
        # Recompute this derived boundary so sessions saved by versions that
        # included the first descending FC point in ZFC are corrected on load.
        dataset.metadata["automatic_split_index"] = corrected
        return BranchSplit(
            automatic_index=corrected,
            manual_index=dataset.metadata.get("manual_split_index"),
        )

    @staticmethod
    def _branch_style(dataset: Dataset, branch: str) -> PlotStyle:
        if dataset.metadata.get("measurement_parent"):
            return dataclasses.replace(dataset.plot_style)
        stored = dataset.metadata.get(f"{branch.lower()}_style")
        if isinstance(stored, dict):
            return PlotStyle.from_dict(stored)
        if branch == "ZFC":
            return dataclasses.replace(
                dataset.plot_style,
                marker="o",
                marker_fill_color="#FFFFFF",
                legend_text="",
            )
        return dataclasses.replace(
            dataset.plot_style,
            line_style="--",
            marker="s",
            marker_fill_color="#FFFFFF",
            legend_text="",
        )

    def _load_branch_style(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return
        style = self._branch_style(dataset, self.style_branch.currentText())
        controls = (
            self.branch_line_style,
            self.branch_line_width,
            self.branch_marker,
            self.branch_marker_size,
            self.branch_marker_fill,
            self.branch_legend,
        )
        for control in controls:
            control.blockSignals(True)
        self.branch_line_style.setCurrentText(style.line_style)
        self.branch_line_width.setValue(style.line_width)
        self.branch_marker.setCurrentText(style.marker)
        self.branch_marker_size.setValue(style.marker_size)
        self.branch_marker_fill.setText(style.marker_fill_color or "none")
        self.branch_legend.setText(
            style.legend_text or f"{dataset.display_name} {self.style_branch.currentText()}"
        )
        self.branch_color.setStyleSheet(f"background-color: {style.color};")
        for control in controls:
            control.blockSignals(False)

    def _apply_branch_style(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return
        branch = self.style_branch.currentText()
        style = self._branch_style(dataset, branch)
        style.line_style = self.branch_line_style.currentText()
        style.line_width = self.branch_line_width.value()
        style.marker = self.branch_marker.currentText()
        style.marker_size = self.branch_marker_size.value()
        fill = self.branch_marker_fill.text().strip()
        style.marker_fill_color = None if fill.lower() == "none" else fill
        style.legend_text = self.branch_legend.text().strip()
        dataset.metadata[f"{branch.lower()}_style"] = style.to_dict()
        if dataset.metadata.get("measurement_parent"):
            dataset.plot_style = style
        self.state.modified = True
        self.dataset_list.refresh_style_indicators()
        self.refresh_plot()

    def _choose_branch_color(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return
        branch = self.style_branch.currentText()
        style = self._branch_style(dataset, branch)
        color = QColorDialog.getColor(QColor(style.color), self)
        if color.isValid():
            style.color = color.name()
            dataset.metadata[f"{branch.lower()}_style"] = style.to_dict()
            if dataset.metadata.get("measurement_parent"):
                dataset.plot_style = style
            self.branch_color.setStyleSheet(f"background-color: {color.name()};")
            self.state.modified = True
            self.dataset_list.refresh_style_indicators()
            self.refresh_plot()

    def _reset_branch_style(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return
        branch = self.style_branch.currentText()
        if dataset.metadata.get("measurement_parent"):
            dataset.plot_style = default_plot_style(dataset.plot_order, dataset.display_name)
        dataset.metadata.pop(f"{branch.lower()}_style", None)
        self._load_branch_style()
        self.state.modified = True
        self.dataset_list.refresh_style_indicators()
        self.refresh_plot()

    def _mt_spec(self) -> PlotSpec:
        mode = str(self.mt_mode.currentData())
        curves: list[CurveSpec] = []
        y_label = "Signal"
        for dataset in self.state.visible_datasets(ModuleType.MPMS):
            if dataset.metadata.get("measurement_container"):
                continue
            dataset.plot_warning = (
                ""
                if dataset.metadata.get(
                    "molar_mass_confirmed", float(dataset.metadata.get("molar_mass_g_mol", 1)) != 1
                )
                else "Molar mass is provisional (1 g/mol). Calculate and Apply Sample Information before interpreting molar results."
            )
            temperature = np.asarray(dataset.raw_data["temperature_k"], dtype=float)
            moment = np.asarray(dataset.raw_data["moment_emu"], dtype=float)
            if mode == "moment":
                values = moment
                y_label = "Moment (emu)"
            else:
                susceptibility, inverse = calculate_susceptibility(
                    moment,
                    dataset.raw_data["field_oe"],
                    sample_weight_mg=float(dataset.metadata["sample_weight_mg"]),
                    molar_mass_g_mol=float(dataset.metadata.get("molar_mass_g_mol", 1.0)),
                )
                values = inverse if mode == "chi_inverse" else susceptibility
                y_label = "χ⁻¹ (mol Oe emu⁻¹)" if mode == "chi_inverse" else "χ (emu mol⁻¹ Oe⁻¹)"
            split = self._split_for(dataset)
            zfc_t_raw, fc_t_raw = split_branches(temperature, split)
            zfc_y_raw, fc_y_raw = split_branches(values, split)
            zfc_t = np.asarray(zfc_t_raw, dtype=float)
            fc_t = np.asarray(fc_t_raw, dtype=float)
            zfc_y = np.asarray(zfc_y_raw, dtype=float)
            fc_y = np.asarray(fc_y_raw, dtype=float)
            if dataset.metadata.get("measurement_branch", {}).get("mode") == "mh":
                continue
            if len(zfc_t):
                curves.append(
                    CurveSpec(
                        zfc_t,
                        zfc_y,
                        dataset.display_name if dataset.metadata.get("measurement_parent") else f"{dataset.display_name} ZFC",
                        self._branch_style(dataset, "ZFC"),
                    )
                )
            if len(fc_t):
                curves.append(
                    CurveSpec(
                        fc_t,
                        fc_y,
                        f"{dataset.display_name} FC",
                        self._branch_style(dataset, "FC"),
                    )
                )
            fit_data = dataset.analysis_results.get("curie_weiss_fit")
            if mode == "chi_inverse" and isinstance(fit_data, dict):
                result = FitResult(**fit_data)
                fit_t = np.linspace(result.t_min, result.t_max, 400)
                fit_style = PlotStyle(
                    color="#DC2626",
                    line_style="--",
                    line_width=2.0,
                    legend_text=f"{dataset.display_name} fit",
                )
                curves.append(
                    CurveSpec(
                        fit_t,
                        curie_weiss_inverse(fit_t, result.c, result.theta, result.chi0),
                        f"{dataset.display_name} fit",
                        fit_style,
                    )
                )
        return PlotSpec(
            "Temperature (K)",
            y_label,
            curves,
            legend_enabled=self.legend_enabled.isChecked(),
            figure_width_cm=self.figure_width.value(),
            figure_height_cm=self.figure_height.value(),
            dpi=self.figure_dpi.value(),
            transparent=self.transparent.isChecked(),
        )

    def _run_fit(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            show_warning(self, "Curie-Weiss Fit", "Select an MPMS dataset first.")
            return
        if self.mt_mode.currentData() != "chi_inverse":
            show_warning(
                self,
                "Curie-Weiss Fit",
                "Legacy Curie-Weiss fitting is available only in χ⁻¹–T mode.",
            )
            return
        try:
            susceptibility, inverse = calculate_susceptibility(
                dataset.raw_data["moment_emu"],
                dataset.raw_data["field_oe"],
                sample_weight_mg=float(dataset.metadata["sample_weight_mg"]),
                molar_mass_g_mol=float(dataset.metadata["molar_mass_g_mol"]),
            )
            del susceptibility
            temperature = np.asarray(dataset.raw_data["temperature_k"], dtype=float)
            split = self._split_for(dataset)
            zfc_t_raw, fc_t_raw = split_branches(temperature, split)
            zfc_y_raw, fc_y_raw = split_branches(inverse, split)
            zfc_t = np.asarray(zfc_t_raw, dtype=float)
            fc_t = np.asarray(fc_t_raw, dtype=float)
            zfc_y = np.asarray(zfc_y_raw, dtype=float)
            fc_y = np.asarray(fc_y_raw, dtype=float)
            branch = self.fit_branch.currentText()
            if dataset.metadata.get("measurement_parent"):
                branch = dataset.display_name
                branch_t, branch_y = temperature, inverse
            else:
                branch_t, branch_y = (fc_t, fc_y) if branch == "FC" else (zfc_t, zfc_y)
            selected = (branch_t >= self.fit_t_min.value()) & (branch_t <= self.fit_t_max.value())
            fit_t, fit_y = branch_t[selected], branch_y[selected]
            options: dict[str, Any] = {
                "c_init": c_from_mu_eff(self.mu_eff.value()),
                "chi0_init": self.chi0.value(),
                "fix_c": self.fix_c.isChecked(),
                "fix_chi0": self.fix_chi0.isChecked(),
                "branch": branch,
            }
            cancelled = Event()
            run_task(
                self,
                "Curie–Weiss fit",
                lambda: fit_curie_weiss(fit_t, fit_y, **options, cancel_event=cancelled),
                lambda result: self._finish_curie_fit(dataset, fit_t, fit_y, result, options),
                state=self.state,
                dataset=dataset,
                cancelled=cancelled,
            )
        except Exception as exc:
            show_error(self, "Curie-Weiss Fit Error", str(exc))

    def _finish_curie_fit(
        self, dataset: Dataset, fit_t: np.ndarray, fit_y: np.ndarray, result: FitResult, options: dict[str, Any] | None = None
    ) -> None:
        dataset.analysis_results["curie_weiss_fit"] = dataclasses.asdict(result)
        dataset.metadata.pop("analysis_status", None)
        from materials_data_studio.core.processing import record_operation
        record_operation(dataset, "curie_weiss", {"x": "__mpms_temperature", "y": "__mpms_inverse", "minimum_k": result.t_min, "maximum_k": result.t_max, **(options or {"c_init": result.c, "theta_init": result.theta, "chi0_init": result.chi0, "branch": result.branch})})
        self.fit_result.setPlainText(result.to_text())
        predicted = curie_weiss_inverse(fit_t, result.c, result.theta, result.chi0)
        residual_spec = PlotSpec(
            "Temperature (K)",
            "Residual",
            [
                CurveSpec(
                    fit_t,
                    fit_y - predicted,
                    "Residual",
                    PlotStyle(color="#475569", marker="o", line_style="none"),
                )
            ],
            legend_enabled=False,
        )
        render_plot(self.residual_plot, residual_spec)
        set_residual_available(self.residual_plot, bool(residual_spec.curves))
        self.state.modified = True
        self.refresh_plot()

    def _load_segments(self, dataset: Dataset) -> None:
        self.segment_list.blockSignals(True)
        self.segment_list.clear()
        for index, data in enumerate(dataset.metadata.get("sweep_segments", [])):
            segment = SweepSegment(**data)
            item = QListWidgetItem(
                f"{segment.nominal_temperature_k:.2f} K · {segment.direction} · "
                f"cycle {segment.cycle_index + 1}"
            )
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.segment_list.addItem(item)
        self.segment_list.blockSignals(False)

    def _mh_spec(self) -> PlotSpec:
        selected_dataset = self.dataset_list.current_dataset()
        field_unit = FieldUnit(str(self.field_unit.currentData()))
        vertical_unit = MagnetizationUnit(str(self.magnetization_unit.currentData()))
        if selected_dataset is None:
            return PlotSpec(f"Field ({field_unit.value})", vertical_unit.value)
        curves: list[CurveSpec] = []
        selected_segment_indices = {
            int(self.segment_list.item(row).data(Qt.ItemDataRole.UserRole))
            for row in range(self.segment_list.count())
            if self.segment_list.item(row).checkState() == Qt.CheckState.Checked
        }
        for dataset in self.state.visible_datasets(ModuleType.MPMS):
            if dataset.metadata.get("measurement_container"):
                continue
            field = np.asarray(dataset.raw_data["field_oe"], dtype=float)
            if self.center_zero.isChecked():
                field = center_field(field)
            display_field = convert_field(field, field_unit)
            display_moment = convert_moment(
                dataset.raw_data["moment_emu"],
                vertical_unit,
                sample_weight_mg=float(dataset.metadata["sample_weight_mg"]),
                molar_mass_g_mol=float(dataset.metadata.get("molar_mass_g_mol", 1.0)),
            )
            if dataset.metadata.get("measurement_parent"):
                if dataset.metadata.get("measurement_branch", {}).get("mode") == "mh":
                    curves.append(CurveSpec(display_field, display_moment, dataset.display_name, dataset.plot_style))
                continue
            for segment_index, segment_data in enumerate(
                dataset.metadata.get("sweep_segments", [])
            ):
                if (
                    dataset.dataset_id == selected_dataset.dataset_id
                    and segment_index not in selected_segment_indices
                ):
                    continue
                segment = SweepSegment(**segment_data)
                selection = slice(segment.start_index, segment.stop_index + 1)
                label = (
                    f"{dataset.display_name} · {segment.nominal_temperature_k:.2f} K · "
                    f"{segment.direction} · cycle {segment.cycle_index + 1}"
                )
                style = default_plot_style(len(curves), label)
                curves.append(
                    CurveSpec(
                        display_field[selection],
                        display_moment[selection],
                        label,
                        style,
                    )
                )
        return PlotSpec(
            f"Field ({field_unit.value})",
            vertical_unit.value,
            curves,
            legend_enabled=self.legend_enabled.isChecked(),
            figure_width_cm=self.figure_width.value(),
            figure_height_cm=self.figure_height.value(),
            dpi=self.figure_dpi.value(),
            transparent=self.transparent.isChecked(),
        )

    def refresh_plot(self) -> None:
        """Refresh the active MPMS sub-tab."""
        if self.tabs.currentIndex() == 0:
            self.current_spec = self._mt_spec()
            render_plot(self.mt_plot, self.current_spec)
            dataset = self.dataset_list.current_dataset()
            if dataset is not None and not dataset.metadata.get("measurement_container") and not dataset.metadata.get("measurement_parent"):
                split = self._split_for(dataset)
                temperature = dataset.raw_data["temperature_k"]
                if len(temperature):
                    self.mt_plot.addItem(
                        pg.InfiniteLine(
                            pos=float(temperature[split.active_index]),
                            angle=90,
                            pen=pg.mkPen("#94A3B8", style=Qt.PenStyle.DotLine),
                            label="ZFC/FC split",
                        )
                    )
        else:
            self.current_spec = self._mh_spec()
            render_plot(self.mh_plot, self.current_spec)
        self.dataset_list.refresh_diagnostics()

    def _sync_legend_context_actions(self, checked: bool) -> None:
        for action in self.legend_context_actions:
            action.blockSignals(True)
            action.setChecked(checked)
            action.blockSignals(False)

    def apply_theme(self, theme: str) -> None:
        """Apply the selected theme to every MPMS plot scene."""
        for plot in (self.mt_plot, self.mh_plot, self.residual_plot):
            apply_plot_theme(plot, theme)

    def refresh(self) -> None:
        """Refresh controls and plots after state replacement."""
        self.dataset_list.refresh()
        self._load_selected(self.dataset_list.current_dataset())
        self.refresh_plot()

    def export_data(self) -> None:
        """Export active plot data with explicit units."""
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            show_warning(self, "Export Data", "Select an MPMS dataset first.")
            return
        path_text, _ = QFileDialog.getSaveFileName(
            self,
            "Export MPMS Data",
            f"{dataset.display_name}.tsv",
            "Tab-separated (*.dat *.tsv);;CSV (*.csv);;Igor Text Wave (*.itx)",
        )
        if not path_text:
            return
        try:
            columns = {
                "Temperature_K": dataset.raw_data["temperature_k"],
                "Field_Oe": dataset.raw_data["field_oe"],
                "Moment_emu": dataset.raw_data["moment_emu"],
            }
            susceptibility, inverse = calculate_susceptibility(
                dataset.raw_data["moment_emu"],
                dataset.raw_data["field_oe"],
                sample_weight_mg=float(dataset.metadata["sample_weight_mg"]),
                molar_mass_g_mol=float(dataset.metadata.get("molar_mass_g_mol", 1.0)),
            )
            columns["Chi_emu_mol_Oe"] = susceptibility
            columns["ChiInv_mol_Oe_emu"] = inverse
            split = self._split_for(dataset)
            branch_flag = np.zeros(len(inverse), dtype=float)
            branch_flag[split.active_index + 1 :] = 1
            columns["ZFC_0_FC_1"] = branch_flag
            fit_data = dataset.analysis_results.get("curie_weiss_fit")
            if isinstance(fit_data, dict):
                result = FitResult(**fit_data)
                columns["Fit_value"] = curie_weiss_inverse(
                    dataset.raw_data["temperature_k"],
                    result.c,
                    result.theta,
                    result.chi0,
                )
            export_numeric(Path(path_text), columns, metadata=[dataset.display_name])
        except Exception as exc:
            show_error(self, "Export Error", str(exc))

    def export_figure(self) -> None:
        """Export the active MPMS plot with Matplotlib."""
        path_text, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export MPMS Figure",
            "mpms-plot.png",
            FIGURE_FILE_FILTERS,
        )
        if path_text:
            try:
                spec = self.current_spec
                if self.export_current_view.isChecked():
                    active_plot = self.mt_plot if self.tabs.currentIndex() == 0 else self.mh_plot
                    x_range, y_range = active_plot.viewRange()
                    spec = dataclasses.replace(
                        spec,
                        x_range=(float(x_range[0]), float(x_range[1])),
                        y_range=(float(y_range[0]), float(y_range[1])),
                    )
                export_figure(resolve_figure_export_path(path_text, selected_filter), spec)
            except Exception as exc:
                show_error(self, "Figure Export Error", str(exc))

    def export_analysis_log(self) -> None:
        """Export selected sample, split, warnings, and fit provenance as text."""
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            show_warning(self, "Analysis Log", "Select an MPMS dataset first.")
            return
        name, _ = QFileDialog.getSaveFileName(
            self,
            "Export Analysis Log",
            f"{dataset.display_name}.log",
            "Analysis log (*.log *.txt)",
        )
        if not name:
            return
        split = self._split_for(dataset)
        lines = [
            "=== Materials Data Studio MPMS Analysis Log ===",
            f"Dataset: {dataset.display_name}",
            f"Source: {dataset.source_filename}",
            f"Source SHA-256: {dataset.source_hash}",
            f"Sample weight (mg): {dataset.metadata['sample_weight_mg']}",
            f"Formula: {dataset.metadata.get('formula', '')}",
            f"Molar mass (g/mol): {dataset.metadata.get('molar_mass_g_mol', '')}",
            f"Automatic ZFC/FC split row: {split.automatic_index}",
            f"Manual ZFC/FC split row: {split.manual_index}",
            "",
            "[Warnings]",
            *(dataset.warnings or ["None"]),
            "",
            "[Curie-Weiss fit]",
        ]
        fit_data = dataset.analysis_results.get("curie_weiss_fit")
        lines.extend(
            FitResult(**fit_data).to_text().splitlines()
            if isinstance(fit_data, dict)
            else ["Fit was not performed."]
        )
        try:
            Path(name).write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as exc:
            show_error(self, "Analysis Log Error", str(exc))
