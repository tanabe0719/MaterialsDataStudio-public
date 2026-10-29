"""Session-connected sample, result and reproducible workflow workbench."""

from __future__ import annotations

import copy
import csv
import json
import math
import os
import tempfile
from pathlib import Path
from threading import Event
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from materials_data_studio.core.analysis_workflows import (
    capture_recipe,
    read_recipe,
    result_rows,
    run_recipe,
    validate_recipe,
    write_json_atomic,
)
from materials_data_studio.core.models import ModuleType
from materials_data_studio.core.processing import invalidate_results
from materials_data_studio.ui.tasks import blocking_task, run_task


class AnalysisWorkbench(QDialog):
    """Recipes are data, never executable Python/Igor source."""

    def __init__(self, owner: Any) -> None:
        super().__init__(owner)
        self.owner, self.state = owner, owner.state
        self.setWindowTitle("Analysis Workbench — selected session datasets")
        self.resize(1180, 820)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        outer = QVBoxLayout(self)
        self.datasets = QListWidget()
        self.datasets.setMaximumHeight(135)
        for dataset in self.state.datasets:
            item = QListWidgetItem(f"{dataset.display_name} · {dataset.module_type.value}")
            item.setData(Qt.ItemDataRole.UserRole, dataset.dataset_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if dataset.visible else Qt.CheckState.Unchecked)
            self.datasets.addItem(item)
        outer.addWidget(QLabel("Apply to checked datasets below. Changes are local and Undoable; originals are never overwritten."))
        outer.addWidget(self.datasets)
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)
        self._results_page()
        self._recipe_page()
        self._samples_page()
        self._history_page()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        outer.addWidget(buttons)
        self.datasets.itemChanged.connect(lambda _: self.refresh_results())
        self.refresh_results()

    def selected(self):
        ids = {self.datasets.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.datasets.count()) if self.datasets.item(i).checkState() == Qt.CheckState.Checked}
        return [dataset for dataset in self.state.datasets if dataset.dataset_id in ids]

    def button(self, layout, title, callback):
        button = QPushButton(title)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def page(self, title):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.tabs.addTab(page, title)
        return layout

    def _results_page(self):
        layout = self.page("Results & comparison")
        self.stale = QCheckBox("Include superseded results (clearly marked Stale)")
        self.stale.toggled.connect(self.refresh_results)
        layout.addWidget(self.stale)
        self.results = QTableWidget()
        self.results.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.results, 1)
        row = QHBoxLayout()
        self.button(row, "Refresh", self.refresh_results)
        self.button(row, "Copy table", self.copy_results)
        self.button(row, "Export CSV…", self.export_results)
        layout.addLayout(row)
        comparison = QHBoxLayout()
        self.parameter = QComboBox()
        self.condition = QComboBox()
        self.condition.setEditable(True)
        self.condition.addItems(["sample_weight_mg", "sample_mass_mg", "molar_mass_g_mol", "temperature_k", "field_oe"])
        comparison.addWidget(QLabel("Parameter"))
        comparison.addWidget(self.parameter, 1)
        comparison.addWidget(QLabel("vs metadata field"))
        comparison.addWidget(self.condition, 1)
        self.button(comparison, "Create comparison graph", self.compare)
        layout.addLayout(comparison)

    def refresh_results(self):
        if not hasattr(self, "results"):
            return
        self.rows = result_rows(self.selected(), include_stale=self.stale.isChecked())
        headers = ["dataset", "sample", "method", "parameter", "value", "standard_error", "unit", "status", "conditions", "input_sha256"]
        self.results.setColumnCount(len(headers))
        self.results.setHorizontalHeaderLabels(headers)
        self.results.setRowCount(len(self.rows))
        for i, record in enumerate(self.rows):
            for j, key in enumerate(headers):
                value = record[key]
                item = QTableWidgetItem("" if value is None else str(value))
                item.setToolTip(str(record["conditions"]))
                self.results.setItem(i, j, item)
        self.results.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        previous = self.parameter.currentText()
        self.parameter.clear()
        self.parameter.addItems(sorted({f"{row['method']} / {row['parameter']}" for row in self.rows if isinstance(row["value"], (int, float)) and not isinstance(row["value"], bool)}))
        self.parameter.setCurrentText(previous)

    def copy_results(self):
        headers = [item.text() for i in range(self.results.columnCount()) if (item := self.results.horizontalHeaderItem(i)) is not None]
        QApplication.clipboard().setText("\n".join(["\t".join(headers), *("\t".join(str(row.get(k, "")) for k in headers) for row in self.rows)]))

    def export_results(self):
        filename, _ = QFileDialog.getSaveFileName(self, "Export analysis results", "analysis-results.csv", "CSV (*.csv)")
        if filename:
            rows = copy.deepcopy(self.rows)
            headers = [item.text() for i in range(self.results.columnCount()) if (item := self.results.horizontalHeaderItem(i)) is not None]
            def save():
                path = Path(filename)
                descriptor, temporary = tempfile.mkstemp(prefix=".mds-results-", suffix=".csv", dir=path.parent)
                try:
                    with os.fdopen(descriptor, "w", encoding="utf-8-sig", newline="") as stream:
                        writer = csv.DictWriter(stream, fieldnames=headers)
                        writer.writeheader()
                        writer.writerows(rows)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, path)
                finally:
                    Path(temporary).unlink(missing_ok=True)
            try:
                blocking_task(self, "Export analysis results", save, allow_cancel=False)
            except Exception as exc:
                QMessageBox.warning(self, "Export analysis results", str(exc))

    def compare(self):
        import numpy as np

        from materials_data_studio.plotting.plot_spec import CurveSpec, PlotSpec
        from materials_data_studio.tools.graph_workflows import from_plot_spec
        try:
            method, parameter = self.parameter.currentText().split(" / ", 1)
            field = self.condition.currentText()
            pairs = []
            for dataset in self.selected():
                candidates = [row for row in result_rows([dataset]) if row["method"] == method and row["parameter"] == parameter]
                conditions = {**dataset.metadata, **dataset.metadata.get("measurement_conditions", {})}
                if candidates and field in conditions:
                    pairs.append((float(conditions[field]), float(candidates[0]["value"])))
            if not pairs:
                raise ValueError("No matching finite parameter/condition pairs. Add scalar measurement conditions in Sample Registry first.")
            pairs.sort()
            x, y = np.array(pairs, dtype=float).T
            doc = from_plot_spec(PlotSpec(field, parameter, [CurveSpec(x, y, method)]))
            self.owner.open_native_graph(doc)
        except Exception as exc:
            QMessageBox.warning(self, "Parameter comparison", str(exc))

    def _recipe_page(self):
        layout = self.page("Recipes & batch")
        note = QLabel("Recipes use ordered, whitelisted operations; no code is executed. Check column keys and physical units before running. Each dataset commits only after its complete recipe succeeds.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.recipe = QPlainTextEdit()
        self.recipe.setPlaceholderText('Capture a dataset or load a recipe. Fit steps specify x/y column keys, optional sigma and existing-model parameters.')
        layout.addWidget(self.recipe, 1)
        row = QHBoxLayout()
        self.button(row, "Capture first selected", self.capture)
        self.button(row, "Add operation…", self.add_operation)
        self.button(row, "Load…", self.load_recipe)
        self.button(row, "Save…", self.save_recipe)
        self.button(row, "Preview batch", self.preview_recipe)
        self.button(row, "Preview result (no changes)", self.preview_result)
        self.button(row, "Run checked datasets", self.run_batch)
        layout.addLayout(row)
        self.batch_status = QLabel()
        self.batch_status.setWordWrap(True)
        layout.addWidget(self.batch_status)

    def capture(self):
        selected = self.selected()
        if selected:
            value = capture_recipe(selected[0])
            self.recipe.setPlainText(json.dumps(value, indent=2, ensure_ascii=False))

    def recipe_value(self):
        if len(self.recipe.toPlainText()) > 2 * 1024**2:
            raise ValueError("Recipe is too large")
        return validate_recipe(json.loads(self.recipe.toPlainText()))

    def add_operation(self):
        from materials_data_studio.core.analysis_workflows import OPERATIONS
        selected = self.selected()
        if not selected:
            QMessageBox.information(self, "Recipe", "Select a source dataset first")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Add ordered analysis operation")
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        operation = QComboBox()
        operation.addItems(OPERATIONS)
        x, y, sigma = QComboBox(), QComboBox(), QComboBox()
        sigma.addItem("None", None)
        keys = [key for key, array in {**selected[0].raw_data, **selected[0].processed_data}.items() if array.ndim == 1 and array.dtype.kind in "biuf"]
        for key in keys:
            for combo in (x, y, sigma):
                combo.addItem(key, key)
        kind = selected[0].metadata.get("ppms_kind")
        special = ("__heat_temperature", "__heat_cp_molar", "__heat_sigma") if kind == "heat_capacity" else ("__transport_temperature", "__transport_value", "__transport_sigma") if kind == "resistivity" else ("__mpms_temperature", "__mpms_inverse", None) if selected[0].module_type.value == "mpms" else None
        if special:
            for combo, key in zip((x, y, sigma), special, strict=True):
                if key:
                    combo.addItem(key + " (per-dataset units/segment)", key)
        y.setCurrentIndex(min(1, y.count()-1))
        for label, control in (("Operation", operation), ("Input X column", x), ("Input Y column (verify physical units)", y), ("Standard uncertainty column", sigma)):
            form.addRow(label, control)
        layout.addLayout(form)
        parameters = QPlainTextEdit()
        parameters.setMinimumHeight(180)
        defaults = {"settings": {"normalization": "max_100"}, "background": {"smoothness": 1e5, "convergence_ratio": .01, "max_iterations": 50}, "expression_fit": {"expression": "a*x+b", "minimum_x": 0, "maximum_x": 300}, "low_temperature": {"minimum_k": 2, "maximum_k": 10, "fix_gamma_zero": False}, "debye_einstein": {"minimum_k": 2, "maximum_k": 300, "n_einstein": 2}, "arrhenius": {"minimum_k": 2, "maximum_k": 300}, "mott_vrh": {"minimum_k": 2, "maximum_k": 300, "dimension": 3}, "curie_weiss": {"minimum_k": 100, "maximum_k": 300, "c_init": 1, "fix_c": False, "fix_chi0": False}}
        def operation_changed(name):
            options: dict[str, Any] = copy.deepcopy(defaults.get(name, {}))
            if name == "derive_column":
                options = {"name": "Derived", "symbol": "derived", "expression": "sample_moles", "key": "derived_recipe"}
            if special and name in {"low_temperature", "debye_einstein", "arrhenius", "mott_vrh", "curie_weiss"}:
                x.setCurrentIndex(x.findData(special[0]))
                y.setCurrentIndex(y.findData(special[1]))
                if kind == "resistivity":
                    options.update(bridge=1, vertical_source="resistivity")
                if name == "curie_weiss":
                    options["branch"] = "ZFC"
            parameters.setPlainText(json.dumps(options, indent=2))
        operation.currentTextChanged.connect(operation_changed)
        parameters.setPlainText(json.dumps(defaults["settings"], indent=2))
        layout.addWidget(QLabel("Parameters (JSON data only; no executable code). Fits use the selected arrays in the model's documented units."))
        layout.addWidget(parameters)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                value = self.recipe_value() if self.recipe.toPlainText().strip() else capture_recipe(selected[0])
                options = json.loads(parameters.toPlainText())
                if operation.currentText() not in {"settings", "background", "clear_background", "derive_column", "recalculate_columns"}:
                    options.update(x=x.currentData(), y=y.currentData(), sigma=sigma.currentData())
                value["steps"].append({"operation": operation.currentText(), "parameters": options})
                validate_recipe(value)
                self.recipe.setPlainText(json.dumps(value, ensure_ascii=False, indent=2))
            except Exception as exc:
                QMessageBox.warning(self, "Recipe", str(exc))
        dialog.deleteLater()

    def load_recipe(self):
        filename, _ = QFileDialog.getOpenFileName(self, "Load analysis recipe", "", "MDS Recipe (*.mdsr *.json)")
        if filename:
            try:
                self.recipe.setPlainText(json.dumps(read_recipe(Path(filename)), ensure_ascii=False, indent=2))
            except Exception as exc:
                QMessageBox.warning(self, "Recipe", str(exc))

    def save_recipe(self):
        try:
            value = self.recipe_value()
            filename, _ = QFileDialog.getSaveFileName(self, "Save analysis recipe", "analysis.mdsr", "MDS Recipe (*.mdsr)")
            if filename:
                write_json_atomic(Path(filename), value)
                self.state.axis_settings.setdefault("analysis_recipes", {})[value.get("name", Path(filename).stem)] = value
                self.state.modified = True
        except Exception as exc:
            QMessageBox.warning(self, "Recipe", str(exc))

    def preview_recipe(self):
        try:
            value = self.recipe_value()
            text = "\n".join(f"{dataset.display_name}: {len(value['steps'])} operations; {sum(a.nbytes for a in dataset.raw_data.values()):,} raw array bytes; {'compatible module' if value.get('module') == dataset.module_type.value else 'MODULE MISMATCH'}" for dataset in self.selected())
            QMessageBox.information(self, "Batch preview — no changes applied", text or "No datasets selected")
        except Exception as exc:
            QMessageBox.warning(self, "Recipe", str(exc))

    def run_batch(self):
        try:
            value = self.recipe_value()
            selected = self.selected()
            if not selected:
                raise ValueError("Select datasets first")
            if QMessageBox.question(self, "Run batch recipe", f"Apply {len(value['steps'])} ordered operations to {len(selected)} datasets? Review the preview and units first.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            self.owner.undo_history.checkpoint(force=True)
            for dataset in selected:
                cancelled = Event()
                from materials_data_studio.ui.undo_history import clone_dataset
                snapshot = clone_dataset(dataset)
                run_task(self.owner, "Run analysis recipe", lambda ds=snapshot, stop=cancelled: run_recipe(ds, value, stop),
                         lambda result, ds=dataset: self.owner.accept_recipe(ds, result), state=self.state, dataset=dataset, cancelled=cancelled)
            self.batch_status.setText(f"Queued {len(selected)} datasets. Use each job's Stop to cancel. Successful results appear in the session and common Results table.")
        except Exception as exc:
            QMessageBox.warning(self, "Recipe", str(exc))

    def preview_result(self):
        from materials_data_studio.core.analysis_workflows import recipe_plot
        from materials_data_studio.plotting.pyqtgraph_backend import create_plot_widget, render_plot
        def show(result):
            try:
                dialog = QDialog(self)
                dialog.setWindowTitle("Recipe preview — original session unchanged")
                dialog.resize(950, 650)
                layout = QVBoxLayout(dialog)
                plot = create_plot_widget()
                layout.addWidget(plot)
                render_plot(plot, recipe_plot(result))
                buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
                buttons.rejected.connect(dialog.close)
                layout.addWidget(buttons)
                dialog.exec()
                dialog.deleteLater()
            except Exception as exc:
                QMessageBox.warning(self, "Preview", str(exc))
        try:
            dataset = self.selected()[0]
            value = self.recipe_value()
            stop = Event()
            from materials_data_studio.ui.undo_history import clone_dataset
            snapshot = clone_dataset(dataset)
            run_task(self, "Preview analysis recipe", lambda: run_recipe(snapshot, value, stop), show, state=self.state, dataset=dataset, cancelled=stop)
        except Exception as exc:
            QMessageBox.warning(self, "Preview", str(exc))

    def _samples_page(self):
        layout = self.page("Sample registry")
        layout.addWidget(QLabel("Link selected datasets across modules. Defaults apply only when explicitly requested; file-header values remain in the original source."))
        self.samples = QTableWidget(0, 8)
        self.samples.setHorizontalHeaderLabels(["Sample ID", "Formula", "Mass (mg)", "Width (mm)", "Thickness (mm)", "Contact length (mm)", "Notes", "Conditions JSON"])
        registry = self.state.axis_settings.get("sample_registry", {})
        for sample_id, values in registry.items():
            self._append_sample([sample_id, *(values.get(key, "") for key in ("formula", "mass_mg", "width_mm", "thickness_mm", "contact_length_mm", "notes")), json.dumps(values.get("conditions", {}), ensure_ascii=False)])
        self.samples.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.samples, 1)
        self.override_sample = QCheckBox("Explicitly replace selected datasets' formula/mass/dimensions with sample defaults (invalidates fits)")
        layout.addWidget(self.override_sample)
        row = QHBoxLayout()
        self.button(row, "Add sample", lambda: self._append_sample(["New sample", "", "", "", "", "", "", "{}"]))
        self.button(row, "Delete selected sample", lambda: self.samples.removeRow(self.samples.currentRow()) if self.samples.currentRow() >= 0 else None)
        self.button(row, "Save registry / link selected sample", self.apply_sample)
        layout.addLayout(row)

    def _append_sample(self, values):
        row = self.samples.rowCount()
        self.samples.insertRow(row)
        for col, value in enumerate(values):
            self.samples.setItem(row, col, QTableWidgetItem(str(value)))

    def apply_sample(self):
        from materials_data_studio.modules.mpms.formula import calculate_molar_mass
        try:
            registry = {}
            for row in range(self.samples.rowCount()):
                values = [item.text().strip() if (item := self.samples.item(row, col)) is not None else "" for col in range(8)]
                sample_id = values[0]
                if not sample_id or sample_id in registry:
                    raise ValueError("Sample IDs must be nonempty and unique")
                sample = {"formula": values[1], "notes": values[6], "conditions": json.loads(values[7] or "{}")}
                if not isinstance(sample["conditions"], dict) or any(not isinstance(v, (str, int, float, bool)) for v in sample["conditions"].values()):
                    raise ValueError("Conditions must be a JSON object of scalar values")
                if any(isinstance(v, (int, float)) and not math.isfinite(v) for v in sample["conditions"].values()):
                    raise ValueError("Numeric conditions must be finite")
                for key, text in zip(("mass_mg", "width_mm", "thickness_mm", "contact_length_mm"), values[2:6], strict=True):
                    if text:
                        number = float(text)
                        if not math.isfinite(number) or number <= 0:
                            raise ValueError("Mass and dimensions must be positive")
                        sample[key] = number
                if values[1]:
                    sample["molar_mass_g_mol"] = calculate_molar_mass(values[1])
                registry[sample_id] = sample
            current = self.samples.currentRow()
            selected_item = self.samples.item(current, 0) if current >= 0 else None
            selected_id = selected_item.text().strip() if selected_item is not None else None
            self.owner.undo_history.checkpoint(force=True)
            self.state.axis_settings["sample_registry"] = registry
            if selected_id:
                sample = registry[selected_id]
                for dataset in self.selected():
                    dataset.metadata["sample_id"] = selected_id
                    dataset.metadata["sample_defaults"] = copy.deepcopy(sample)
                    dataset.metadata["measurement_conditions"] = copy.deepcopy(sample["conditions"])
                    if self.override_sample.isChecked():
                        invalidate_results(dataset, "Sample defaults explicitly applied")
                        for key in ("formula", "molar_mass_g_mol"):
                            if key in sample:
                                dataset.metadata[key] = sample[key]
                        if "mass_mg" in sample:
                            dataset.metadata.update(sample_mass_mg=sample["mass_mg"], sample_weight_mg=sample["mass_mg"])
                            if sample.get("molar_mass_g_mol"):
                                dataset.metadata["sample_moles"] = sample["mass_mg"] * 1e-3 / sample["molar_mass_g_mol"]
                        for setting in dataset.metadata.get("bridge_settings", {}).values():
                            for source_key, target_key in (("width_mm", "width_cm"), ("thickness_mm", "thickness_cm"), ("contact_length_mm", "length_cm")):
                                if source_key in sample:
                                    setting[target_key] = sample[source_key] / 10
                        if dataset.module_type == ModuleType.MPMS or dataset.metadata.get("ppms_kind") == "heat_capacity":
                            from materials_data_studio.core.measurement_branches import share_sample
                            share_sample(self.state, dataset)
            self.state.modified = True
            self.owner.refresh_analysis_modules()
            self.refresh_results()
        except Exception as exc:
            QMessageBox.warning(self, "Sample registry", str(exc))

    def _history_page(self):
        layout = self.page("Processing history")
        self.history = QPlainTextEdit()
        self.history.setReadOnly(True)
        layout.addWidget(self.history)
        self.button(layout, "Refresh processing history", self.refresh_history)
        self.refresh_history()

    def refresh_history(self):
        self.history.setPlainText(json.dumps([{ "dataset": dataset.display_name, "source_sha256": dataset.source_hash, "operations": dataset.metadata.get("processing_history", []), "last_recipe": dataset.metadata.get("last_recipe")} for dataset in self.selected()], ensure_ascii=False, indent=2))
