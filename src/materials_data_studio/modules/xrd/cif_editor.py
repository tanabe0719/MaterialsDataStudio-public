"""Table-based, non-destructive CIF editor dialog."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from materials_data_studio.modules.xrd.cif_backend import (
    AtomSite,
    CifDocument,
    UnitCell,
    constrain_unit_cell,
    extract_cif_symmetry,
    extract_cif_tables,
    resolve_space_group,
    save_cif_as,
    update_cif_tables,
)
from materials_data_studio.ui.numeric_inputs import (
    FlexibleDoubleSpinBox as QDoubleSpinBox,
)


class CifEditorDialog(QDialog):
    """Edit cell/site tables and configure powder-pattern simulation."""

    def __init__(
        self,
        document: CifDocument,
        parent: QWidget | None = None,
        *,
        simulation_settings: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(parent)
        self.document = document
        self.setWindowTitle(f"CIF Editor — {document.source_path.name}")
        self.resize(880, 620)
        self.cell, self.sites, warnings = extract_cif_tables(document)
        self.symmetry = extract_cif_symmetry(document)
        self._updating_cell = False
        self._cell_fields: dict[str, QDoubleSpinBox] = {}
        self._build_ui(warnings)
        self._load_tables()
        self._load_simulation_settings(simulation_settings or {})

    def _build_ui(self, warnings: list[str]) -> None:
        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        tabs.tabBar().setProperty("navigation", "dialog")
        tabs.tabBar().setExpanding(False)
        cell_page = QWidget()
        cell_form = QFormLayout(cell_page)
        self.space_group_name = QLineEdit(self.symmetry.name)
        self.space_group_name.editingFinished.connect(self._space_group_changed)
        self.space_group_number = QLabel(str(self.symmetry.number))
        self.crystal_system = QLabel(self.symmetry.crystal_system.title())
        cell_form.addRow("Space group (H-M)", self.space_group_name)
        cell_form.addRow("International number", self.space_group_number)
        cell_form.addRow("Crystal system", self.crystal_system)
        for field_name, label in (
            ("a", "a (Å)"),
            ("b", "b (Å)"),
            ("c", "c (Å)"),
            ("alpha", "alpha (deg)"),
            ("beta", "beta (deg)"),
            ("gamma", "gamma (deg)"),
        ):
            field = QDoubleSpinBox()
            field.setDecimals(8)
            field.setRange(0.000001, 1_000_000 if field_name in {"a", "b", "c"} else 179.999999)
            field.valueChanged.connect(
                lambda _value, name=field_name: self._cell_value_changed(name)
            )
            self._cell_fields[field_name] = field
            cell_form.addRow(label, field)
        tabs.addTab(cell_page, "Unit Cell")

        site_page = QWidget()
        site_layout = QVBoxLayout(site_page)
        self.site_table = QTableWidget(0, 7)
        self.site_table.setHorizontalHeaderLabels(
            ["Label", "Species", "x", "y", "z", "Occupancy", "U/B iso"]
        )
        self.site_table.horizontalHeader().setStretchLastSection(True)
        site_layout.addWidget(self.site_table)
        site_buttons = QHBoxLayout()
        for text, slot in (
            ("Add Site", self._add_site),
            ("Remove Site", self._remove_site),
            ("Duplicate Site", self._duplicate_site),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            site_buttons.addWidget(button)
        site_buttons.addStretch(1)
        site_layout.addLayout(site_buttons)
        tabs.addTab(site_page, "Atom Sites")

        simulation_page = QWidget()
        simulation_form = QFormLayout(simulation_page)
        self.two_theta_min = QDoubleSpinBox()
        self.two_theta_min.setRange(0, 179)
        self.two_theta_min.setValue(1)
        self.two_theta_max = QDoubleSpinBox()
        self.two_theta_max.setRange(1, 180)
        self.two_theta_max.setValue(90)
        from materials_data_studio.modules.xrd.constants import WAVELENGTH_PRESETS_A
        self.radiation = QComboBox()
        self.radiation.addItems(list(WAVELENGTH_PRESETS_A))
        self.wavelength = QDoubleSpinBox()
        self.wavelength.setDecimals(6)
        self.wavelength.setRange(0.01, 10)
        self.wavelength.setValue(1.540598)
        self.relative_scale = QDoubleSpinBox()
        self.relative_scale.setRange(0, 1e6)
        self.relative_scale.setValue(1)
        self.vertical_offset = QDoubleSpinBox()
        self.vertical_offset.setRange(-1e9, 1e9)
        self.broadened = QCheckBox("Show Gaussian broadened profile")
        self.broadened.setChecked(True)
        self.fwhm = QDoubleSpinBox()
        self.fwhm.setRange(0.001, 20)
        self.fwhm.setDecimals(4)
        self.fwhm.setValue(0.15)
        self.profile_step = QDoubleSpinBox()
        self.profile_step.setRange(0.001, 0.1)
        self.profile_step.setDecimals(4)
        self.profile_step.setSingleStep(0.005)
        self.profile_step.setValue(0.01)
        simulation_form.addRow("Radiation", self.radiation)
        simulation_form.addRow("Wavelength (Å)", self.wavelength)
        self.radiation.currentTextChanged.connect(self._radiation_changed)
        self.wavelength.valueChanged.connect(self._wavelength_changed)
        simulation_form.addRow("Minimum 2θ (deg)", self.two_theta_min)
        simulation_form.addRow("Maximum 2θ (deg)", self.two_theta_max)
        simulation_form.addRow("Relative scale", self.relative_scale)
        simulation_form.addRow("Vertical offset", self.vertical_offset)
        simulation_form.addRow(self.broadened)
        simulation_form.addRow("FWHM (deg)", self.fwhm)
        simulation_form.addRow("Profile step (deg)", self.profile_step)
        tabs.addTab(simulation_page, "Simulation")

        layout = QVBoxLayout(self)
        if warnings:
            warning_label = QLabel("\n".join(warnings))
            warning_label.setWordWrap(True)
            warning_label.setStyleSheet("color: #9A6700;")
            layout.addWidget(warning_label)
        layout.addWidget(tabs)
        utility_row = QHBoxLayout()
        revert = QPushButton("Revert Changes")
        save_as = QPushButton("Save Edited CIF As…")
        revert.clicked.connect(self._revert)
        save_as.clicked.connect(self._save_as)
        utility_row.addWidget(revert)
        utility_row.addWidget(save_as)
        utility_row.addStretch(1)
        layout.addLayout(utility_row)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Apply and Simulate")
        buttons.accepted.connect(self._accept_edits)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _radiation_changed(self, name: str) -> None:
        from materials_data_studio.modules.xrd.constants import WAVELENGTH_PRESETS_A
        value = WAVELENGTH_PRESETS_A[name]
        if value is not None:
            self.wavelength.setValue(value)

    def _wavelength_changed(self, value: float) -> None:
        from materials_data_studio.modules.xrd.constants import WAVELENGTH_PRESETS_A
        expected = WAVELENGTH_PRESETS_A[self.radiation.currentText()]
        if expected is not None and value != expected:
            self.radiation.setCurrentText("Custom")

    def _load_simulation_settings(self, settings: dict[str, Any]) -> None:
        angle_range = settings.get("two_theta_range", (1.0, 90.0))
        if isinstance(angle_range, (list, tuple)) and len(angle_range) == 2:
            self.two_theta_min.setValue(float(angle_range[0]))
            self.two_theta_max.setValue(float(angle_range[1]))
        self.wavelength.setValue(float(settings.get("wavelength_a", 1.540598)))
        self.radiation.setCurrentText(str(settings.get("radiation", "Custom")))
        self.relative_scale.setValue(float(settings.get("relative_scale", 1.0)))
        self.vertical_offset.setValue(float(settings.get("vertical_offset", 0.0)))
        fwhm = settings.get("fwhm_deg", 0.15)
        self.broadened.setChecked(fwhm is not None)
        if fwhm is not None:
            self.fwhm.setValue(float(fwhm))
        self.profile_step.setValue(float(settings.get("profile_step_deg", 0.01)))

    def _load_tables(self) -> None:
        self._updating_cell = True
        for name, field in self._cell_fields.items():
            field.setValue(float(getattr(self.cell, name)))
        self._updating_cell = False
        self._apply_symmetry_constraints()
        self.site_table.setRowCount(len(self.sites))
        for row, site in enumerate(self.sites):
            values = (
                site.label,
                site.species,
                f"{site.fract_x:.12g}",
                f"{site.fract_y:.12g}",
                f"{site.fract_z:.12g}",
                f"{site.occupancy:.12g}",
                "" if site.displacement is None else f"{site.displacement:.12g}",
            )
            for column, value in enumerate(values):
                self.site_table.setItem(row, column, QTableWidgetItem(value))

    def _read_tables(self) -> tuple[UnitCell, list[AtomSite]]:
        cell = constrain_unit_cell(
            UnitCell(**{name: field.value() for name, field in self._cell_fields.items()}),
            self.symmetry,
        )
        sites = []
        for row in range(self.site_table.rowCount()):
            values = []
            for column in range(self.site_table.columnCount()):
                item = self.site_table.item(row, column)
                values.append(item.text().strip() if item is not None else "")
            sites.append(
                AtomSite(
                    label=values[0],
                    species=values[1],
                    fract_x=float(values[2]),
                    fract_y=float(values[3]),
                    fract_z=float(values[4]),
                    occupancy=float(values[5]),
                    displacement=float(values[6]) if values[6] else None,
                )
            )
        return cell, sites

    def _space_group_changed(self) -> None:
        try:
            self.symmetry = resolve_space_group(self.space_group_name.text())
        except Exception as exc:
            QMessageBox.critical(self, "Space-Group Error", str(exc))
            self.space_group_name.setText(self.symmetry.name)
            return
        self.space_group_name.setText(self.symmetry.name)
        self.space_group_number.setText(str(self.symmetry.number))
        self.crystal_system.setText(self.symmetry.crystal_system.title())
        self._apply_symmetry_constraints()

    def _cell_value_changed(self, name: str) -> None:
        if not self._updating_cell:
            self._apply_symmetry_constraints(name)

    def _apply_symmetry_constraints(self, changed_parameter: str = "a") -> None:
        cell = constrain_unit_cell(
            UnitCell(**{name: field.value() for name, field in self._cell_fields.items()}),
            self.symmetry,
            changed_parameter=changed_parameter,
        )
        self._updating_cell = True
        for name, field in self._cell_fields.items():
            field.setValue(float(getattr(cell, name)))
        system = self.symmetry.crystal_system
        editable = {
            "cubic": {"a"},
            "tetragonal": {"a", "c"},
            "orthorhombic": {"a", "b", "c"},
            "hexagonal": {"a", "c"},
            "monoclinic": {"a", "b", "c", "beta"},
        }.get(system, set(self._cell_fields))
        if system == "trigonal":
            editable = {"a", "c"} if ":H" in self.symmetry.name.upper() else {"a", "alpha"}
        for name, field in self._cell_fields.items():
            field.setEnabled(name in editable)
        self._updating_cell = False

    def _apply_tables(self) -> None:
        cell, sites = self._read_tables()
        warnings = update_cif_tables(self.document, cell, sites, self.symmetry)
        self.cell, self.sites = cell, sites
        if warnings:
            QMessageBox.warning(self, "Partial Occupancy", "\n".join(warnings))

    def _add_site(self) -> None:
        row = self.site_table.rowCount()
        self.site_table.insertRow(row)
        defaults = (f"Site{row + 1}", "X", "0", "0", "0", "1", "")
        for column, value in enumerate(defaults):
            self.site_table.setItem(row, column, QTableWidgetItem(value))

    def _remove_site(self) -> None:
        selected = sorted(
            {index.row() for index in self.site_table.selectedIndexes()}, reverse=True
        )
        for row in selected:
            self.site_table.removeRow(row)

    def _duplicate_site(self) -> None:
        row = self.site_table.currentRow()
        if row < 0:
            return
        target = self.site_table.rowCount()
        self.site_table.insertRow(target)
        for column in range(self.site_table.columnCount()):
            source = self.site_table.item(row, column)
            value = source.text() if source is not None else ""
            if column == 0:
                value = f"{value}_copy"
            self.site_table.setItem(target, column, QTableWidgetItem(value))

    def _revert(self) -> None:
        self.document.revert()
        self.cell, self.sites, _ = extract_cif_tables(self.document)
        self.symmetry = extract_cif_symmetry(self.document)
        self.space_group_name.setText(self.symmetry.name)
        self.space_group_number.setText(str(self.symmetry.number))
        self.crystal_system.setText(self.symmetry.crystal_system.title())
        self._load_tables()

    def _save_as(self) -> None:
        try:
            self._apply_tables()
            name, _ = QFileDialog.getSaveFileName(
                self,
                "Save Edited CIF As",
                self.document.source_path.stem + "-edited.cif",
                "CIF (*.cif)",
            )
            if name:
                save_cif_as(self.document, Path(name))
        except Exception as exc:
            QMessageBox.critical(self, "CIF Validation Error", str(exc))

    def _accept_edits(self) -> None:
        try:
            if self.two_theta_min.value() >= self.two_theta_max.value():
                raise ValueError("Minimum 2θ must be less than maximum 2θ.")
            self._apply_tables()
        except Exception as exc:
            QMessageBox.critical(self, "CIF Validation Error", str(exc))
            return
        self.accept()
