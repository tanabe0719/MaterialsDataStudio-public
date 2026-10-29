"""Compact settings and peak-selection controls for the phase-search window."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QWidget,
)

from materials_data_studio.modules.xrd.constants import WAVELENGTH_PRESETS_A
from materials_data_studio.modules.xrd.phase_models import CalculationSettings, SearchQuery
from materials_data_studio.ui.numeric_inputs import FlexibleDoubleSpinBox


def table(headers: list[str]) -> QTableWidget:
    result = QTableWidget(0, len(headers))
    result.setHorizontalHeaderLabels(headers)
    result.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    result.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    result.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    result.verticalHeader().hide()
    return result


def fill_table(target: QTableWidget, rows: list[list[Any]]) -> None:
    blocked, updates = target.blockSignals(True), target.updatesEnabled()
    target.setUpdatesEnabled(False)
    try:
        target.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, value in enumerate(row):
                target.setItem(i, j, QTableWidgetItem(str(value)))
        target.resizeColumnsToContents()
    finally:
        target.blockSignals(blocked)
        target.setUpdatesEnabled(updates)


class SearchForm(QWidget):
    def __init__(self, wavelength: float):
        super().__init__()
        form = QFormLayout(self)
        self.source = QComboBox()
        self.source.setProperty("mdsTranslateItems", True)
        self.source.addItems(["COD + MP + User CIF", "COD", "MP", "User CIF"])
        self.source.setCurrentIndex(0)
        form.addRow("Database", self.source)
        self.fields: dict[str, QLineEdit] = {}
        for key, label, hint in (("required", "Required elements", "All must be present: Na Cl"),
                                 ("allowed", "Allowed elements", "Subsets allowed: Na Ca Cl O"),
                                 ("excluded", "Excluded elements", "C H"), ("formula", "Formula", "NaCl"),
                                 ("cod_ids", "COD IDs", "Exact seven-digit IDs, separated by commas"),
                                 ("space_group", "Space group", "H-M symbol or IT number")):
            field = QLineEdit()
            field.setPlaceholderText(hint)
            field.setMaxLength(240)
            self.fields[key] = field
            form.addRow(label, field)
        self.crystal_system = QComboBox()
        self.crystal_system.addItems(["", "triclinic", "monoclinic", "orthorhombic", "tetragonal", "trigonal", "hexagonal", "cubic"])
        form.addRow("Crystal system", self.crystal_system)
        self.observation = QWidget()
        observed_form = QFormLayout(self.observation)
        advanced = QWidget()
        advanced_form = QFormLayout(advanced)
        advanced.setVisible(False)
        toggle = QToolButton()
        toggle.setText("Advanced detection settings…")
        toggle.setCheckable(True)
        toggle.toggled.connect(advanced.setVisible)
        self.numeric: dict[str, FlexibleDoubleSpinBox] = {}
        for key, label, lower, upper, value, decimals in (
            ("volume_min", "Minimum volume (Å³; 0 = any)", 0, 1e7, 0, 2),
            ("volume_max", "Maximum volume (Å³; 0 = any)", 0, 1e7, 0, 2),
            ("wavelength", "Wavelength (Å)", 0.05, 5, wavelength, 6),
            ("lower", "2θ minimum (deg)", 0.01, 179.9, 5, 3),
            ("upper", "2θ maximum (deg)", 0.02, 179.99, 90, 3),
            ("tolerance", "Peak tolerance (deg)", 0.001, 1, 0.15, 3),
            ("resolution", "Reflection grouping (deg)", 0, 0.5, 0.08, 3),
            ("zero_shift", "Fixed zero shift (deg)", -0.5, 0.5, 0, 3),
            ("prominence", "Detection prominence (fraction)", 0.0001, 1, 0.02, 4),
            ("smoothing_sigma", "Smoothing σ (deg)", 0, 1, .02, 3),
            ("noise_sigma", "Noise σ (0 = estimate)", 0, 1e12, 0, 4),
            ("snr", "Minimum signal / noise", 1, 100, 5, 1),
            ("min_fwhm", "Minimum FWHM (deg)", .001, 10, .04, 3),
            ("max_fwhm", "Maximum FWHM (deg)", .002, 10, 1.5, 3),
            ("peak_sigma", "Peak σ (deg; 0 = fitted)", 0, 3, 0, 4),
            ("background_smoothness", "Automatic background smoothness", 1, 1e12, 1e6, 0),
            ("alpha2_ratio", "Cu Kα2 / Kα1 intensity", 0, 1, .5, 3),
            ("min_score", "Minimum match score", 0, 100, 50, 1)):
            field = FlexibleDoubleSpinBox()
            field.setRange(lower, upper)
            field.setDecimals(decimals)
            field.setValue(value)
            self.numeric[key] = field
            if key == "wavelength":
                self.radiation = QComboBox()
                self.radiation.addItems(list(WAVELENGTH_PRESETS_A))
                self.radiation.setCurrentText("Cu Kα1/2" if wavelength == 1.540598 else next((n for n, v in WAVELENGTH_PRESETS_A.items() if v == wavelength), "Custom"))
                observed_form.addRow("Radiation", self.radiation)
            if key in {"volume_min", "volume_max", "tolerance", "resolution", "zero_shift", "min_score"}:
                target = form
            elif key in {"smoothing_sigma", "noise_sigma", "min_fwhm", "max_fwhm", "peak_sigma", "background_smoothness", "alpha2_ratio"}:
                target = advanced_form
            else:
                target = observed_form
            target.addRow(label, field)
        self.mp_scale = FlexibleDoubleSpinBox()
        self.mp_scale.setRange(0, 3)
        self.mp_scale.setDecimals(2)
        self.mp_scale.setValue(1)
        form.addRow("MP isotropic lattice range (±%)", self.mp_scale)
        observed_form.addRow(toggle)
        observed_form.addRow(advanced)
        for key, label, lower, upper, value in (("candidate_limit", "Retrieval candidates per database", 50, 5000, 1000),
                                                 ("result_limit", "Number of ranked results", 1, 500, 100)):
            field = FlexibleDoubleSpinBox()
            field.setRange(lower, upper)
            field.setDecimals(0)
            field.setValue(value)
            self.numeric[key] = field
            form.addRow(label, field)
        self.radiation.currentTextChanged.connect(self._radiation)
        self.numeric["wavelength"].valueChanged.connect(self._wavelength)

    def _radiation(self, name: str) -> None:
        value = 1.540598 if name == "Cu Kα1/2" else WAVELENGTH_PRESETS_A[name]
        if value is not None:
            self.numeric["wavelength"].setValue(value)

    def _wavelength(self, value: float) -> None:
        if (1.540598 if self.radiation.currentText() == "Cu Kα1/2" else WAVELENGTH_PRESETS_A[self.radiation.currentText()]) != value:
            self.radiation.setCurrentText("Custom")

    def query(self) -> SearchQuery:
        query = SearchQuery(**{k: v.text().strip() for k, v in self.fields.items()}, crystal_system=self.crystal_system.currentText(),
                            volume_min=self.numeric["volume_min"].value(), volume_max=self.numeric["volume_max"].value())
        query.validate()
        return query

    def settings(self) -> CalculationSettings:
        settings = CalculationSettings(**{k: self.numeric[k].value() for k in ("wavelength", "lower", "upper", "tolerance", "resolution", "zero_shift")}, radiation=self.radiation.currentText(), alpha2_ratio=self.numeric["alpha2_ratio"].value())
        settings.validate()
        return settings

    def state(self) -> dict[str, Any]:
        return {"mp_scale_percent": self.mp_scale.value(), "source": self.source.currentIndex(), "source_format": "combined-v2", "limits": {k: self.numeric[k].value() for k in ("candidate_limit", "result_limit")}, "query": {**{k: v.text() for k, v in self.fields.items()},
                    "crystal_system": self.crystal_system.currentText(), "volume_min": self.numeric["volume_min"].value(), "volume_max": self.numeric["volume_max"].value()},
                "settings": {**{k: self.numeric[k].value() for k in ("wavelength", "lower", "upper", "tolerance", "resolution", "zero_shift")}, "radiation": self.radiation.currentText(), "alpha2_ratio": self.numeric["alpha2_ratio"].value()}, "detection": self.detection_values(), "min_score": self.numeric["min_score"].value(), "prominence": self.numeric["prominence"].value()}

    def detection_values(self) -> dict[str, float]:
        return {k: self.numeric[k].value() for k in ("prominence", "smoothing_sigma", "noise_sigma", "snr", "min_fwhm", "max_fwhm", "peak_sigma", "background_smoothness", "alpha2_ratio")}

    def restore(self, state: dict[str, Any]) -> None:
        self.mp_scale.setValue(float(state.get("mp_scale_percent", 1)))
        for key, value in state.get("query", {}).items():
            if key in self.fields:
                self.fields[key].setText(str(value))
            elif key in self.numeric:
                self.numeric[key].setValue(float(value))
        self.crystal_system.setCurrentText(state.get("query", {}).get("crystal_system", ""))
        for key, value in state.get("settings", {}).items():
            if key in self.numeric:
                self.numeric[key].setValue(float(value))
        if state.get("settings"):
            self.radiation.setCurrentText(state["settings"].get("radiation", "Custom"))
        self.numeric["prominence"].setValue(float(state.get("prominence", 0.02)))
        source = int(state.get("source", 0))
        if state.get("source_format") == "combined-v2":
            self.source.setCurrentIndex(max(0, min(source, 3)))
        elif state:
            # Migrate old explicit local-only/COD selections; combined now includes MP.
            self.source.setCurrentIndex({0: 3, 1: 1, 2: 0, 3: 0}.get(source, 0))
        for key, value in state.get("limits", {}).items():
            if key in self.numeric:
                self.numeric[key].setValue(float(value))
        for key, value in state.get("detection", {}).items():
            if key in self.numeric:
                self.numeric[key].setValue(float(value))
        self.numeric["min_score"].setValue(float(state.get("min_score", 50)))


def fill_peaks(target: QTableWidget, peaks: list[dict[str, Any]]) -> None:
    target.blockSignals(True)
    fill_table(target, [["", f"{p['position']:.5f}", f"{p['intensity']:.5g}", f"{p.get('width', 0):.4g}", p.get("origin", "saved")] for p in peaks])
    for i, peak in enumerate(peaks):
        item = target.item(i, 0)
        assert item is not None
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked if peak.get("included", True) else Qt.CheckState.Unchecked)
    target.blockSignals(False)
