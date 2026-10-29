"""Interactive arbitrary-column PPMS DAT workspace."""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from threading import Event
from typing import Any

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from materials_data_studio.core.export import export_numeric
from materials_data_studio.core.models import Dataset, ModuleType, PlotStyle, source_sha256
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.core.source_io import ImportedSource, import_source
from materials_data_studio.modules.mpms.formula import calculate_molar_mass
from materials_data_studio.modules.ppms.generic import (
    evaluate_expression,
    fit_user_expression,
    parse_generic_ppms_text,
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
from materials_data_studio.ui.action_layout import ActionBar, button_role
from materials_data_studio.ui.array_table import ArrayTableModel
from materials_data_studio.ui.dialogs import show_error, show_warning
from materials_data_studio.ui.measurement_tree import MeasurementTree
from materials_data_studio.ui.numeric_inputs import (
    FlexibleDoubleSpinBox as QDoubleSpinBox,
)
from materials_data_studio.ui.panels import dataset_context, install_settings_panel
from materials_data_studio.ui.style_inputs import ColorEdit, StyleCombo
from materials_data_studio.ui.tasks import blocking_task, run_task
from materials_data_studio.ui.workspace_layout import WorkspaceSplitter, install_workspace


class GenericPpmsWidget(QWidget):
    """Read, calculate, edit, plot, and fit arbitrary PPMS columns."""

    kind = "generic"

    def __init__(self, state: ProjectState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self.dataset_list = MeasurementTree(
            state,
            ModuleType.PPMS,
            dataset_predicate=lambda dataset: dataset.metadata.get("ppms_kind") == self.kind,
            kind=self.kind,
        )
        self.plot_widget = create_plot_widget()
        self._build_controls()
        self._build_layout()
        from materials_data_studio.ui.branch_controls import BranchControls
        self.branch_controls = BranchControls(self.state, {"fit_min": self.fit_min, "fit_max": self.fit_max, "fit_formula": self.fit_formula})
        from materials_data_studio.ui.analysis_ranges import install_fit_range
        install_fit_range(self.plot_widget, self.fit_min, self.fit_max)
        self.dataset_list.current_dataset_changed.connect(self._load_selected)
        self.dataset_list.datasets_changed.connect(self.refresh_plot)
        self.refresh()

    @staticmethod
    def _spin(minimum: float, maximum: float, value: float, decimals: int = 6) -> QDoubleSpinBox:
        field = QDoubleSpinBox()
        field.setRange(minimum, maximum)
        field.setDecimals(decimals)
        field.setValue(value)
        return field

    def _build_controls(self) -> None:
        self.sample_weight = self._spin(0, 1e12, 0)
        self.sample_weight.editingFinished.connect(self._save_sample)
        self.formula = QLineEdit()
        self.calculate_formula = QPushButton("Calculate molar amount")
        self.calculate_formula.clicked.connect(self._calculate_formula)
        self.molar_mass_label = QLabel("—")
        self.moles_label = QLabel("—")

        self.columns = QListWidget()
        self.columns.currentItemChanged.connect(self._column_selected)
        self.duplicate_column = QPushButton("Duplicate")
        self.edit_column = QPushButton("Edit data…")
        self.delete_column = QPushButton("Delete")
        self.duplicate_column.clicked.connect(self._duplicate_column)
        self.edit_column.clicked.connect(self._edit_column)
        self.delete_column.clicked.connect(self._delete_column)

        self.new_name = QLineEdit()
        self.new_name.setPlaceholderText("e.g. molar_heat_capacity")
        self.expression = QLineEdit()
        self.expression.setPlaceholderText("e.g. cp_uj_k * 1e-6 / sample_moles")
        self.create_column = QPushButton("Create calculated column")
        self.create_column.clicked.connect(self._create_column)
        self.recalculate = QPushButton("Recalculate dependent columns")
        self.recalculate.clicked.connect(self._recalculate_columns)
        self.variables_label = QPlainTextEdit()
        self.variables_label.setReadOnly(True)
        self.variables_label.setMaximumHeight(100)
        self.variables_label.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.variables_label.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.variables_label.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.variables_label.setPlainText("Import a dataset to see available symbols.")

        self.x_axis = QComboBox()
        self.y_axis = QComboBox()
        self.x_axis.currentIndexChanged.connect(self.refresh_plot)
        self.y_axis.currentIndexChanged.connect(self.refresh_plot)
        self.legend = QCheckBox("Show legend")
        self.legend.setChecked(True)
        self.legend.toggled.connect(self.refresh_plot)
        self.log_y = QCheckBox("Logarithmic y axis")
        self.log_y.toggled.connect(self.refresh_plot)
        self.style_color = QPushButton("Choose…")
        self.style_color.clicked.connect(self._choose_color)
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

        self.fit_formula = QLineEdit("a*x+b")
        self.fit_min = self._spin(-1e15, 1e15, 0)
        self.fit_max = self._spin(-1e15, 1e15, 300)
        self.run_fit = QPushButton("Run curve fit")
        self.run_fit.clicked.connect(self._run_fit)
        self.clear_fit = QPushButton("Clear fit result")
        self.clear_fit.clicked.connect(self._clear_fit)
        self.fit_result = QLabel("No curve fit.")
        self.fit_result.setWordWrap(True)

        self.figure_width = self._spin(4, 60, 16, 2)
        self.figure_height = self._spin(4, 60, 11, 2)
        self.figure_dpi = QSpinBox()
        self.figure_dpi.setRange(72, 1200)
        self.figure_dpi.setValue(300)
        self.transparent = QCheckBox("Transparent background")

    def _build_layout(self) -> None:
        left = QGroupBox("Generic PPMS Datasets")
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(self.dataset_list)
        import_button = QPushButton("Import arbitrary PPMS DAT")
        import_button.setProperty("primary", True)
        import_button.clicked.connect(self.choose_import_files)
        left_layout.addWidget(import_button)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        sample_card = QGroupBox("Sample Information")
        sample_form = QFormLayout(sample_card)
        sample_form.addRow("Sample weight (mg)", self.sample_weight)
        sample_form.addRow("Chemical formula", self.formula)
        sample_form.addRow(self.calculate_formula)
        sample_form.addRow("Molar mass (g/mol)", self.molar_mass_label)
        sample_form.addRow("Sample amount (mol)", self.moles_label)

        columns_card = QGroupBox("Columns")
        columns_layout = QVBoxLayout(columns_card)
        columns_layout.addWidget(self.columns)
        button_role(self.delete_column, "destructive")
        columns_layout.addWidget(ActionBar([[self.edit_column, self.duplicate_column], [self.delete_column]]))

        calculation_card = QGroupBox("Column Calculation")
        calculation_form = QFormLayout(calculation_card)
        calculation_form.addRow("New variable name", self.new_name)
        calculation_form.addRow("Expression", self.expression)
        calculation_form.addRow(self.create_column)
        calculation_form.addRow(self.recalculate)
        button_role(self.create_column, "primary")
        button_role(self.recalculate, "secondary")
        calculation_form.addRow("Available symbols", self.variables_label)

        plot_card = QGroupBox("Plot")
        plot_form = QFormLayout(plot_card)
        plot_form.addRow("x axis", self.x_axis)
        plot_form.addRow("y axis", self.y_axis)
        style_card = QGroupBox("Plot Style")
        style_form = QFormLayout(style_card)
        style_form.addRow(self.log_y)
        style_form.addRow(self.legend)
        style_form.addRow("Color", self.style_color)
        style_form.addRow("Line style", self.line_style)
        style_form.addRow("Marker", self.marker)
        style_form.addRow("Marker size", self.marker_size)
        style_form.addRow("Marker fill", self.marker_fill)

        fit_card = QGroupBox("Curve Fitting")
        fit_form = QFormLayout(fit_card)
        fit_form.addRow("Formula y(x)", self.fit_formula)
        fit_form.addRow("Minimum x", self.fit_min)
        fit_form.addRow("Maximum x", self.fit_max)
        fit_form.addRow(self.run_fit)
        fit_form.addRow(self.clear_fit)
        fit_form.addRow(self.fit_result)

        figure_card = QGroupBox("Figure Export")
        figure_form = QFormLayout(figure_card)
        figure_form.addRow("Width (cm)", self.figure_width)
        figure_form.addRow("Height (cm)", self.figure_height)
        figure_form.addRow("DPI", self.figure_dpi)
        figure_form.addRow(self.transparent)
        for card in (
            sample_card,
            columns_card,
            calculation_card,
            plot_card,
            style_card,
            fit_card,
            figure_card,
        ):
            right_layout.addWidget(card)
        right_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        install_settings_panel(scroll, right, "generic", "Selected dataset / branch")
        splitter = WorkspaceSplitter()
        splitter.addWidget(left)
        splitter.addWidget(self.plot_widget)
        splitter.addWidget(scroll)
        splitter.setSizes([280, 850, 380])
        self.splitter = splitter
        layout = QVBoxLayout(self)
        install_workspace(layout, splitter, "generic")

    def choose_import_files(self) -> None:
        names, _ = QFileDialog.getOpenFileNames(
            self,
            "Import arbitrary PPMS data",
            "",
            "PPMS DAT (*.dat);;All files (*)",
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
                        source_path, lambda file: parse_generic_ppms_text(read_ppms_text(file))
                    ),
                )
                self._add_result(path, source.result, source)
            except Exception as exc:
                show_error(self, "Generic PPMS Import Error", f"{path.name}: {exc}")
        self.refresh()

    def _add_result(self, path: Path, result: Any, source: ImportedSource | None = None) -> None:
        dataset = Dataset(
            path.stem,
            ModuleType.PPMS,
            result.columns,
            source_filename=str(path),
            source_hash=source.sha256 if source else source_sha256(path),
            source_bytes=source.data if source else path.read_bytes(),
            metadata={
                "ppms_kind": self.kind,
                "generic_columns": result.descriptors,
                "sample_weight_mg": result.sample_weight_mg or 0.0,
                "formula": "",
                "molar_mass_g_mol": 0.0,
                "sample_moles": 0.0,
                "header_rows": result.header_rows,
            },
            plot_style=default_plot_style(len(self.dataset_list.datasets()), path.stem),
        )
        self.state.add_dataset(dataset)
        self.refresh()

    @staticmethod
    def _descriptors(dataset: Dataset) -> list[dict[str, Any]]:
        stored = dataset.metadata.get("generic_columns", [])
        return stored if isinstance(stored, list) else []

    @staticmethod
    def _array(dataset: Dataset, descriptor: dict[str, Any]) -> np.ndarray:
        return np.asarray(dataset.array(descriptor["key"]))

    def _numeric_descriptors(self, dataset: Dataset) -> list[dict[str, Any]]:
        return [
            descriptor
            for descriptor in self._descriptors(dataset)
            if descriptor.get("kind") == "numeric"
        ]

    def _load_selected(self, dataset: Dataset | None) -> None:
        self.branch_controls.load(dataset)
        dataset_context(self, dataset)
        from materials_data_studio.ui.branch_controls import branch_context
        branch_context(self, dataset)
        self.columns.clear()
        self.x_axis.clear()
        self.y_axis.clear()
        if dataset is None:
            self.variables_label.setPlainText("Import a dataset to see available symbols.")
            return
        self.sample_weight.setValue(float(dataset.metadata.get("sample_weight_mg", 0)))
        self.formula.setText(str(dataset.metadata.get("formula", "")))
        self._update_sample_labels(dataset)
        for descriptor in self._descriptors(dataset):
            item = QListWidgetItem(
                f"{descriptor.get('name')}  [{descriptor.get('symbol')}]"
                + (" (text)" if descriptor.get("kind") == "text" else "")
            )
            item.setData(Qt.ItemDataRole.UserRole, descriptor.get("key"))
            self.columns.addItem(item)
        for descriptor in self._numeric_descriptors(dataset):
            for combo in (self.x_axis, self.y_axis):
                combo.addItem(descriptor.get("name", ""), descriptor.get("key"))
        selected_x = str(dataset.metadata.get("generic_x", ""))
        selected_y = str(dataset.metadata.get("generic_y", ""))
        for combo, key in ((self.x_axis, selected_x), (self.y_axis, selected_y)):
            index = combo.findData(key)
            if index >= 0:
                combo.setCurrentIndex(index)
        if self.y_axis.count() > 1 and self.y_axis.currentIndex() == 0 and not selected_y:
            self.y_axis.setCurrentIndex(1)
        symbols = [
            descriptor.get("symbol", "") for descriptor in self._numeric_descriptors(dataset)
        ]
        self.variables_label.setPlainText("\n".join([*symbols, "sample_weight_mg", "sample_moles"]))
        self.line_style.setCurrentText(dataset.plot_style.line_style)
        self.marker.setCurrentText(dataset.plot_style.marker)
        self.marker_size.setValue(dataset.plot_style.marker_size)
        self.marker_fill.setText(dataset.plot_style.marker_fill_color or "none")
        self._refresh_fit_result(dataset)
        self.refresh_plot()

    def _save_sample(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return
        dataset.metadata["sample_weight_mg"] = self.sample_weight.value()
        from materials_data_studio.core.processing import invalidate_results
        invalidate_results(dataset, "Sample mass changed")
        self._calculate_formula(show_errors=False)

    def _calculate_formula(self, *, show_errors: bool = True) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return
        try:
            formula = self.formula.text().strip()
            from materials_data_studio.core.processing import invalidate_results
            invalidate_results(dataset, "Sample molar amount changed")
            molar_mass = calculate_molar_mass(formula)
            moles = self.sample_weight.value() * 1e-3 / molar_mass
            dataset.metadata.update(
                {
                    "formula": formula,
                    "sample_weight_mg": self.sample_weight.value(),
                    "molar_mass_g_mol": molar_mass,
                    "sample_moles": moles,
                }
            )
            self.state.modified = True
            self._update_sample_labels(dataset)
            self.dataset_list.refresh_diagnostics()
        except Exception as exc:
            if show_errors:
                show_error(self, "Formula Error", str(exc))

    def _update_sample_labels(self, dataset: Dataset) -> None:
        mass = float(dataset.metadata.get("molar_mass_g_mol", 0))
        moles = float(dataset.metadata.get("sample_moles", 0))
        self.molar_mass_label.setText(f"{mass:.10g}" if mass > 0 else "—")
        self.moles_label.setText(f"{moles:.10g}" if moles > 0 else "—")

    def _descriptor_by_key(self, dataset: Dataset, key: str | None) -> dict[str, Any] | None:
        return next(
            (
                descriptor
                for descriptor in self._descriptors(dataset)
                if descriptor.get("key") == key
            ),
            None,
        )

    def _current_descriptor(self, dataset: Dataset) -> dict[str, Any] | None:
        item = self.columns.currentItem()
        return (
            None
            if item is None
            else self._descriptor_by_key(dataset, str(item.data(Qt.ItemDataRole.UserRole)))
        )

    def _column_selected(self) -> None:
        has_item = self.columns.currentItem() is not None
        self.duplicate_column.setEnabled(has_item)
        self.edit_column.setEnabled(has_item)
        self.delete_column.setEnabled(has_item)

    def _unique_symbol(self, dataset: Dataset, name: str) -> str:
        base = re.sub(r"\W+", "_", name).strip("_").lower() or "calculated"
        if base[0].isdigit():
            base = f"value_{base}"
        used = {item.get("symbol") for item in self._descriptors(dataset)}
        symbol = base
        suffix = 2
        while symbol in used:
            symbol = f"{base}_{suffix}"
            suffix += 1
        return symbol

    def _duplicate_column(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return
        descriptor = self._current_descriptor(dataset)
        if descriptor is None:
            return
        key = f"calculated_{uuid.uuid4().hex}"
        name = f"{descriptor.get('name', 'Column')} copy"
        dataset.processed_data[key] = np.array(self._array(dataset, descriptor), copy=True)
        self._descriptors(dataset).append(
            {
                "key": key,
                "name": name,
                "symbol": self._unique_symbol(dataset, name),
                "kind": descriptor.get("kind", "numeric"),
                "origin": "duplicate",
            }
        )
        self.state.modified = True
        self._load_selected(dataset)

    def _delete_column(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return
        descriptor = self._current_descriptor(dataset)
        if descriptor is None:
            return
        self._descriptors(dataset).remove(descriptor)
        dataset.processed_data.pop(descriptor["key"], None)
        from materials_data_studio.core.processing import invalidate_results
        invalidate_results(dataset, "Input column removed")
        self.state.modified = True
        self._load_selected(dataset)

    def _edit_column(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return
        descriptor = self._current_descriptor(dataset)
        if descriptor is None:
            return
        values = self._array(dataset, descriptor)
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Edit {descriptor.get('name')}")
        dialog.resize(520, 600)
        table = QTableView()
        model = ArrayTableModel(values, table)
        table.setModel(model)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout = QVBoxLayout(dialog)
        layout.addWidget(table)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            edited = model.result()
        except ValueError as exc:
            show_error(self, "Column Edit Error", str(exc))
            dialog.deleteLater()
            return
        dataset.processed_data[descriptor["key"]] = edited
        from materials_data_studio.core.processing import invalidate_results
        invalidate_results(dataset, "Input column edited")
        if descriptor.get("origin") == "calculated":
            descriptor["frozen"] = True
            descriptor.pop("stale", None)
        dataset.analysis_results.pop("generic_fit", None)
        self.state.modified = True
        self.refresh_plot()
        dialog.deleteLater()

    def _create_column(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return
        try:
            name = self.new_name.text().strip()
            if not name:
                raise ValueError("Enter a name for the new variable.")
            variables: dict[str, np.ndarray | float] = {
                descriptor["symbol"]: np.asarray(self._array(dataset, descriptor), dtype=float)
                for descriptor in self._numeric_descriptors(dataset)
            }
            variables["sample_weight_mg"] = float(dataset.metadata.get("sample_weight_mg", 0))
            variables["sample_moles"] = float(dataset.metadata.get("sample_moles", 0))
            result = evaluate_expression(self.expression.text(), variables)
            row_count = len(next(iter(dataset.raw_data.values())))
            if result.ndim == 0:
                result = np.full(row_count, result.item())
            result = np.asarray(result, dtype=float).reshape(-1)
            if len(result) != row_count:
                raise ValueError(
                    f"The expression returned {len(result)} values; expected {row_count}."
                )
            key = f"calculated_{uuid.uuid4().hex}"
            dataset.processed_data[key] = result
            self._descriptors(dataset).append(
                {
                    "key": key,
                    "name": name,
                    "symbol": self._unique_symbol(dataset, name),
                    "kind": "numeric",
                    "origin": "calculated",
                    "expression": self.expression.text(),
                }
            )
            self.state.modified = True
            self.new_name.clear()
            self._load_selected(dataset)
        except Exception as exc:
            show_error(self, "Column Calculation Error", str(exc))

    def _recalculate_columns(self) -> None:
        from materials_data_studio.core.processing import recalculate_columns
        dataset = self.dataset_list.current_dataset()
        if dataset is not None:
            try:
                recalculate_columns(dataset)
                self.state.modified = True
                self._load_selected(dataset)
            except Exception as exc:
                show_error(self, "Column Recalculation", str(exc))

    def _choose_color(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return
        color = QColorDialog.getColor(QColor(dataset.plot_style.color), self)
        if color.isValid():
            dataset.plot_style.color = color.name()
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

    def build_plot_spec(self) -> PlotSpec:
        curves: list[CurveSpec] = []
        x_label = self.x_axis.currentText() or "x"
        y_label = self.y_axis.currentText() or "y"
        active = self.dataset_list.current_dataset()
        for dataset in self.dataset_list.datasets():
            if not dataset.visible or dataset.metadata.get("measurement_container"):
                continue
            x_key = str(
                dataset.metadata.get(
                    "generic_x",
                    self.x_axis.currentData() if dataset is active else "",
                )
            )
            y_key = str(
                dataset.metadata.get(
                    "generic_y",
                    self.y_axis.currentData() if dataset is active else "",
                )
            )
            if dataset is active:
                x_key = str(self.x_axis.currentData() or "")
                y_key = str(self.y_axis.currentData() or "")
                dataset.metadata["generic_x"] = x_key
                dataset.metadata["generic_y"] = y_key
            x_descriptor = self._descriptor_by_key(dataset, x_key)
            y_descriptor = self._descriptor_by_key(dataset, y_key)
            if x_descriptor is None or y_descriptor is None:
                continue
            try:
                x = np.asarray(self._array(dataset, x_descriptor), dtype=float)
                y = np.asarray(self._array(dataset, y_descriptor), dtype=float)
                curves.append(CurveSpec(x, y, dataset.display_name, dataset.plot_style))
                if dataset is active:
                    x_label = x_descriptor["name"]
                    y_label = y_descriptor["name"]
            except (TypeError, ValueError) as exc:
                dataset.plot_warning = f"Not plotted: {exc}"
                continue
        if active is not None and active.visible and not active.metadata.get("measurement_container"):
            fit = active.analysis_results.get("generic_fit")
            if isinstance(fit, dict):
                style = PlotStyle(
                    color=active.plot_style.color,
                    line_style="--",
                    marker="none",
                    legend_text=f"{active.display_name} fit",
                )
                curves.append(
                    CurveSpec(
                        np.asarray(fit.get("x", []), dtype=float),
                        np.asarray(fit.get("y_fit", []), dtype=float),
                        style.legend_text,
                        style,
                    )
                )
        return PlotSpec(
            x_label,
            y_label,
            curves,
            legend_enabled=self.legend.isChecked(),
            y_log_scale=self.log_y.isChecked(),
            figure_width_cm=self.figure_width.value(),
            figure_height_cm=self.figure_height.value(),
            dpi=self.figure_dpi.value(),
            transparent=self.transparent.isChecked(),
        )

    def refresh_plot(self) -> None:
        render_plot(self.plot_widget, self.build_plot_spec())
        self.dataset_list.refresh_diagnostics()

    def _run_fit(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return
        x_descriptor = self._descriptor_by_key(dataset, str(self.x_axis.currentData() or ""))
        y_descriptor = self._descriptor_by_key(dataset, str(self.y_axis.currentData() or ""))
        if x_descriptor is None or y_descriptor is None:
            show_warning(self, "Curve Fit", "Select numeric x and y columns first.")
            return
        if x_descriptor.get("stale") or y_descriptor.get("stale"):
            show_warning(self, "Curve Fit", "Recalculate dependent columns before fitting.")
            return
        try:
            x, y = self._array(dataset, x_descriptor), self._array(dataset, y_descriptor)
            formula, lower, upper = (
                self.fit_formula.text(),
                self.fit_min.value(),
                self.fit_max.value(),
            )
            cancelled = Event()
            run_task(
                self,
                "Curve fit",
                lambda: fit_user_expression(x, y, formula, lower, upper, cancel_event=cancelled),
                lambda fit: self._finish_expression_fit(
                    dataset, x_descriptor, y_descriptor, formula, lower, upper, fit
                ),
                state=self.state,
                dataset=dataset,
                cancelled=cancelled,
            )
        except Exception as exc:
            show_error(self, "Curve Fit Error", str(exc))

    def _finish_expression_fit(
        self,
        dataset: Dataset,
        x_descriptor: dict[str, str],
        y_descriptor: dict[str, str],
        formula: str,
        lower: float,
        upper: float,
        fit: Any,
    ) -> None:
        dataset.analysis_results["generic_fit"] = {
            "formula": formula,
            "minimum_x": lower,
            "maximum_x": upper,
            "parameter_names": fit.parameter_names,
            "parameters": fit.parameters.tolist(),
            "standard_errors": fit.standard_errors.tolist(),
            "r_squared": fit.r_squared,
            "x": fit.x.tolist(),
            "y_fit": fit.y_fit.tolist(),
            "x_key": x_descriptor["key"],
            "y_key": y_descriptor["key"],
        }
        from materials_data_studio.core.processing import record_operation
        record_operation(dataset, "expression_fit", {"x": x_descriptor["key"], "y": y_descriptor["key"], "expression": formula, "minimum_x": lower, "maximum_x": upper})
        self.state.modified = True
        self._refresh_fit_result(dataset)
        self.refresh_plot()

    def _clear_fit(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return
        dataset.analysis_results.pop("generic_fit", None)
        self.state.modified = True
        self._refresh_fit_result(dataset)
        self.refresh_plot()

    def _refresh_fit_result(self, dataset: Dataset) -> None:
        result = dataset.analysis_results.get("generic_fit")
        if not isinstance(result, dict):
            self.fit_result.setText("No curve fit.")
            return
        names = result.get("parameter_names", [])
        values = result.get("parameters", [])
        errors = result.get("standard_errors", [])
        parameters = "; ".join(
            f"{name} = {float(value):.8g} ± {float(error):.3g}"
            for name, value, error in zip(names, values, errors, strict=False)
        )
        self.fit_result.setText(
            f"{result.get('formula', '')}\n{parameters}\n"
            f"R² = {float(result.get('r_squared', np.nan)):.8g}"
        )

    def export_data(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return
        name, _ = QFileDialog.getSaveFileName(
            self,
            "Export Generic PPMS Data",
            f"{dataset.display_name}.csv",
            "Data (*.csv *.tsv *.dat *.itx)",
        )
        if not name:
            return
        try:
            columns = {
                descriptor["name"]: self._array(dataset, descriptor)
                for descriptor in self._numeric_descriptors(dataset)
            }
            export_numeric(
                Path(name),
                columns,
                metadata=[
                    f"sample_weight_mg={dataset.metadata.get('sample_weight_mg', 0)}",
                    f"formula={dataset.metadata.get('formula', '')}",
                    f"sample_moles={dataset.metadata.get('sample_moles', 0)}",
                ],
            )
        except Exception as exc:
            show_error(self, "Generic PPMS Export Error", str(exc))

    def export_figure(self) -> None:
        name, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Generic PPMS Figure",
            "generic-ppms.png",
            FIGURE_FILE_FILTERS,
        )
        if name:
            try:
                export_figure(
                    resolve_figure_export_path(name, selected_filter),
                    self.build_plot_spec(),
                )
            except Exception as exc:
                show_error(self, "Figure Export Error", str(exc))

    def refresh(self) -> None:
        self.dataset_list.refresh()
        self._load_selected(self.dataset_list.current_dataset())

    def serialize_state(self) -> dict[str, Any]:
        return {
            "x_key": self.x_axis.currentData(),
            "y_key": self.y_axis.currentData(),
            "fit_formula": self.fit_formula.text(),
            "fit_min": self.fit_min.value(),
            "fit_max": self.fit_max.value(),
            "legend": self.legend.isChecked(),
            "log_y": self.log_y.isChecked(),
            "figure_width": self.figure_width.value(),
            "figure_height": self.figure_height.value(),
            "figure_dpi": self.figure_dpi.value(),
            "transparent": self.transparent.isChecked(),
            "splitter_sizes": self.splitter.sizes(),
        }

    def restore_state(self, state: dict[str, Any]) -> None:
        self.fit_formula.setText(str(state.get("fit_formula", "a*x+b")))
        self.fit_min.setValue(float(state.get("fit_min", 0)))
        self.fit_max.setValue(float(state.get("fit_max", 300)))
        self.legend.setChecked(bool(state.get("legend", True)))
        self.log_y.setChecked(bool(state.get("log_y", False)))
        self.figure_width.setValue(float(state.get("figure_width", 16)))
        self.figure_height.setValue(float(state.get("figure_height", 11)))
        self.figure_dpi.setValue(int(state.get("figure_dpi", 300)))
        self.transparent.setChecked(bool(state.get("transparent", False)))
        sizes = state.get("splitter_sizes")
        if isinstance(sizes, list):
            self.splitter.setSizes([int(value) for value in sizes])
        self.refresh()
        for combo, key in (
            (self.x_axis, state.get("x_key")),
            (self.y_axis, state.get("y_key")),
        ):
            index = combo.findData(key)
            if index >= 0:
                combo.setCurrentIndex(index)

    def apply_theme(self, theme: str) -> None:
        apply_plot_theme(self.plot_widget, theme)
