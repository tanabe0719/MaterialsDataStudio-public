"""Interactive plot export dialog and multi-curve table writer."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from materials_data_studio.core.export import sanitize_column_name
from materials_data_studio.plotting.plot_spec import CurveSpec, PlotSpec
from materials_data_studio.ui.numeric_inputs import FlexibleDoubleSpinBox


def _unique_curve_names(curves: list[CurveSpec]) -> list[str]:
    names: list[str] = []
    counts: dict[str, int] = {}
    for index, curve in enumerate(curves, start=1):
        try:
            base = sanitize_column_name(curve.style.legend_text or curve.label)
        except ValueError:
            base = f"Curve_{index}"
        counts[base] = counts.get(base, 0) + 1
        names.append(base if counts[base] == 1 else f"{base}_{counts[base]}")
    return names


def export_plot_curves(path: Path, spec: PlotSpec, selected_indices: list[int]) -> None:
    """Write selected plotted curves as paired x/y columns, padding shorter curves."""
    if path.suffix.lower() not in {".csv", ".tsv"}:
        raise ValueError("Interactive data export supports CSV and TSV files.")
    curves = [spec.curves[index] for index in selected_indices]
    if not curves:
        raise ValueError("Select at least one curve to export.")
    names = _unique_curve_names(curves)
    delimiter = "," if path.suffix.lower() == ".csv" else "\t"
    row_count = max(len(curve.x) for curve in curves)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter=delimiter, lineterminator="\n")
        headers: list[str] = []
        for name in names:
            headers.extend((f"{name}_x", f"{name}_y"))
        writer.writerow(headers)
        for row in range(row_count):
            values: list[str | float] = []
            for curve in curves:
                if row < len(curve.x):
                    values.extend((float(curve.x[row]), float(curve.y[row])))
                else:
                    values.extend(("", ""))
            writer.writerow(values)


class InteractiveExportDialog(QDialog):
    """Choose plotted data, image output, or an Igor packed experiment."""

    def __init__(self, spec: PlotSpec, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export Interactive Plot")
        self.resize(560, 520)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.tabBar().setProperty("navigation", "dialog")
        layout.addWidget(self.tabs, 1)
        self._add_data_tab(spec)
        self._add_vector_tab(spec)
        self._add_raster_tab(spec)
        self._add_h5xp_tab()
        from materials_data_studio.ui.figure_templates import install_figure_templates
        self.appearance_override = None
        install_figure_templates(self, layout)
        from materials_data_studio.core.settings import create_settings
        try:
            saved = json.loads(str(create_settings().value("figures/default_export", "{}")))
            for field in (self.vector_width, self.raster_width):
                field.setValue(float(saved.get("width_cm", spec.figure_width_cm)))
            for field in (self.vector_height, self.raster_height):
                field.setValue(float(saved.get("height_cm", spec.figure_height_cm)))
            self.raster_dpi.setValue(int(saved.get("dpi", spec.dpi)))
            for field in (self.vector_transparent, self.raster_transparent):
                field.setChecked(bool(saved.get("transparent", spec.transparent)))
        except (ValueError, TypeError):
            pass
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _add_data_tab(self, spec: PlotSpec) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Curves currently shown from checked datasets:"))
        self.curves = QListWidget()
        for index, curve in enumerate(spec.curves):
            item = QListWidgetItem(curve.style.legend_text or curve.label or f"Curve {index + 1}")
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.curves.addItem(item)
        layout.addWidget(self.curves, 1)
        selection = QHBoxLayout()
        select_all = QPushButton("Select All")
        select_none = QPushButton("Clear")
        select_all.clicked.connect(lambda: self._check_all(Qt.CheckState.Checked))
        select_none.clicked.connect(lambda: self._check_all(Qt.CheckState.Unchecked))
        selection.addWidget(select_all)
        selection.addWidget(select_none)
        selection.addStretch(1)
        layout.addLayout(selection)
        form = QFormLayout()
        self.data_format = QComboBox()
        self.data_format.addItem("CSV - comma separated", "csv")
        self.data_format.addItem("TSV - tab separated", "tsv")
        form.addRow("Format", self.data_format)
        layout.addLayout(form)
        self.tabs.addTab(page, "Data")

    def _add_vector_tab(self, spec: PlotSpec) -> None:
        page = QWidget()
        form = QFormLayout(page)
        self.vector_format = QComboBox()
        self.vector_format.addItem("SVG", "svg")
        self.vector_format.addItem("PDF", "pdf")
        self.vector_width = self._size_control(spec.figure_width_cm)
        self.vector_height = self._size_control(spec.figure_height_cm)
        self.vector_transparent = QCheckBox("Transparent background")
        self.vector_transparent.setChecked(spec.transparent)
        form.addRow("Format", self.vector_format)
        form.addRow("Width", self.vector_width)
        form.addRow("Height", self.vector_height)
        form.addRow(self.vector_transparent)
        self.tabs.addTab(page, "Vector image")

    def _add_raster_tab(self, spec: PlotSpec) -> None:
        page = QWidget()
        form = QFormLayout(page)
        self.raster_format = QComboBox()
        self.raster_format.addItem("PNG", "png")
        self.raster_format.addItem("TIFF", "tiff")
        self.raster_format.addItem("JPEG", "jpg")
        self.raster_width = self._size_control(spec.figure_width_cm)
        self.raster_height = self._size_control(spec.figure_height_cm)
        self.raster_dpi = QSpinBox()
        self.raster_dpi.setRange(50, 2400)
        self.raster_dpi.setValue(spec.dpi)
        self.raster_dpi.setSuffix(" dpi")
        self.raster_transparent = QCheckBox("Transparent background")
        self.raster_transparent.setChecked(spec.transparent)
        self.raster_format.currentIndexChanged.connect(self._sync_raster_options)
        form.addRow("Format", self.raster_format)
        form.addRow("Width", self.raster_width)
        form.addRow("Height", self.raster_height)
        form.addRow("Resolution", self.raster_dpi)
        form.addRow(self.raster_transparent)
        self.tabs.addTab(page, "Raster image")
        self._sync_raster_options()

    def _add_h5xp_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("Igor Pro HDF5 Packed Experiment (.h5xp)")
        title.setProperty("role", "sectionTitle")
        layout.addWidget(title)
        description = QLabel(
            "Save the current interactive graph as editable Igor waves and a "
            "recreated native graph. This saves the experiment without launching Igor Pro."
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        details = QLabel(
            "Included: visible curves, x/y values, trace styles, axis labels, "
            "linear/log state, ranges, title, and legend state."
        )
        details.setWordWrap(True)
        layout.addWidget(details)
        layout.addStretch(1)
        self.tabs.addTab(page, "Igor H5XP")

    @staticmethod
    def _size_control(value: float) -> FlexibleDoubleSpinBox:
        control = FlexibleDoubleSpinBox()
        control.setRange(2.0, 100.0)
        control.setDecimals(2)
        control.setSingleStep(0.5)
        control.setSuffix(" cm")
        control.setValue(value)
        return control

    def _check_all(self, state: Qt.CheckState) -> None:
        for index in range(self.curves.count()):
            self.curves.item(index).setCheckState(state)

    def _sync_raster_options(self) -> None:
        supports_alpha = self.raster_format.currentData() != "jpg"
        self.raster_transparent.setEnabled(supports_alpha)
        if not supports_alpha:
            self.raster_transparent.setChecked(False)

    def _validate_and_accept(self) -> None:
        if self.export_kind() == "data" and not self.selected_curve_indices():
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.warning(self, "Export Interactive Plot", "Select at least one curve.")
            return
        if self.export_kind() in {"vector", "raster"}:
            from materials_data_studio.core.settings import create_settings
            width, height, dpi, transparent = self.image_options()
            create_settings().setValue("figures/default_export", json.dumps({"width_cm": width, "height_cm": height, "dpi": dpi, "transparent": transparent}))
        self.accept()

    def export_kind(self) -> str:
        return ("data", "vector", "raster", "h5xp")[self.tabs.currentIndex()]

    def selected_curve_indices(self) -> list[int]:
        return [
            int(self.curves.item(index).data(Qt.ItemDataRole.UserRole))
            for index in range(self.curves.count())
            if self.curves.item(index).checkState() == Qt.CheckState.Checked
        ]

    def selected_format(self) -> str:
        if self.export_kind() == "data":
            return str(self.data_format.currentData())
        if self.export_kind() == "vector":
            return str(self.vector_format.currentData())
        if self.export_kind() == "h5xp":
            return "h5xp"
        return str(self.raster_format.currentData())

    def image_options(self) -> tuple[float, float, int, bool]:
        if self.export_kind() == "vector":
            return (
                self.vector_width.value(),
                self.vector_height.value(),
                300,
                self.vector_transparent.isChecked(),
            )
        return (
            self.raster_width.value(),
            self.raster_height.value(),
            self.raster_dpi.value(),
            self.raster_transparent.isChecked(),
        )
