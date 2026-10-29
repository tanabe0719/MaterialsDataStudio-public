"""Interactive PPMS Heat Capacity workspace."""

from __future__ import annotations

import dataclasses
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
    QLabel,
    QLineEdit,
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
from materials_data_studio.core.workers import Worker
from materials_data_studio.modules.mpms.formula import calculate_molar_mass, parse_formula
from materials_data_studio.modules.ppms.heat_capacity import (
    DebyeEinsteinFit,
    average_adjacent_heat_capacity,
    convert_to_molar_heat_capacity,
    fit_debye_einstein,
    fit_low_temperature,
    integrate_residual_entropy,
    segment_heat_capacity,
)
from materials_data_studio.modules.ppms.heat_capacity_parser import (
    HeatCapacityParseResult,
    parse_heat_capacity_text,
)
from materials_data_studio.modules.ppms.io import read_ppms_text
from materials_data_studio.plotting.matplotlib_backend import (
    FIGURE_FILE_FILTERS,
    export_figure,
    resolve_figure_export_path,
)
from materials_data_studio.plotting.plot_spec import CurveSpec, PlotSpec
from materials_data_studio.plotting.pyqtgraph_backend import (
    apply_plot_theme,
    create_plot_widget,
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
from materials_data_studio.ui.tasks import blocking_task, run_task
from materials_data_studio.ui.workspace_layout import (
    WorkspaceSplitter,
    install_workspace,
    set_residual_available,
    with_residual_controls,
)


class HeatCapacityWidget(QWidget):
    """Load, segment, fit, integrate, and export PPMS Heat Capacity data."""

    kind = "heat_capacity"

    def __init__(self, state: ProjectState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._workers: set[Worker] = set()
        self.dataset_list = MeasurementTree(
            state,
            ModuleType.PPMS,
            dataset_predicate=lambda dataset: dataset.metadata.get("ppms_kind") == self.kind,
            kind=self.kind,
        )
        self.cp_plot = create_plot_widget()
        self.ct_plot = create_plot_widget()
        self.plot_widget = self.cp_plot
        self.plot_tabs = QTabWidget()
        self.plot_tabs.setDocumentMode(True)
        self.plot_tabs.tabBar().setProperty("navigation", "plot")
        self.plot_tabs.tabBar().setExpanding(False)
        self.plot_tabs.addTab(self.cp_plot, "Cp–T")
        self.plot_tabs.addTab(self.ct_plot, "C/T–T²")
        self.plot_tabs.currentChanged.connect(self._plot_tab_changed)
        self.residual_widget = create_plot_widget()
        self._build_controls()
        from materials_data_studio.ui.branch_controls import BranchControls
        self.branch_controls = BranchControls(self.state, {"low_min": self.low_min, "low_max": self.low_max, "low_weighted": self.low_weighted, "fix_low_gamma": self.fix_low_gamma, "de_min": self.de_min, "de_max": self.de_max, "excluded_ranges": self.excluded_ranges, "entropy_min": self.entropy_min, "entropy_max": self.entropy_max, "einstein_terms": self.einstein_terms, "theta_d": self.theta_d, "theta_e1": self.theta_e1, "theta_e2": self.theta_e2, "weight_d": self.weight_d, "weight_e1": self.weight_e1, "weight_e2": self.weight_e2, "gamma": self.gamma, "fix_gamma": self.fix_gamma, "fix_theta_d": self.fix_theta_d, "fix_theta_e1": self.fix_theta_e1, "fix_theta_e2": self.fix_theta_e2, "fix_weight_d": self.fix_weight_d, "fix_weight_e1": self.fix_weight_e1, "fix_weight_e2": self.fix_weight_e2, "constrain_atoms": self.constrain_atoms, "de_weighted": self.de_weighted})
        self._build_layout()
        from materials_data_studio.ui.analysis_ranges import install_fit_range
        install_fit_range(self.cp_plot, self.de_min, self.de_max, exclusions=self.excluded_ranges)
        install_fit_range(self.ct_plot, self.low_min, self.low_max, to_plot=lambda v: v*v, to_physical=lambda v: np.sqrt(max(0, v)))
        self.dataset_list.current_dataset_changed.connect(self._load_selected)
        self.dataset_list.datasets_changed.connect(self.refresh_plot)
        self.refresh()

    def _spin(
        self, minimum: float, maximum: float, value: float, decimals: int = 4
    ) -> QDoubleSpinBox:
        field = QDoubleSpinBox()
        field.setRange(minimum, maximum)
        field.setDecimals(decimals)
        field.setValue(value)
        return field

    def _build_controls(self) -> None:
        self.mass = self._spin(0, 1e9, 1, 6)
        self.mass_source = QLabel("From file header")
        self.formula = QLineEdit()
        self.calculate_formula = QPushButton("Calculate Formula Values")
        self.calculate_formula.clicked.connect(self._calculate_formula)
        self.molar_mass = self._spin(0, 1e9, 1, 6)
        self.molar_mass_source = QLabel("From file header")
        self.atoms = self._spin(0, 1e6, 1, 4)
        self.atoms_source = QLabel("From file header")
        self.reset_header = QPushButton("Reset to Header Values")
        self.reset_header.clicked.connect(self._reset_header)
        for field in (self.mass, self.molar_mass, self.atoms):
            field.editingFinished.connect(self._save_sample)
        self.formula.editingFinished.connect(self._save_sample)

        self.segment = QComboBox()
        self.segment.currentIndexChanged.connect(self._selected_segment_changed)
        self.segment_start = QSpinBox()
        self.segment_stop = QSpinBox()
        for field in (self.segment_start, self.segment_stop):
            field.setRange(0, 0)
        self.apply_segment_bounds = QPushButton("Apply Manual Segment Bounds")
        self.apply_segment_bounds.clicked.connect(self._apply_segment_bounds)
        self.reset_segment_bounds = QPushButton("Reset Automatic Bounds")
        self.reset_segment_bounds.clicked.connect(self._reset_segment_bounds)
        self.plot_mode = QComboBox()
        self.plot_mode.addItem("Cp vs T", "cp_t")
        self.plot_mode.addItem("C/T vs T²", "cp_over_t")
        self.plot_mode.currentIndexChanged.connect(self.refresh_plot)
        self.unit = QComboBox()
        self.unit.addItem("Raw sample heat capacity (µJ/K)", "raw")
        self.unit.addItem("Molar heat capacity (J mol⁻¹ K⁻¹)", "molar")
        self.unit.currentIndexChanged.connect(self.refresh_plot)
        self.log_y = QCheckBox("Logarithmic y axis")
        self.log_y.toggled.connect(self.refresh_plot)
        self.legend = QCheckBox("Show legend")
        self.legend.setChecked(True)
        self.legend.toggled.connect(self.refresh_plot)
        self.style_color = QPushButton("Choose…")
        self.style_color.clicked.connect(self._choose_style_color)
        self.line_style = StyleCombo("line")
        self.line_style.addItems(["-", "--", ":", "-.", "none"])
        self.marker = StyleCombo("marker")
        self.marker.addItems(["none", "o", "s", "^", "v", "d", "x", "+"])
        self.marker_size = self._spin(0, 50, 6, 2)
        self.marker_fill = ColorEdit("none")
        self.line_style.currentIndexChanged.connect(self._apply_style)
        self.marker.currentIndexChanged.connect(self._apply_style)
        self.marker_size.valueChanged.connect(self._apply_style)
        self.marker_fill.editingFinished.connect(self._apply_style)
        self.figure_width = self._spin(4, 60, 16, 2)
        self.figure_height = self._spin(4, 60, 11, 2)
        self.figure_dpi = QSpinBox()
        self.figure_dpi.setRange(72, 1200)
        self.figure_dpi.setValue(300)
        self.transparent = QCheckBox("Transparent background")

        self.low_min = self._spin(0.001, 1e5, 2)
        self.low_max = self._spin(0.001, 1e5, 10)
        self.low_weighted = QCheckBox("Use experimental uncertainties")
        self.fix_low_gamma = QCheckBox("Fix γ = 0")
        self.run_low = QPushButton("Run Low-T Fit")
        self.run_low.clicked.connect(self._run_low_fit)
        self.low_result = QLabel("No low-temperature fit.")
        self.low_result.setWordWrap(True)
        self.show_low_residual = QCheckBox("Show low-T residuals")
        self.show_low_residual.toggled.connect(self.refresh_plot)

        self.de_min = self._spin(0.001, 1e5, 2)
        self.de_max = self._spin(0.001, 1e5, 300)
        self.einstein_terms = QComboBox()
        for count in range(3):
            self.einstein_terms.addItem(str(count), count)
        self.einstein_terms.setCurrentIndex(2)
        self.theta_d = self._spin(0.001, 1e5, 250)
        self.theta_e1 = self._spin(0.001, 1e5, 450)
        self.theta_e2 = self._spin(0.001, 1e5, 800)
        self.weight_d = self._spin(0, 1e5, 1)
        self.weight_e1 = self._spin(0, 1e5, 1)
        self.weight_e2 = self._spin(0, 1e5, 1)
        self.gamma = self._spin(0, 1e5, 0, 8)
        self.fix_gamma = QCheckBox("Fix γ = 0")
        self.fix_theta_d = QCheckBox("Fix ΘD")
        self.fix_theta_e1 = QCheckBox("Fix ΘE1")
        self.fix_theta_e2 = QCheckBox("Fix ΘE2")
        self.fix_weight_d = QCheckBox("Fix wD")
        self.fix_weight_e1 = QCheckBox("Fix wE1")
        self.fix_weight_e2 = QCheckBox("Fix wE2")
        self.constrain_atoms = QCheckBox(
            "Constrain total oscillator weight to atoms per formula unit"
        )
        self.constrain_atoms.setChecked(True)
        self.de_weighted = QCheckBox("Use experimental uncertainties")
        self.excluded_ranges = QLineEdit()
        self.excluded_ranges.setPlaceholderText("e.g. 45-55, 90-100 K")
        self.run_de = QPushButton("Run Debye–Einstein Fit")
        self.run_de.clicked.connect(self._run_debye_fit)
        self.de_result = QLabel("No Debye–Einstein fit.")
        self.de_result.setWordWrap(True)
        self.show_de_residual = QCheckBox("Show Debye–Einstein residuals")
        self.show_de_residual.setChecked(True)
        self.show_de_residual.toggled.connect(self.refresh_plot)

        self.entropy_min = self._spin(0.001, 1e5, 2)
        self.entropy_max = self._spin(0.001, 1e5, 300)
        self.run_entropy = QPushButton("Integrate ΔCp/T")
        self.run_entropy.clicked.connect(self._run_entropy)
        self.entropy_result = QLabel("No entropy result.")
        self.entropy_result.setWordWrap(True)

    def _build_layout(self) -> None:
        left = QGroupBox("Heat Capacity Datasets")
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(self.dataset_list)
        import_button = QPushButton("Import Heat Capacity DAT")
        import_button.setProperty("primary", True)
        import_button.clicked.connect(self.choose_import_files)
        left_layout.addWidget(import_button)

        center = QSplitter(Qt.Orientation.Vertical)
        center.addWidget(self.plot_tabs)
        center.addWidget(self.residual_widget)
        center.setSizes([620, 230])

        right = QWidget()
        right_layout = QVBoxLayout(right)
        sample_card = QGroupBox("Sample Information")
        sample_form = QFormLayout(sample_card)
        sample_form.addRow("Sample mass (mg)", self.mass)
        sample_form.addRow("Mass source", self.mass_source)
        sample_form.addRow("Chemical formula", self.formula)
        sample_form.addRow(self.calculate_formula)
        sample_form.addRow("Molar mass (g/mol)", self.molar_mass)
        sample_form.addRow("Molar-mass source", self.molar_mass_source)
        sample_form.addRow("Atoms / formula unit", self.atoms)
        sample_form.addRow("Atom-count source", self.atoms_source)
        sample_form.addRow(self.reset_header)
        selection_card = QGroupBox("Data and Plot")
        selection_form = QFormLayout(selection_card)
        selection_form.addRow(QLabel("Select a branch in Datasets. Use the file context menu to edit branches and adjacent-pair averaging."))
        self.segment.hide()
        # Branch bounds are edited in the raw-data branch dialog.
        style_card = QGroupBox("Plot Style")
        style_form = QFormLayout(style_card)
        style_form.addRow("Heat-capacity unit", self.unit)
        style_form.addRow(self.log_y)
        style_form.addRow(self.legend)
        style_form.addRow("Color", self.style_color)
        style_form.addRow("Line style", self.line_style)
        style_form.addRow("Marker", self.marker)
        style_form.addRow("Marker size", self.marker_size)
        style_form.addRow("Marker fill", self.marker_fill)
        low_card = QGroupBox("C/T vs T² Low-T Fit")
        low_form = QFormLayout(low_card)
        low_form.addRow("Minimum T (K)", self.low_min)
        low_form.addRow("Maximum T (K)", self.low_max)
        low_form.addRow(self.low_weighted)
        low_form.addRow(self.fix_low_gamma)
        low_form.addRow(self.run_low)
        low_form.addRow(self.show_low_residual)
        low_form.addRow(self.low_result)
        de_card = QGroupBox("Debye–Einstein Fit")
        de_form = QFormLayout(de_card)
        for label, widget in (
            ("Minimum T (K)", self.de_min),
            ("Maximum T (K)", self.de_max),
            ("Einstein terms", self.einstein_terms),
            ("Initial ΘD (K)", self.theta_d),
            ("Initial ΘE1 (K)", self.theta_e1),
            ("Initial ΘE2 (K)", self.theta_e2),
            ("Initial wD", self.weight_d),
            ("Initial wE1", self.weight_e1),
            ("Initial wE2", self.weight_e2),
            ("Initial γ", self.gamma),
        ):
            de_form.addRow(label, widget)
        for widget in (
            self.fix_gamma,
            self.fix_theta_d,
            self.fix_theta_e1,
            self.fix_theta_e2,
            self.fix_weight_d,
            self.fix_weight_e1,
            self.fix_weight_e2,
            self.constrain_atoms,
            self.de_weighted,
        ):
            de_form.addRow(widget)
        de_form.addRow("Excluded T ranges", self.excluded_ranges)
        de_form.addRow(self.run_de)
        de_form.addRow(self.show_de_residual)
        de_form.addRow(self.de_result)
        entropy_card = QGroupBox("Residual Entropy")
        entropy_form = QFormLayout(entropy_card)
        entropy_form.addRow("Minimum T (K)", self.entropy_min)
        entropy_form.addRow("Maximum T (K)", self.entropy_max)
        entropy_form.addRow(self.run_entropy)
        entropy_form.addRow(self.entropy_result)
        figure_card = QGroupBox("Figure Export")
        figure_form = QFormLayout(figure_card)
        figure_form.addRow("Width (cm)", self.figure_width)
        figure_form.addRow("Height (cm)", self.figure_height)
        figure_form.addRow("DPI", self.figure_dpi)
        figure_form.addRow(self.transparent)
        for card in (
            sample_card,
            selection_card,
            style_card,
            low_card,
            de_card,
            entropy_card,
            figure_card,
        ):
            right_layout.addWidget(card)
        right_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        install_settings_panel(scroll, right, "heat_capacity", "File-shared sample")
        splitter = WorkspaceSplitter()
        splitter.addWidget(left)
        splitter.addWidget(with_residual_controls(center, self.residual_widget, "heat_capacity"))
        splitter.addWidget(scroll)
        splitter.setSizes([280, 850, 360])
        self.splitter = splitter
        layout = QVBoxLayout(self)
        install_workspace(layout, splitter, "heat_capacity")

    def choose_import_files(self) -> None:
        names, _ = QFileDialog.getOpenFileNames(
            self, "Import PPMS Heat Capacity", "", "PPMS DAT (*.dat);;All files (*)"
        )
        if names:
            self.import_files([Path(name) for name in names])

    def import_files(self, paths: list[Path]) -> None:
        for path in paths:
            try:
                source = blocking_task(
                    self,
                    f"Import {path.name}",
                    lambda source_path=path: import_source(
                        source_path, lambda file: parse_heat_capacity_text(read_ppms_text(file))
                    ),
                )
                self._add_result(path, source.result, source)
            except Exception as exc:
                show_error(self, "Heat Capacity Import Error", f"{path.name}: {exc}")

    def _add_result(
        self, path: Path, result: HeatCapacityParseResult, source: ImportedSource | None = None
    ) -> None:
        raw = {
            "timestamp_s": result.timestamp_s,
            "field_oe": result.field_oe,
            "temperature_k": result.temperature_k,
            "cp_raw_uj_k": result.heat_capacity_uj_k,
            "cp_raw_error_uj_k": result.heat_capacity_error_uj_k,
            "source_row_index": result.source_row_index,
            "fit_deviation": result.fit_deviation,
            "sample_coupling": result.sample_coupling,
            "system_status": result.system_status,
        }
        header = dataclasses.asdict(result.metadata)
        dataset = Dataset(
            path.stem,
            ModuleType.PPMS,
            raw,
            source_filename=str(path),
            source_hash=source.sha256 if source else source_sha256(path),
            source_bytes=source.data if source else path.read_bytes(),
            metadata={
                "ppms_kind": self.kind,
                "header": header,
                "sample_mass_mg": result.metadata.sample_mass_mg or 0.0,
                "mass_source": "From file header",
                "formula": "",
                "molar_mass_g_mol": result.metadata.molar_mass_g_mol or 0.0,
                "molar_mass_source": "From file header",
                "atoms_per_formula_unit": result.metadata.atoms_per_formula_unit or 0.0,
                "atoms_source": "From file header",
                "selected_segment": 0,
                "manual_segment_bounds": {},
                "comments": result.comments,
            },
            plot_style=default_plot_style(len(self.dataset_list.datasets()), path.stem),
            warnings=[
                f"Imported {len(result.temperature_k)} measurement rows; "
                f"skipped {result.skipped_rows} informational/error rows.",
                *result.messages,
            ],
        )
        self.state.add_dataset(dataset)
        self.refresh()

    def _current_segment(self, dataset: Dataset):
        if dataset.metadata.get("measurement_parent"):
            from materials_data_studio.modules.ppms.heat_capacity import HeatCapacitySegment
            field = self._data_array(dataset, "field_oe")
            segment = HeatCapacitySegment(0, np.arange(len(field), dtype=np.int64), float(np.mean(field)), float(np.min(field)), float(np.max(field)), "manual branch")
            return segment, [segment]
        temperature = self._data_array(dataset, "temperature_k")
        field = self._data_array(dataset, "field_oe")
        segments = segment_heat_capacity(
            temperature,
            field,
        )
        index = min(int(dataset.metadata.get("selected_segment", 0)), len(segments) - 1)
        selected = segments[index]
        overrides = dataset.metadata.get("manual_segment_bounds", {})
        stored = overrides.get(str(selected.segment_id)) if isinstance(overrides, dict) else None
        if isinstance(stored, list) and len(stored) == 2:
            start = max(0, min(int(stored[0]), len(temperature) - 1))
            stop = max(start, min(int(stored[1]), len(temperature) - 1))
            indices = np.arange(start, stop + 1, dtype=np.int64)
            selected_field = field[indices]
            selected = dataclasses.replace(
                selected,
                indices=indices,
                mean_field_oe=float(np.mean(selected_field)),
                min_field_oe=float(np.min(selected_field)),
                max_field_oe=float(np.max(selected_field)),
                direction="manual field segment",
            )
        return selected, segments

    def _averaged_arrays(self, dataset: Dataset) -> dict[str, np.ndarray]:
        if dataset.metadata.get("measurement_branch", {}).get("average_pairs") is False:
            return {key: np.asarray(value, dtype=float) for key, value in dataset.raw_data.items()}
        if dataset.metadata.get("averaging_revision") != 2:
            if "averaged_temperature_k" in dataset.processed_data:
                dataset.processed_data.clear()
                dataset.analysis_results.clear()
                dataset.metadata["manual_segment_bounds"] = {}
                dataset.warnings.append("Averaging updated: separate magnetic fields and propagated uncertainties. Re-run previous fits; original raw data is unchanged.")
            dataset.metadata["averaging_revision"] = 2
        if "averaged_temperature_k" not in dataset.processed_data:
            temperature = np.asarray(dataset.raw_data["temperature_k"], dtype=float)
            aligned = {
                name: values
                for name, values in dataset.raw_data.items()
                if name != "temperature_k" and np.asarray(values).shape == temperature.shape
            }
            averaged = average_adjacent_heat_capacity(temperature, aligned)
            for name, values in averaged.items():
                dataset.processed_data[f"averaged_{name}"] = values
        return {
            name.removeprefix("averaged_"): np.asarray(values, dtype=float)
            for name, values in dataset.processed_data.items()
            if name.startswith("averaged_")
        }

    def _data_array(self, dataset: Dataset, name: str) -> np.ndarray:
        return self._averaged_arrays(dataset)[name]

    def _molar_arrays(self, dataset: Dataset) -> tuple[np.ndarray, np.ndarray]:
        return convert_to_molar_heat_capacity(
            self._data_array(dataset, "cp_raw_uj_k"),
            self._data_array(dataset, "cp_raw_error_uj_k"),
            sample_mass_mg=float(dataset.metadata.get("sample_mass_mg", 0)),
            molar_mass_g_mol=float(dataset.metadata.get("molar_mass_g_mol", 0)),
        )

    def _load_selected(self, dataset: Dataset | None) -> None:
        self.branch_controls.load(dataset)
        dataset_context(self, dataset)
        from materials_data_studio.ui.branch_controls import branch_context
        branch_context(self, dataset)
        if dataset is None:
            return
        controls = (
            self.mass,
            self.molar_mass,
            self.atoms,
            self.line_style,
            self.marker,
            self.marker_size,
            self.marker_fill,
        )
        for control in controls:
            control.blockSignals(True)
        self.mass.setValue(float(dataset.metadata.get("sample_mass_mg", 0)))
        self.formula.setText(str(dataset.metadata.get("formula", "")))
        self.molar_mass.setValue(float(dataset.metadata.get("molar_mass_g_mol", 0)))
        self.atoms.setValue(float(dataset.metadata.get("atoms_per_formula_unit", 0)))
        self.line_style.setCurrentText(dataset.plot_style.line_style)
        self.marker.setCurrentText(dataset.plot_style.marker)
        self.marker_size.setValue(dataset.plot_style.marker_size)
        self.marker_fill.setText(dataset.plot_style.marker_fill_color or "none")
        self.style_color.setStyleSheet(f"background-color: {dataset.plot_style.color};")
        self.mass_source.setText(str(dataset.metadata.get("mass_source", "")))
        self.molar_mass_source.setText(str(dataset.metadata.get("molar_mass_source", "")))
        self.atoms_source.setText(str(dataset.metadata.get("atoms_source", "")))
        for control in controls:
            control.blockSignals(False)
        self._refresh_segments()
        self._refresh_results(dataset)
        self.refresh_plot()

    def _save_sample(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return
        dataset.metadata.update(
            {
                "sample_mass_mg": self.mass.value(),
                "mass_source": "Manual override",
                "formula": self.formula.text().strip(),
                "molar_mass_g_mol": self.molar_mass.value(),
                "molar_mass_source": "Manual override",
                "atoms_per_formula_unit": self.atoms.value(),
                "atoms_source": "Manual override",
            }
        )
        dataset.processed_data.clear()
        dataset.analysis_results.clear()
        self.state.modified = True
        share_sample(self.state, dataset)
        self._load_selected(dataset)
        self.refresh_plot()

    def _calculate_formula(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return
        try:
            formula = self.formula.text().strip()
            counts = parse_formula(formula)
            self.molar_mass.setValue(calculate_molar_mass(formula))
            self.atoms.setValue(sum(counts.values()))
            dataset.metadata.update(
                {
                    "formula": formula,
                    "molar_mass_g_mol": self.molar_mass.value(),
                    "molar_mass_source": "Calculated from formula",
                    "atoms_per_formula_unit": self.atoms.value(),
                    "atoms_source": "Calculated from formula",
                }
            )
            dataset.processed_data.clear()
            dataset.analysis_results.clear()
            self.state.modified = True
            share_sample(self.state, dataset)
            self._load_selected(dataset)
            self.refresh_plot()
        except Exception as exc:
            show_error(self, "Formula Error", str(exc))

    def _reset_header(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return
        header = dataset.metadata.get("header", {})
        if not isinstance(header, dict):
            return
        dataset.metadata.update(
            {
                "sample_mass_mg": float(header.get("sample_mass_mg") or 0),
                "mass_source": "From file header",
                "molar_mass_g_mol": float(header.get("molar_mass_g_mol") or 0),
                "molar_mass_source": "From file header",
                "atoms_per_formula_unit": float(header.get("atoms_per_formula_unit") or 0),
                "atoms_source": "From file header",
                "formula": "",
            }
        )
        dataset.processed_data.clear()
        dataset.analysis_results.clear()
        self.state.modified = True
        share_sample(self.state, dataset)
        self._load_selected(dataset)
        self.refresh_plot()

    def _refresh_segments(self) -> None:
        dataset = self.dataset_list.current_dataset()
        self.segment.blockSignals(True)
        self.segment.clear()
        if dataset is not None:
            _selected, segments = self._current_segment(dataset)
            for segment in segments:
                self.segment.addItem(segment.label, segment.segment_id)
            self.segment.setCurrentIndex(
                min(int(dataset.metadata.get("selected_segment", 0)), len(segments) - 1)
            )
            selected, _segments = self._current_segment(dataset)
            maximum = len(self._data_array(dataset, "temperature_k")) - 1
            for field in (self.segment_start, self.segment_stop):
                field.setRange(0, maximum)
            self.segment_start.setValue(int(selected.indices[0]))
            self.segment_stop.setValue(int(selected.indices[-1]))
        self.segment.blockSignals(False)

    def _selected_segment_changed(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is not None and self.segment.currentIndex() >= 0:
            dataset.metadata["selected_segment"] = self.segment.currentIndex()
            self.state.modified = True
            self._refresh_segments()
            self.refresh_plot()

    def _apply_segment_bounds(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return
        if self.segment_start.value() > self.segment_stop.value():
            show_warning(
                self,
                "Segment Bounds",
                "The first measurement index must not exceed the last index.",
            )
            return
        overrides = dataset.metadata.setdefault("manual_segment_bounds", {})
        if isinstance(overrides, dict):
            overrides[str(self.segment.currentIndex())] = [
                self.segment_start.value(),
                self.segment_stop.value(),
            ]
        dataset.processed_data.clear()
        dataset.analysis_results.clear()
        self.state.modified = True
        self._refresh_segments()
        self.refresh_plot()

    def _choose_style_color(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return
        color = QColorDialog.getColor(QColor(dataset.plot_style.color), self)
        if color.isValid():
            dataset.plot_style.color = color.name()
            self.style_color.setStyleSheet(f"background-color: {color.name()};")
            self.state.modified = True
            self.dataset_list.refresh_style_indicators()
            self.refresh_plot()

    def _apply_style(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return
        dataset.plot_style.line_style = self.line_style.currentText()
        dataset.plot_style.marker = self.marker.currentText()
        dataset.plot_style.marker_size = self.marker_size.value()
        fill = self.marker_fill.text().strip()
        dataset.plot_style.marker_fill_color = None if fill.lower() == "none" else fill
        self.state.modified = True
        self.dataset_list.refresh_style_indicators()
        self.refresh_plot()

    def _reset_segment_bounds(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return
        overrides = dataset.metadata.get("manual_segment_bounds", {})
        if isinstance(overrides, dict):
            overrides.pop(str(self.segment.currentIndex()), None)
        dataset.processed_data.clear()
        dataset.analysis_results.clear()
        self.state.modified = True
        self._refresh_segments()
        self.refresh_plot()

    def _plot_tab_changed(self, index: int) -> None:
        self.plot_mode.setCurrentIndex(max(0, min(index, self.plot_mode.count() - 1)))
        self.refresh_plot()

    def build_plot_spec(self, mode: str | None = None) -> PlotSpec:
        curves: list[CurveSpec] = []
        mode = mode or ("cp_over_t" if self.plot_tabs.currentIndex() == 1 else "cp_t")
        molar = self.unit.currentData() == "molar"
        for dataset in self.dataset_list.datasets():
            if not dataset.visible or dataset.metadata.get("measurement_container"):
                continue
            dataset.plot_warning = ""
            try:
                segment, _segments = self._current_segment(dataset)
                indices = segment.indices
                temperature = self._data_array(dataset, "temperature_k")
                cp = (
                    self._molar_arrays(dataset)[0]
                    if molar
                    else self._data_array(dataset, "cp_raw_uj_k")
                )
                if mode == "cp_over_t":
                    x = temperature[indices] ** 2
                    y = cp[indices] / temperature[indices]
                else:
                    x, y = temperature[indices], cp[indices]
                curves.append(
                    CurveSpec(
                        x,
                        y,
                        f"{dataset.display_name} — {segment.label}",
                        dataset.plot_style,
                    )
                )
                if molar and mode == "cp_over_t" and "low_t_fit" in dataset.processed_data:
                    fit = np.asarray(dataset.processed_data["low_t_fit"], dtype=float)[indices]
                    bounds = dataset.analysis_results.get("low_temperature", {}).get("temperature_range_k", (-np.inf, np.inf))
                    fit_mask = (temperature[indices] >= bounds[0]) & (temperature[indices] <= bounds[1]) & np.isfinite(fit)
                    style = PlotStyle(
                        color=dataset.plot_style.color,
                        line_style="--",
                        marker="none",
                        legend_text=f"{dataset.display_name} low-T fit",
                    )
                    curves.append(CurveSpec(x[fit_mask], fit[fit_mask], style.legend_text, style))
                if molar and mode == "cp_t" and "debye_einstein_fit" in dataset.processed_data:
                    fit = np.asarray(dataset.processed_data["debye_einstein_fit"], dtype=float)[
                        indices
                    ]
                    style = PlotStyle(
                        color=dataset.plot_style.color,
                        line_style="--",
                        marker="none",
                        legend_text=f"{dataset.display_name} D–E fit",
                    )
                    curves.append(CurveSpec(temperature[indices], fit, style.legend_text, style))
            except Exception as exc:
                dataset.plot_warning = f"Not plotted: {exc}"
                continue
        return PlotSpec(
            "T² (K²)" if mode == "cp_over_t" else "Temperature (K)",
            (
                "Cp/T (J mol⁻¹ K⁻²)"
                if molar and mode == "cp_over_t"
                else "Cp/T (µJ K⁻²)"
                if mode == "cp_over_t"
                else "Cp (J mol⁻¹ K⁻¹)"
                if molar
                else "Sample heat capacity (µJ K⁻¹)"
            ),
            curves,
            legend_enabled=self.legend.isChecked(),
            y_log_scale=self.log_y.isChecked(),
            figure_width_cm=self.figure_width.value(),
            figure_height_cm=self.figure_height.value(),
            dpi=self.figure_dpi.value(),
            transparent=self.transparent.isChecked(),
        )

    def _residual_spec(self) -> PlotSpec:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return PlotSpec("Temperature (K)", "Residual")
        segment, _segments = self._current_segment(dataset)
        indices = segment.indices
        temperature = self._data_array(dataset, "temperature_k")
        curves: list[CurveSpec] = []
        if (
            self.show_de_residual.isChecked()
            and "debye_einstein_residual" in dataset.processed_data
        ):
            residual = np.asarray(dataset.processed_data["debye_einstein_residual"], dtype=float)
            curves.append(
                CurveSpec(
                    temperature[indices],
                    residual[indices],
                    "ΔCp",
                    dataset.plot_style,
                )
            )
        elif self.show_low_residual.isChecked() and "low_t_fit" in dataset.processed_data:
            cp, _error = self._molar_arrays(dataset)
            residual = cp / temperature - np.asarray(
                dataset.processed_data["low_t_fit"], dtype=float
            )
            bounds = dataset.analysis_results.get("low_temperature", {}).get("temperature_range_k", (-np.inf, np.inf))
            mask = (temperature[indices] >= bounds[0]) & (temperature[indices] <= bounds[1]) & np.isfinite(residual[indices])
            curves.append(
                CurveSpec(
                    temperature[indices][mask] ** 2,
                    residual[indices][mask],
                    "Low-T residual",
                    dataset.plot_style,
                )
            )
            return PlotSpec("T² (K²)", "Residual Cp/T", curves, legend_enabled=False)
        return PlotSpec(
            "Temperature (K)",
            "ΔCp (J mol⁻¹ K⁻¹)",
            curves,
            legend_enabled=False,
        )

    def refresh_plot(self) -> None:
        plot = self.plot_tabs.currentWidget()
        if plot is self.ct_plot:
            render_plot(self.ct_plot, self.build_plot_spec("cp_over_t"))
        else:
            render_plot(self.cp_plot, self.build_plot_spec("cp_t"))
        residual = self._residual_spec()
        render_plot(self.residual_widget, residual)
        if residual.curves and residual.x_label == "Temperature (K)":
            self.residual_widget.addItem(
                pg.InfiniteLine(
                    pos=0,
                    angle=0,
                    pen=pg.mkPen("#64748B", style=Qt.PenStyle.DashLine),
                )
            )
            region = pg.LinearRegionItem(
                values=(self.entropy_min.value(), self.entropy_max.value()),
                movable=False,
                brush=pg.mkBrush(37, 99, 235, 35),
                pen=pg.mkPen(None),
            )
            self.residual_widget.addItem(region)
        set_residual_available(self.residual_widget, bool(residual.curves))
        self.dataset_list.refresh_diagnostics()

    def refresh(self) -> None:
        self.dataset_list.refresh()
        self._load_selected(self.dataset_list.current_dataset())
        self.refresh_plot()

    def _run_low_fit(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return
        try:
            segment, _segments = self._current_segment(dataset)
            cp, error = self._molar_arrays(dataset)
            indices = segment.indices
            result = fit_low_temperature(
                self._data_array(dataset, "temperature_k")[indices],
                cp[indices],
                minimum_k=self.low_min.value(),
                maximum_k=self.low_max.value(),
                atoms_per_formula_unit=float(dataset.metadata.get("atoms_per_formula_unit", 0))
                or None,
                error_j_mol_k=error[indices] if self.low_weighted.isChecked() else None,
                fix_gamma_zero=self.fix_low_gamma.isChecked(),
            )
            full = np.full(len(cp), np.nan)
            full[indices] = result.fitted_values
            dataset.processed_data["low_t_fit"] = full
            stored = dataclasses.asdict(result)
            stored.pop("fitted_values")
            stored["segment_id"] = segment.segment_id
            stored["branch"] = segment.direction
            stored["fix_gamma_zero"] = self.fix_low_gamma.isChecked()
            dataset.analysis_results["low_temperature"] = stored
            from materials_data_studio.core.processing import record_operation
            record_operation(dataset, "low_temperature", {"x": "__heat_temperature", "y": "__heat_cp_molar", "sigma": "__heat_sigma" if self.low_weighted.isChecked() else None, "minimum_k": self.low_min.value(), "maximum_k": self.low_max.value(), "fix_gamma_zero": self.fix_low_gamma.isChecked()})
            self.state.modified = True
            self._refresh_results(dataset)
            self.refresh_plot()
        except Exception as exc:
            show_error(self, "Low-T Fit Error", str(exc))

    def _excluded(self) -> tuple[tuple[float, float], ...]:
        text = self.excluded_ranges.text().strip()
        if not text:
            return ()
        ranges = []
        for item in text.split(","):
            cleaned = item.strip().removesuffix("K").strip()
            lower, upper = cleaned.split("-", 1)
            ranges.append((float(lower), float(upper)))
        return tuple(ranges)

    def _run_debye_fit(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return
        try:
            segment, _segments = self._current_segment(dataset)
            cp, error = self._molar_arrays(dataset)
            indices = segment.indices
            initial = {
                "gamma": self.gamma.value(),
                "theta_d": self.theta_d.value(),
                "weight_d": self.weight_d.value(),
                "theta_e1": self.theta_e1.value(),
                "weight_e1": self.weight_e1.value(),
                "theta_e2": self.theta_e2.value(),
                "weight_e2": self.weight_e2.value(),
            }
            fixed = {}
            for checked, name, value in (
                (self.fix_gamma.isChecked(), "gamma", 0.0),
                (self.fix_theta_d.isChecked(), "theta_d", self.theta_d.value()),
                (self.fix_theta_e1.isChecked(), "theta_e1", self.theta_e1.value()),
                (self.fix_theta_e2.isChecked(), "theta_e2", self.theta_e2.value()),
                (self.fix_weight_d.isChecked(), "weight_d", self.weight_d.value()),
                (self.fix_weight_e1.isChecked(), "weight_e1", self.weight_e1.value()),
                (self.fix_weight_e2.isChecked(), "weight_e2", self.weight_e2.value()),
            ):
                if checked:
                    fixed[name] = value
            atoms = float(dataset.metadata.get("atoms_per_formula_unit", 0))
            temperature = self._data_array(dataset, "temperature_k")[indices]
            fit_cp = cp[indices]
            fit_error = error[indices] if self.de_weighted.isChecked() else None
            context = {
                "minimum_k": self.de_min.value(),
                "maximum_k": self.de_max.value(),
                "excluded_ranges": self._excluded(),
            }
            options = {
                **context,
                "n_einstein": int(self.einstein_terms.currentData()),
                "initial": initial,
                "fixed": fixed,
                "errors": fit_error,
                "constrain_atoms": atoms
                if self.constrain_atoms.isChecked() and atoms > 0
                else None,
            }
            cancelled = Event()
            task = run_task(
                self,
                "Debye–Einstein fit",
                lambda: fit_debye_einstein(temperature, fit_cp, **options, cancel_event=cancelled),
                lambda result: self._finish_debye_fit(dataset, indices, result, context, {"x": "__heat_temperature", "y": "__heat_cp_molar", "sigma": "__heat_sigma" if fit_error is not None else None, **{k: v for k, v in options.items() if k not in {"errors", "constrain_atoms"}}, "use_sample_atoms": options["constrain_atoms"] is not None}),
                state=self.state,
                dataset=dataset,
                cancelled=cancelled,
            )
            self.run_de.setEnabled(False)
            task.finished.connect(lambda: self.run_de.setEnabled(True))
        except Exception as exc:
            show_error(self, "Debye–Einstein Fit Error", str(exc))

    def _finish_debye_fit(
        self,
        dataset: Dataset,
        indices: np.ndarray,
        result: object,
        context: dict[str, Any] | None = None,
        recipe_options: dict[str, Any] | None = None,
    ) -> None:
        self.run_de.setEnabled(True)
        if not isinstance(result, DebyeEinsteinFit):
            return
        full_fit = np.full(len(self._data_array(dataset, "temperature_k")), np.nan)
        full_residual = np.full_like(full_fit, np.nan)
        full_fit[indices] = result.fitted_values
        full_residual[indices] = result.residuals
        dataset.processed_data["debye_einstein_fit"] = full_fit
        dataset.processed_data["debye_einstein_residual"] = full_residual
        dataset.analysis_results["debye_einstein"] = {
            "parameters": result.parameters,
            "standard_errors": result.standard_errors,
            "r_squared": result.r_squared,
            "reduced_chi_squared": result.reduced_chi_squared,
            "converged": result.converged,
            "covariance_warning": result.covariance_warning,
            **(
                context
                or {
                    "minimum_k": self.de_min.value(),
                    "maximum_k": self.de_max.value(),
                    "excluded_ranges": self._excluded(),
                }
            ),
        }
        if recipe_options is not None:
            from materials_data_studio.core.processing import record_operation
            record_operation(dataset, "debye_einstein", recipe_options)
        self.state.modified = True
        self._refresh_results(dataset)
        self.refresh_plot()

    def _run_entropy(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None or "debye_einstein_residual" not in dataset.processed_data:
            show_warning(self, "Residual Entropy", "Run a Debye–Einstein fit first.")
            return
        try:
            segment, _segments = self._current_segment(dataset)
            value = integrate_residual_entropy(
                self._data_array(dataset, "temperature_k")[segment.indices],
                dataset.processed_data["debye_einstein_residual"][segment.indices],
                minimum_k=self.entropy_min.value(),
                maximum_k=self.entropy_max.value(),
            )
            dataset.analysis_results["entropy"] = {
                "value_j_mol_k": value,
                "minimum_k": self.entropy_min.value(),
                "maximum_k": self.entropy_max.value(),
                "segment_id": segment.segment_id,
            }
            self.state.modified = True
            self._refresh_results(dataset)
        except Exception as exc:
            show_error(self, "Residual Entropy Error", str(exc))

    def _refresh_results(self, dataset: Dataset) -> None:
        low = dataset.analysis_results.get("low_temperature")
        if isinstance(low, dict):
            theta = low.get("debye_temperature_k")
            self.low_result.setText(
                f"γ = {float(low['gamma_j_mol_k2']):.6g} ± {float(low.get('gamma_error', float('nan'))):.3g} J mol⁻¹ K⁻² "
                f"({float(low['gamma_j_mol_k2']) * 1000:.6g} mJ mol⁻¹ K⁻²)\n"
                f"β = {float(low['beta_j_mol_k4']):.6g} ± {float(low.get('beta_error', float('nan'))):.3g} J mol⁻¹ K⁻⁴\n"
                f"ΘD = {float(theta):.6g} ± {float(low.get('debye_temperature_error_k') or float('nan')):.3g} K"
                if theta is not None
                else "Low-T fit complete; ΘD unavailable because β or atom count is invalid."
            )
        else:
            self.low_result.setText("No low-temperature fit.")
        de = dataset.analysis_results.get("debye_einstein")
        if isinstance(de, dict):
            params = de.get("parameters", {})
            self.de_result.setText(
                "\n".join(f"{name} = {float(value):.6g} ± {float(de.get('standard_errors', {}).get(name, float('nan'))):.3g}" for name, value in params.items())
                + f"\nR² = {float(de.get('r_squared', float('nan'))):.6g}"
                + f"\nConverged: {de.get('converged', 'unknown')}\n{de.get('covariance_warning', '')}"
            )
        else:
            self.de_result.setText("No Debye–Einstein fit.")
        entropy = dataset.analysis_results.get("entropy")
        self.entropy_result.setText(
            f"ΔS = {float(entropy['value_j_mol_k']):.8g} J mol⁻¹ K⁻¹"
            if isinstance(entropy, dict)
            else "No entropy result."
        )

    def export_data(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return
        name, _ = QFileDialog.getSaveFileName(
            self,
            "Export Heat Capacity Data",
            f"{dataset.display_name}.csv",
            "Data (*.csv *.tsv *.dat *.itx)",
        )
        if not name:
            return
        try:
            temperature = self._data_array(dataset, "temperature_k")
            cp, error = self._molar_arrays(dataset)
            segment_ids = np.full(len(temperature), -1.0)
            branch_ids = np.full(len(temperature), -1.0)
            _selected, segments = self._current_segment(dataset)
            for segment in segments:
                segment_ids[segment.indices] = segment.segment_id
                branch_ids[segment.indices] = 1 if segment.direction == "warming" else -1
            nan = np.full(len(temperature), np.nan)
            export_numeric(
                Path(name),
                {
                    "Temperature_K": temperature,
                    "Field_Oe": self._data_array(dataset, "field_oe"),
                    "Cp_raw_uJ_per_K": self._data_array(dataset, "cp_raw_uj_k"),
                    "Cp_raw_error_uJ_per_K": self._data_array(dataset, "cp_raw_error_uj_k"),
                    "Cp_molar_J_per_mol_K": cp,
                    "Cp_molar_error_J_per_mol_K": error,
                    "Temperature_squared_K2": temperature**2,
                    "Original_source_row_start": self._data_array(dataset, "source_row_start"),
                    "Original_source_row_stop": self._data_array(dataset, "source_row_stop"),
                    "Cp_over_T_J_per_mol_K2": cp / temperature,
                    "LowT_fit_value": dataset.processed_data.get("low_t_fit", nan),
                    "Debye_Einstein_fit_J_per_mol_K": dataset.processed_data.get(
                        "debye_einstein_fit", nan
                    ),
                    "Residual_Cp_J_per_mol_K": dataset.processed_data.get(
                        "debye_einstein_residual", nan
                    ),
                    "Residual_Cp_over_T_J_per_mol_K2": dataset.processed_data.get(
                        "debye_einstein_residual", nan
                    )
                    / temperature,
                    "Field_segment": segment_ids,
                    "Temperature_branch": branch_ids,
                },
                metadata=[dataset.display_name, dataset.source_filename],
            )
        except Exception as exc:
            show_error(self, "Heat Capacity Export Error", str(exc))

    def export_figure(self) -> None:
        name, selected_filter = QFileDialog.getSaveFileName(
            self, "Export Heat Capacity Figure", "heat-capacity.png", FIGURE_FILE_FILTERS
        )
        if name:
            try:
                export_figure(
                    resolve_figure_export_path(name, selected_filter),
                    self.build_plot_spec(),
                )
            except Exception as exc:
                show_error(self, "Figure Export Error", str(exc))

    def serialize_state(self) -> dict[str, Any]:
        return {
            "plot_mode": str(self.plot_mode.currentData()),
            "plot_tab": self.plot_tabs.currentIndex(),
            "unit": str(self.unit.currentData()),
            "log_y": self.log_y.isChecked(),
            "legend": self.legend.isChecked(),
            "fix_low_gamma": self.fix_low_gamma.isChecked(),
            "show_low_residual": self.show_low_residual.isChecked(),
            "show_de_residual": self.show_de_residual.isChecked(),
            "figure_width": self.figure_width.value(),
            "figure_height": self.figure_height.value(),
            "figure_dpi": self.figure_dpi.value(),
            "transparent": self.transparent.isChecked(),
            "splitter_sizes": self.splitter.sizes(),
        }

    def restore_state(self, state: dict[str, Any]) -> None:
        for combo, value in (
            (self.plot_mode, state.get("plot_mode")),
            (self.unit, state.get("unit")),
        ):
            index = combo.findData(value)
            if index >= 0:
                combo.setCurrentIndex(index)
        self.log_y.setChecked(bool(state.get("log_y", False)))
        self.legend.setChecked(bool(state.get("legend", True)))
        self.fix_low_gamma.setChecked(bool(state.get("fix_low_gamma", False)))
        self.show_low_residual.setChecked(bool(state.get("show_low_residual", False)))
        self.show_de_residual.setChecked(bool(state.get("show_de_residual", True)))
        tab_index = int(
            state.get(
                "plot_tab",
                1 if state.get("plot_mode") == "cp_over_t" else 0,
            )
        )
        self.plot_tabs.setCurrentIndex(max(0, min(tab_index, 1)))
        self.figure_width.setValue(float(state.get("figure_width", 16)))
        self.figure_height.setValue(float(state.get("figure_height", 11)))
        self.figure_dpi.setValue(int(state.get("figure_dpi", 300)))
        self.transparent.setChecked(bool(state.get("transparent", False)))
        sizes = state.get("splitter_sizes")
        if isinstance(sizes, list):
            self.splitter.setSizes([int(value) for value in sizes])
        self.refresh()

    def apply_theme(self, theme: str) -> None:
        apply_plot_theme(self.cp_plot, theme)
        apply_plot_theme(self.ct_plot, theme)
        apply_plot_theme(self.residual_widget, theme)
