"""Interactive PPMS Electrical Resistivity workspace."""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path
from typing import Any

import numpy as np
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
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from materials_data_studio.core.export import export_numeric
from materials_data_studio.core.models import Dataset, ModuleType, PlotStyle, source_sha256
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.core.source_io import ImportedSource, import_source
from materials_data_studio.core.units import FieldUnit, convert_field
from materials_data_studio.modules.ppms.io import read_ppms_text
from materials_data_studio.modules.ppms.resistivity import (
    fit_arrhenius,
    fit_mott_vrh,
    geometry_factor,
    resistance_to_resistivity,
    segment_transport,
    source_resistivity_to_ohm_cm,
)
from materials_data_studio.modules.ppms.resistivity_parser import (
    ResistivityParseResult,
    parse_resistivity_text,
)
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
from materials_data_studio.ui.dialogs import show_error
from materials_data_studio.ui.measurement_tree import MeasurementTree
from materials_data_studio.ui.numeric_inputs import (
    FlexibleDoubleSpinBox as QDoubleSpinBox,
)
from materials_data_studio.ui.panels import dataset_context, install_settings_panel
from materials_data_studio.ui.style_inputs import ColorEdit, StyleCombo
from materials_data_studio.ui.tasks import blocking_task
from materials_data_studio.ui.workspace_layout import (
    WorkspaceSplitter,
    install_workspace,
    set_residual_available,
    with_residual_controls,
)


def _default_bridge_setting(
    bridge: int, available: bool, name: str, source: str, kind: str, unit: str
) -> dict[str, Any]:
    style = default_plot_style(bridge - 1, name or f"Bridge {bridge}")
    return {
        "visible": available,
        "name": name or f"Bridge {bridge}",
        "source": source,
        "kind": kind,
        "unit": unit,
        "geometry_mode": "width_thickness_length",
        "width_cm": 0.0,
        "thickness_cm": 0.0,
        "length_cm": 0.0,
        "style": style.to_dict(),
    }


class ResistivityWidget(QWidget):
    """Plot and fit up to four PPMS electrical-transport bridges."""

    kind = "resistivity"

    def __init__(self, state: ProjectState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self.dataset_list = MeasurementTree(
            state,
            ModuleType.PPMS,
            dataset_predicate=lambda dataset: dataset.metadata.get("ppms_kind") == self.kind,
            kind=self.kind,
        )
        self.rho_t_plot = create_plot_widget()
        self.rho_h_plot = create_plot_widget()
        self.plot_widget = self.rho_t_plot
        self.plot_tabs = QTabWidget()
        self.plot_tabs.setDocumentMode(True)
        self.plot_tabs.tabBar().setProperty("navigation", "plot")
        self.plot_tabs.tabBar().setExpanding(False)
        self.plot_tabs.addTab(self.rho_t_plot, "ρ–T")
        self.plot_tabs.addTab(self.rho_h_plot, "ρ–H")
        self.plot_tabs.currentChanged.connect(self._plot_tab_changed)
        self.residual_widget = create_plot_widget()
        self._build_controls()
        from materials_data_studio.ui.branch_controls import BranchControls
        self.branch_controls = BranchControls(self.state, {"fit_min": self.fit_min, "fit_max": self.fit_max, "vrh_dimension": self.vrh_dimension, "fit_weighted": self.fit_weighted})
        self._build_layout()
        self._display_mode_changed()
        from materials_data_studio.ui.analysis_ranges import install_fit_range
        def coordinate(value):
            mode = self.display_mode.currentData()
            return ((1000.0 if self.arrhenius_x.currentData() == "thousand" else 1.0) / max(value, 1e-12) if mode == "arrhenius" else max(value, 1e-12) ** (-1 / (int(self.vrh_dimension.currentData()) + 1)) if mode == "vrh" else value)
        def physical(value):
            mode = self.display_mode.currentData()
            return ((1000.0 if self.arrhenius_x.currentData() == "thousand" else 1.0) / max(value, 1e-12) if mode == "arrhenius" else max(value, 1e-12) ** (-(int(self.vrh_dimension.currentData()) + 1)) if mode == "vrh" else value)
        install_fit_range(self.rho_t_plot, self.fit_min, self.fit_max, to_plot=coordinate, to_physical=physical)
        self.dataset_list.current_dataset_changed.connect(self._load_selected)
        self.dataset_list.datasets_changed.connect(self.refresh_plot)
        self.refresh()

    def _spin(self, value: float = 0.0) -> QDoubleSpinBox:
        field = QDoubleSpinBox()
        field.setDecimals(8)
        field.setRange(0, 1e12)
        field.setValue(value)
        return field

    def _build_controls(self) -> None:
        self.bridge_checks: dict[int, QCheckBox] = {}
        for bridge in range(1, 5):
            checkbox = QCheckBox(f"Bridge {bridge}")
            checkbox.toggled.connect(self._bridge_visibility_changed)
            self.bridge_checks[bridge] = checkbox
        self.bridge = QComboBox()
        self.bridge.currentIndexChanged.connect(self._load_bridge)
        self.availability = QLabel("No dataset selected")
        self.source = QComboBox()
        self.source.currentIndexChanged.connect(self._source_changed)
        self.geometry_status = QLabel("Geometry not configured")
        self.geometry_status.setWordWrap(True)
        self.sample_width = self._spin()
        self.thickness = self._spin()
        self.length = self._spin()
        self.apply_geometry = QPushButton("Apply Bridge Geometry")
        self.apply_geometry.clicked.connect(self._save_bridge)
        self.style_color = QPushButton("Choose…")
        self.style_color.clicked.connect(self._choose_style_color)
        self.line_style = StyleCombo("line")
        self.line_style.addItems(["-", "--", ":", "-.", "none"])
        self.marker = StyleCombo("marker")
        self.marker.addItems(["none", "o", "s", "^", "v", "d", "x", "+"])
        self.marker_size = self._spin(6)
        self.marker_fill = ColorEdit("none")
        self.line_style.currentIndexChanged.connect(self._apply_bridge_style)
        self.marker.currentIndexChanged.connect(self._apply_bridge_style)
        self.marker_size.valueChanged.connect(self._apply_bridge_style)
        self.marker_fill.editingFinished.connect(self._apply_bridge_style)

        self.plot_type = QComboBox()
        self.plot_type.addItem("rho vs T", "rho_t")
        self.plot_type.addItem("rho vs H", "rho_h")
        self.plot_type.currentIndexChanged.connect(self._mode_changed)
        self.vertical_unit = QComboBox()
        self.vertical_unit.addItem("Raw resistance (ohm)", "raw")
        self.vertical_unit.addItem("Resistivity (ohm·cm)", "resistivity")
        self.vertical_unit.currentIndexChanged.connect(self.refresh_plot)
        self.display_mode = QComboBox()
        self.display_mode.addItem("Normal", "normal")
        self.display_mode.addItem("Arrhenius", "arrhenius")
        self.display_mode.addItem("Mott variable-range hopping", "vrh")
        self.display_mode.currentIndexChanged.connect(self._display_mode_changed)
        self.arrhenius_x = QComboBox()
        self.arrhenius_x.addItem("1/T (K⁻¹)", "inverse")
        self.arrhenius_x.addItem("1000/T (K⁻¹)", "thousand")
        self.arrhenius_x.currentIndexChanged.connect(self.refresh_plot)
        self.field_unit = QComboBox()
        for unit in FieldUnit:
            self.field_unit.addItem(unit.value, unit.value)
        self.field_unit.currentIndexChanged.connect(self.refresh_plot)
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
        self.log_y = QCheckBox("Logarithmic y axis (Normal mode)")
        self.log_y.toggled.connect(self.refresh_plot)
        self.legend = QCheckBox("Show legend")
        self.legend.setChecked(True)
        self.legend.toggled.connect(self.refresh_plot)

        self.fit_min = self._spin(2)
        self.fit_max = self._spin(300)
        self.fit_weighted = QCheckBox("Use valid error weights")
        self.vrh_dimension = QComboBox()
        for dimension in (1, 2, 3):
            self.vrh_dimension.addItem(f"Mott VRH, d = {dimension}", dimension)
        self.vrh_dimension.currentIndexChanged.connect(self.refresh_plot)
        self.run_fit = QPushButton("Run Selected Linearized Fit")
        self.run_fit.clicked.connect(self._run_fit)
        self.reset_fit = QPushButton("Clear fit result")
        self.reset_fit.clicked.connect(self._reset_fit)
        self.fit_result = QLabel("No transport fit.")
        self.fit_result.setWordWrap(True)
        self.figure_width = self._spin(16)
        self.figure_width.setRange(4, 60)
        self.figure_height = self._spin(11)
        self.figure_height.setRange(4, 60)
        self.figure_dpi = QSpinBox()
        self.figure_dpi.setRange(72, 1200)
        self.figure_dpi.setValue(300)
        self.transparent = QCheckBox("Transparent background")

    def _build_layout(self) -> None:
        left = QGroupBox("Resistivity Datasets")
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(self.dataset_list)
        button = QPushButton("Import Resistivity DAT")
        button.setProperty("primary", True)
        button.clicked.connect(self.choose_import_files)
        left_layout.addWidget(button)
        center = QSplitter(Qt.Orientation.Vertical)
        center.addWidget(self.plot_tabs)
        center.addWidget(self.residual_widget)
        center.setSizes([650, 200])
        right = QWidget()
        right_layout = QVBoxLayout(right)
        bridge_card = QGroupBox("Bridge Controls")
        bridge_form = QFormLayout(bridge_card)
        bridge_row = QHBoxLayout()
        for checkbox in self.bridge_checks.values():
            bridge_row.addWidget(checkbox)
        for checkbox in self.bridge_checks.values():
            checkbox.hide()
        self.bridge.hide()
        bridge_form.addRow("Availability", self.availability)
        bridge_form.addRow("Source column", self.source)
        bridge_form.addRow("Width (cm)", self.sample_width)
        bridge_form.addRow("Thickness (cm)", self.thickness)
        bridge_form.addRow("Contact length L (cm)", self.length)
        bridge_form.addRow(self.apply_geometry)
        bridge_form.addRow(self.geometry_status)
        style_card = QGroupBox("Plot Style")
        style_form = QFormLayout(style_card)
        style_form.addRow("Color", self.style_color)
        style_form.addRow("Line style", self.line_style)
        style_form.addRow("Marker", self.marker)
        style_form.addRow("Marker size", self.marker_size)
        style_form.addRow("Marker fill", self.marker_fill)
        plot_card = QGroupBox("Sweep and Plot")
        plot_form = QFormLayout(plot_card)
        plot_form.addRow("Vertical axis", self.vertical_unit)
        plot_form.addRow("Display mode", self.display_mode)
        plot_form.addRow("Arrhenius x axis", self.arrhenius_x)
        plot_form.addRow("Field unit", self.field_unit)
        self.segment.hide()
        # Branch bounds are edited in the raw-data branch dialog.
        style_form.addRow(self.log_y)
        style_form.addRow(self.legend)
        fit_card = QGroupBox("Arrhenius / Mott VRH Fit")
        fit_form = QFormLayout(fit_card)
        fit_form.addRow("Minimum T (K)", self.fit_min)
        fit_form.addRow("Maximum T (K)", self.fit_max)
        fit_form.addRow("VRH dimensionality", self.vrh_dimension)
        fit_form.addRow(self.fit_weighted)
        fit_form.addRow(self.run_fit)
        fit_form.addRow(self.reset_fit)
        fit_form.addRow(self.fit_result)
        figure_card = QGroupBox("Figure Export")
        figure_form = QFormLayout(figure_card)
        figure_form.addRow("Width (cm)", self.figure_width)
        figure_form.addRow("Height (cm)", self.figure_height)
        figure_form.addRow("DPI", self.figure_dpi)
        figure_form.addRow(self.transparent)
        for card in (bridge_card, plot_card, style_card, fit_card, figure_card):
            right_layout.addWidget(card)
        right_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        install_settings_panel(scroll, right, "resistivity", "Selected dataset / branch")
        splitter = WorkspaceSplitter()
        splitter.addWidget(left)
        splitter.addWidget(with_residual_controls(center, self.residual_widget, "resistivity"))
        splitter.addWidget(scroll)
        splitter.setSizes([280, 850, 360])
        self.splitter = splitter
        layout = QVBoxLayout(self)
        install_workspace(layout, splitter, "resistivity")

    def choose_import_files(self) -> None:
        names, _ = QFileDialog.getOpenFileNames(
            self, "Import PPMS Resistivity", "", "PPMS DAT (*.dat);;All files (*)"
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
                        source_path, lambda file: parse_resistivity_text(read_ppms_text(file))
                    ),
                )
                self._add_result(path, source.result, source)
            except Exception as exc:
                show_error(self, "Resistivity Import Error", f"{path.name}: {exc}")

    def _add_result(
        self, path: Path, result: ResistivityParseResult, source: ImportedSource | None = None
    ) -> None:
        raw: dict[str, np.ndarray] = {
            "timestamp_s": result.timestamp_s,
            "temperature_k": result.temperature_k,
            "field_oe": result.field_oe,
            "measurement_order": result.measurement_order,
        }
        bridge_settings: dict[str, Any] = {}
        bridge_metadata: dict[str, Any] = {}
        for bridge, values in result.bridge_data.items():
            for key, array in values.items():
                raw[f"bridge_{bridge}_{key}"] = array
            metadata = result.bridges[bridge]
            bridge_metadata[str(bridge)] = {
                "sample_name": metadata.sample_name,
                "cross_section": metadata.cross_section,
                "length": metadata.length,
                "configured_unit": metadata.configured_unit,
                "available": metadata.available,
                "source_columns": metadata.source_columns,
            }
            bridge_settings[str(bridge)] = _default_bridge_setting(
                bridge,
                metadata.available,
                metadata.sample_name,
                metadata.selected_source,
                metadata.selected_kind,
                metadata.selected_unit,
            )
        dataset = Dataset(
            path.stem,
            ModuleType.PPMS,
            raw,
            source_filename=str(path),
            source_hash=source.sha256 if source else source_sha256(path),
            source_bytes=source.data if source else path.read_bytes(),
            metadata={
                "ppms_kind": self.kind,
                "bridges": bridge_metadata,
                "bridge_settings": bridge_settings,
                "plot_type": "rho_t",
                "selected_segment": 0,
                "manual_segment_bounds": {},
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

    def _settings(self, dataset: Dataset) -> dict[str, dict[str, Any]]:
        settings = dataset.metadata.get("bridge_settings", {})
        return settings if isinstance(settings, dict) else {}

    def _bridge_setting(self, dataset: Dataset, bridge: int) -> dict[str, Any]:
        setting = self._settings(dataset).get(str(bridge), {})
        return setting if isinstance(setting, dict) else {}

    def _segments(self, dataset: Dataset):
        return segment_transport(
            dataset.array("temperature_k"),
            dataset.array("field_oe"),
            mode=str(dataset.metadata.get("plot_type", "rho_t")),
            measurement_order=dataset.array("measurement_order"),
        )

    def _selected_segment(self, dataset: Dataset):
        if dataset.metadata.get("measurement_parent"):
            from materials_data_studio.modules.ppms.resistivity import TransportSegment
            values = np.asarray(dataset.array("field_oe"), dtype=float)
            segment = TransportSegment(0, np.arange(len(values), dtype=np.int64), "temperature", "manual branch", float(np.mean(values)), float(np.min(values)), float(np.max(values)))
            return segment, [segment]
        segments = self._segments(dataset)
        index = min(int(dataset.metadata.get("selected_segment", 0)), len(segments) - 1)
        selected = segments[index]
        overrides = dataset.metadata.get("manual_segment_bounds", {})
        stored = overrides.get(str(selected.segment_id)) if isinstance(overrides, dict) else None
        if isinstance(stored, list) and len(stored) == 2:
            start = max(0, min(int(stored[0]), len(dataset.array("temperature_k")) - 1))
            stop = max(start, min(int(stored[1]), len(dataset.array("temperature_k")) - 1))
            indices = np.arange(start, stop + 1, dtype=np.int64)
            selected.indices = indices
        return selected, segments

    def _load_selected(self, dataset: Dataset | None) -> None:
        self.branch_controls.load(dataset)
        dataset_context(self, dataset)
        from materials_data_studio.ui.branch_controls import branch_context
        branch_context(self, dataset)
        self.bridge.blockSignals(True)
        self.bridge.clear()
        if dataset is None:
            self.bridge.blockSignals(False)
            return
        metadata = dataset.metadata.get("bridges", {})
        settings = self._settings(dataset)
        for bridge in range(1, 5):
            info = metadata.get(str(bridge), {}) if isinstance(metadata, dict) else {}
            available = bool(info.get("available")) if isinstance(info, dict) else False
            label = f"Bridge {bridge}" + ("" if available else " — No valid data")
            self.bridge.addItem(label, bridge)
            self.bridge_checks[bridge].blockSignals(True)
            self.bridge_checks[bridge].setEnabled(available)
            self.bridge_checks[bridge].setChecked(
                bool(settings.get(str(bridge), {}).get("visible", False))
            )
            self.bridge_checks[bridge].blockSignals(False)
        branch_bridge = dataset.metadata.get("measurement_branch", {}).get("bridge")
        if branch_bridge is not None:
            self.bridge.setCurrentIndex(self.bridge.findData(int(branch_bridge)))
        self.bridge.blockSignals(False)
        index = self.plot_type.findData(dataset.metadata.get("measurement_branch", {}).get("mode", dataset.metadata.get("plot_type", "rho_t")))
        if index >= 0:
            self.plot_type.setCurrentIndex(index)
        self._load_bridge()
        self._refresh_segments()
        self._refresh_result(dataset)
        self.refresh_plot()

    def _load_bridge(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None or self.bridge.currentData() is None:
            return
        bridge = int(self.bridge.currentData())
        metadata_all = dataset.metadata.get("bridges", {})
        metadata = metadata_all.get(str(bridge), {}) if isinstance(metadata_all, dict) else {}
        setting = self._bridge_setting(dataset, bridge)
        available = bool(metadata.get("available")) if isinstance(metadata, dict) else False
        self.availability.setText(
            f"{setting.get('name', f'Bridge {bridge}')} — "
            + ("Valid data" if available else "No valid data")
        )
        self.source.blockSignals(True)
        self.source.clear()
        columns = metadata.get("source_columns", []) if isinstance(metadata, dict) else []
        for key in columns:
            self.source.addItem(str(key), str(key))
        source_index = self.source.findData(setting.get("source"))
        self.source.setCurrentIndex(max(0, source_index))
        self.source.blockSignals(False)
        style_data = setting.get("style", {})
        style = (
            PlotStyle.from_dict(style_data)
            if isinstance(style_data, dict)
            else default_plot_style(bridge - 1)
        )
        style_controls = (
            self.line_style,
            self.marker,
            self.marker_size,
            self.marker_fill,
        )
        for control in style_controls:
            control.blockSignals(True)
        for control, key in (
            (self.sample_width, "width_cm"),
            (self.thickness, "thickness_cm"),
            (self.length, "length_cm"),
        ):
            control.setValue(float(setting.get(key, 0)))
        self.line_style.setCurrentText(style.line_style)
        self.marker.setCurrentText(style.marker)
        self.marker_size.setValue(style.marker_size)
        self.marker_fill.setText(style.marker_fill_color or "none")
        self.style_color.setStyleSheet(f"background-color: {style.color};")
        for control in style_controls:
            control.blockSignals(False)
        self._update_geometry_status(setting)

    def _source_changed(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None or self.source.currentData() is None:
            return
        bridge = int(self.bridge.currentData())
        setting = self._bridge_setting(dataset, bridge)
        source = str(self.source.currentData())
        setting["source"] = source
        if source == "resistance":
            setting.update({"kind": "resistance", "unit": "ohm"})
        elif source == "resistivity_ohm":
            configured = (
                dataset.metadata.get("bridges", {}).get(str(bridge), {}).get("configured_unit", "")
            )
            setting.update(
                {
                    "kind": "resistance"
                    if str(configured).lower() in {"ohm", "ohms"}
                    else "unknown",
                    "unit": "ohm",
                }
            )
        else:
            setting.update({"kind": "resistivity", "unit": source.removeprefix("resistivity_")})
        dataset.processed_data.clear()
        dataset.analysis_results.clear()
        self.state.modified = True
        self._update_geometry_status(setting)
        self.refresh_plot()

    def _save_bridge(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return
        setting = self._bridge_setting(dataset, int(self.bridge.currentData()))
        setting.update(
            {
                "geometry_mode": "width_thickness_length",
                "width_cm": self.sample_width.value(),
                "thickness_cm": self.thickness.value(),
                "length_cm": self.length.value(),
            }
        )
        dataset.processed_data.clear()
        dataset.analysis_results.clear()
        self.state.modified = True
        self._update_geometry_status(setting)
        self.refresh_plot()

    def _factor(self, setting: dict[str, Any]) -> float:
        return geometry_factor(
            "width_thickness_length",
            width_cm=float(setting.get("width_cm", 0)),
            thickness_cm=float(setting.get("thickness_cm", 0)),
            length_cm=float(setting.get("length_cm", 0)),
        )

    def _update_geometry_status(self, setting: dict[str, Any]) -> None:
        if setting.get("kind") == "resistivity":
            self.geometry_status.setText("Already geometry corrected; geometry is not applied.")
            return
        try:
            self.geometry_status.setText(f"A/L = {self._factor(setting):.8g} cm")
        except Exception:
            self.geometry_status.setText("Geometry incomplete; raw resistance remains available.")

    def _bridge_values(
        self, dataset: Dataset, bridge: int, *, resistivity: bool
    ) -> tuple[np.ndarray, np.ndarray | None]:
        setting = self._bridge_setting(dataset, bridge)
        source = str(setting.get("source", ""))
        if not source:
            raise ValueError(f"Bridge {bridge} has no selected source.")
        values = np.asarray(dataset.array(f"bridge_{bridge}_{source}"), dtype=float)
        error_key = f"bridge_{bridge}_standard_deviation"
        error = (
            np.asarray(dataset.array(error_key), dtype=float)
            if error_key in dataset.raw_data
            else None
        )
        kind = str(setting.get("kind", ""))
        if not resistivity:
            if kind != "resistance":
                raise ValueError(
                    f"Bridge {bridge} supplies already corrected resistivity, not raw resistance."
                )
            return values, error
        if kind == "resistivity":
            factor = {"ohm_cm": 1.0, "ohm_m": 100.0, "mohm_cm": 1e-3}.get(str(setting.get("unit")))
            converted = source_resistivity_to_ohm_cm(values, str(setting.get("unit")))
            return converted, None if error is None or factor is None else error * factor
        factor = self._factor(setting)
        return resistance_to_resistivity(values, factor), (
            None if error is None else error * factor
        )

    def _bridge_visibility_changed(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return
        for bridge, checkbox in self.bridge_checks.items():
            self._bridge_setting(dataset, bridge)["visible"] = checkbox.isChecked()
        self.state.modified = True
        self.refresh_plot()

    def _mode_changed(self) -> None:
        dataset = self.dataset_list.current_dataset()
        rho_t = self.plot_type.currentData() == "rho_t"
        desired_tab = 0 if rho_t else 1
        if self.plot_tabs.currentIndex() != desired_tab:
            self.plot_tabs.setCurrentIndex(desired_tab)
        self.display_mode.setEnabled(rho_t)
        self.run_fit.setEnabled(rho_t and self.display_mode.currentData() != "normal")
        if dataset is not None:
            dataset.metadata["plot_type"] = str(self.plot_type.currentData())
            dataset.metadata["selected_segment"] = 0
            self.state.modified = True
            self._refresh_segments()
        self.refresh_plot()

    def _plot_tab_changed(self, index: int) -> None:
        self.plot_type.setCurrentIndex(max(0, min(index, self.plot_type.count() - 1)))

    def _display_mode_changed(self) -> None:
        rho_t = self.plot_type.currentData() == "rho_t"
        self.run_fit.setEnabled(rho_t and self.display_mode.currentData() in {"arrhenius", "vrh"})
        self.refresh_plot()

    def _choose_style_color(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None or self.bridge.currentData() is None:
            return
        setting = self._bridge_setting(dataset, int(self.bridge.currentData()))
        stored = setting.get("style", {})
        style = (
            PlotStyle.from_dict(stored)
            if isinstance(stored, dict)
            else default_plot_style(int(self.bridge.currentData()) - 1)
        )
        color = QColorDialog.getColor(QColor(style.color), self)
        if color.isValid():
            style.color = color.name()
            setting["style"] = style.to_dict()
            dataset.plot_style = style
            self.dataset_list.refresh_style_indicators()
            self.style_color.setStyleSheet(f"background-color: {color.name()};")
            self.state.modified = True
            self.refresh_plot()

    def _apply_bridge_style(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None or self.bridge.currentData() is None:
            return
        setting = self._bridge_setting(dataset, int(self.bridge.currentData()))
        stored = setting.get("style", {})
        style = (
            PlotStyle.from_dict(stored)
            if isinstance(stored, dict)
            else default_plot_style(int(self.bridge.currentData()) - 1)
        )
        style.line_style = self.line_style.currentText()
        style.marker = self.marker.currentText()
        style.marker_size = self.marker_size.value()
        fill = self.marker_fill.text().strip()
        style.marker_fill_color = None if fill.lower() == "none" else fill
        setting["style"] = style.to_dict()
        dataset.plot_style = style
        self.dataset_list.refresh_style_indicators()
        self.state.modified = True
        self.refresh_plot()

    def _refresh_segments(self) -> None:
        dataset = self.dataset_list.current_dataset()
        self.segment.blockSignals(True)
        self.segment.clear()
        if dataset is not None:
            _selected, segments = self._selected_segment(dataset)
            for segment in segments:
                cycle = f" — cycle {segment.cycle + 1}" if segment.sweep_type == "field" else ""
                self.segment.addItem(segment.label + cycle, segment.segment_id)
            self.segment.setCurrentIndex(
                min(int(dataset.metadata.get("selected_segment", 0)), len(segments) - 1)
            )
            selected, _segments = self._selected_segment(dataset)
            maximum = len(dataset.array("temperature_k")) - 1
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
        if dataset is None or self.segment_start.value() > self.segment_stop.value():
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

    def _transform(
        self, temperature: np.ndarray, values: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, str, str]:
        mode = str(self.display_mode.currentData())
        vertical = "ρ" if self.vertical_unit.currentData() == "resistivity" else "R"
        if mode == "arrhenius":
            scale = 1000.0 if self.arrhenius_x.currentData() == "thousand" else 1.0
            return (
                scale / temperature,
                np.log(values),
                "1000/T (K⁻¹)" if scale == 1000 else "1/T (K⁻¹)",
                f"ln({vertical})",
            )
        if mode == "vrh":
            dimension = int(self.vrh_dimension.currentData())
            p = 1 / (dimension + 1)
            return temperature ** (-p), np.log(values), f"T^(-{p:.4g})", f"ln({vertical})"
        return (
            temperature,
            values,
            "Temperature (K)",
            ("ρ (ohm·cm)" if vertical == "ρ" else "R (ohm)"),
        )

    def build_plot_spec(self) -> PlotSpec:
        curves: list[CurveSpec] = []
        x_label, y_label = "Temperature (K)", "ρ (ohm·cm)"
        mode = str(self.plot_type.currentData())
        resistivity = self.vertical_unit.currentData() == "resistivity"
        for dataset in self.dataset_list.datasets():
            if not dataset.visible or dataset.metadata.get("measurement_container"):
                continue
            if dataset.metadata.get("measurement_parent") and dataset.metadata.get("measurement_branch", {}).get("mode") != mode:
                continue
            dataset.plot_warning = ""
            segment, _segments = self._selected_segment(dataset)
            indices = segment.indices
            for bridge in range(1, 5):
                setting = self._bridge_setting(dataset, bridge)
                if dataset.metadata.get("measurement_parent") and bridge != dataset.metadata["measurement_branch"]["bridge"]:
                    continue
                if not setting.get("visible"):
                    continue
                try:
                    values, _error = self._bridge_values(dataset, bridge, resistivity=resistivity)
                    temperature = np.asarray(dataset.array("temperature_k"), dtype=float)
                    if mode == "rho_h":
                        field_unit = FieldUnit(str(self.field_unit.currentData()))
                        x = convert_field(dataset.array("field_oe")[indices], field_unit)
                        y = values[indices]
                        x_label = f"Magnetic field ({field_unit.value})"
                        y_label = "ρ (ohm·cm)" if resistivity else "R (ohm)"
                    else:
                        x, y, x_label, y_label = self._transform(
                            temperature[indices], values[indices]
                        )
                    style_data = setting.get("style", {})
                    style = (
                        PlotStyle.from_dict(style_data)
                        if isinstance(style_data, dict)
                        else dataset.plot_style
                    )
                    curves.append(
                        CurveSpec(
                            x,
                            y,
                            f"{dataset.display_name} — {setting.get('name')} — {segment.label}",
                            style,
                        )
                    )
                    fit_key = (
                        f"bridge_{bridge}_arrhenius_fit"
                        if self.display_mode.currentData() == "arrhenius"
                        else f"bridge_{bridge}_vrh_fit"
                    )
                    if mode == "rho_t" and self.display_mode.currentData() in {"arrhenius", "vrh"} and fit_key in dataset.processed_data:
                        fit = np.asarray(dataset.processed_data[fit_key], dtype=float)[indices]
                        result = dataset.analysis_results.get(f"{self.display_mode.currentData()}_bridge_{bridge}", {})
                        fit_mask = (temperature[indices] >= result.get("minimum_k", -np.inf)) & (temperature[indices] <= result.get("maximum_k", np.inf)) & np.isfinite(fit)
                        fit_style = PlotStyle(
                            color=style.color,
                            line_style="--",
                            marker="none",
                            legend_text=f"{setting.get('name')} fit",
                        )
                        curves.append(CurveSpec(x[fit_mask], fit[fit_mask], fit_style.legend_text, fit_style))
                except Exception as exc:
                    dataset.plot_warning = f"Not plotted: {exc}"
                    continue
        return PlotSpec(
            x_label,
            y_label,
            curves,
            legend_enabled=self.legend.isChecked(),
            y_log_scale=self.log_y.isChecked() and self.display_mode.currentData() == "normal",
            figure_width_cm=self.figure_width.value(),
            figure_height_cm=self.figure_height.value(),
            dpi=self.figure_dpi.value(),
            transparent=self.transparent.isChecked(),
        )

    def refresh_plot(self) -> None:
        active_plot = self.rho_t_plot if self.plot_tabs.currentIndex() == 0 else self.rho_h_plot
        render_plot(active_plot, self.build_plot_spec())
        self.dataset_list.refresh_diagnostics()
        dataset = self.dataset_list.current_dataset()
        curves: list[CurveSpec] = []
        if dataset is not None:
            model = str(self.display_mode.currentData())
            bridge = int(self.bridge.currentData())
            key = f"bridge_{bridge}_{model}_fit"
            fitted = dataset.processed_data.get(key)
            if fitted is not None and model in {"arrhenius", "vrh"} and self.plot_type.currentData() == "rho_t":
                segment, _segments = self._selected_segment(dataset)
                temperature = np.asarray(dataset.array("temperature_k"), dtype=float)[
                    segment.indices
                ]
                values, _error = self._bridge_values(dataset, bridge, resistivity=self.vertical_unit.currentData() == "resistivity")
                with np.errstate(divide="ignore", invalid="ignore"):
                    residual_values = np.log(values) - np.asarray(fitted, dtype=float)
                result = dataset.analysis_results.get(f"{model}_bridge_{bridge}", {})
                mask = (temperature >= result.get("minimum_k", -np.inf)) & (temperature <= result.get("maximum_k", np.inf)) & np.isfinite(residual_values[segment.indices])
                curves.append(
                    CurveSpec(
                        temperature[mask],
                        residual_values[segment.indices][mask],
                        "Fit residual",
                        dataset.plot_style,
                    )
                )
        render_plot(
            self.residual_widget,
            PlotSpec("Temperature (K)", "ln-value residual", curves, legend_enabled=False),
        )
        set_residual_available(self.residual_widget, bool(curves))

    def refresh(self) -> None:
        self.dataset_list.refresh()
        self._load_selected(self.dataset_list.current_dataset())
        self.refresh_plot()

    def _run_fit(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None or self.plot_type.currentData() != "rho_t":
            return
        bridge = int(self.bridge.currentData())
        try:
            segment, _segments = self._selected_segment(dataset)
            values, error = self._bridge_values(
                dataset,
                bridge,
                resistivity=self.vertical_unit.currentData() == "resistivity",
            )
            indices = segment.indices
            temperature = dataset.array("temperature_k")[indices]
            fit_error = (
                error[indices] if error is not None and self.fit_weighted.isChecked() else None
            )
            model = str(self.display_mode.currentData())
            if model == "arrhenius":
                result = fit_arrhenius(
                    temperature,
                    values[indices],
                    minimum_k=self.fit_min.value(),
                    maximum_k=self.fit_max.value(),
                    error=fit_error,
                )
                key = f"bridge_{bridge}_arrhenius_fit"
            elif model == "vrh":
                result = fit_mott_vrh(
                    temperature,
                    values[indices],
                    dimension=int(self.vrh_dimension.currentData()),
                    minimum_k=self.fit_min.value(),
                    maximum_k=self.fit_max.value(),
                    error=fit_error,
                )
                key = f"bridge_{bridge}_vrh_fit"
            else:
                return
            full_fit = np.full(len(values), np.nan)
            full_residual = np.full(len(values), np.nan)
            full_fit[indices] = result.fitted_values
            transformed_y = np.log(values[indices])
            full_residual[indices] = transformed_y - result.fitted_values
            dataset.processed_data[key] = full_fit
            dataset.processed_data["transport_fit_residual"] = full_residual
            dataset.analysis_results[f"{model}_bridge_{bridge}"] = {
                "slope": result.slope,
                "intercept": result.intercept,
                "slope_error": result.slope_error,
                "intercept_error": result.intercept_error,
                "r_squared": result.r_squared,
                "point_count": result.point_count,
                "excluded_count": result.excluded_count,
                "activation_energy_ev": result.activation_energy_ev,
                "activation_energy_error_ev": result.activation_energy_error_ev,
                "characteristic_temperature_k": result.characteristic_temperature_k,
                "characteristic_temperature_error_k": result.characteristic_temperature_error_k,
                "prefactor": result.prefactor,
                "prefactor_error": result.prefactor_error,
                "minimum_k": self.fit_min.value(),
                "maximum_k": self.fit_max.value(),
                "bridge": bridge,
                "segment_id": segment.segment_id,
                "dimension": int(self.vrh_dimension.currentData()),
                "vertical_source": str(self.vertical_unit.currentData()),
            }
            from materials_data_studio.core.processing import record_operation
            parameters = {"x": "__transport_temperature", "y": "__transport_value", "bridge": bridge, "vertical_source": str(self.vertical_unit.currentData()), "sigma": "__transport_sigma" if fit_error is not None else None, "minimum_k": self.fit_min.value(), "maximum_k": self.fit_max.value()}
            if model == "vrh":
                parameters["dimension"] = int(self.vrh_dimension.currentData())
            record_operation(dataset, "mott_vrh" if model == "vrh" else "arrhenius", parameters)
            self.state.modified = True
            self._refresh_result(dataset)
            self.refresh_plot()
        except Exception as exc:
            show_error(self, "Transport Fit Error", str(exc))

    def _reset_fit(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return
        bridge = int(self.bridge.currentData())
        model = str(self.display_mode.currentData())
        dataset.analysis_results.pop(f"{model}_bridge_{bridge}", None)
        dataset.processed_data.pop(f"bridge_{bridge}_{model}_fit", None)
        dataset.processed_data.pop("transport_fit_residual", None)
        self.state.modified = True
        self._refresh_result(dataset)
        self.refresh_plot()

    def _refresh_result(self, dataset: Dataset) -> None:
        if self.bridge.currentData() is None:
            return
        bridge = int(self.bridge.currentData())
        model = str(self.display_mode.currentData())
        result = dataset.analysis_results.get(f"{model}_bridge_{bridge}")
        if not isinstance(result, dict):
            self.fit_result.setText("No transport fit.")
            return
        if model == "arrhenius":
            energy = float(result.get("activation_energy_ev", np.nan))
            headline = f"Ea = {energy:.8g} ± {float(result.get('activation_energy_error_ev', np.nan)):.3g} eV ({energy * 1000:.8g} meV)"
        else:
            headline = (
                f"T0 = {float(result.get('characteristic_temperature_k', np.nan)):.8g} ± {float(result.get('characteristic_temperature_error_k', np.nan)):.3g} K; "
                f"Mott VRH, d = {int(result.get('dimension', 3))}"
            )
        self.fit_result.setText(
            headline
            + f"\nPrefactor = {float(result.get('prefactor', np.nan)):.8g} ± {float(result.get('prefactor_error', np.nan)):.3g}"
            + f"\nSlope = {float(result.get('slope', np.nan)):.8g}; "
            + f"R² = {float(result.get('r_squared', np.nan)):.8g}"
            + f"\nIncluded {int(result.get('point_count', 0))}; "
            + f"excluded {int(result.get('excluded_count', 0))} points"
        )

    def export_data(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None or self.bridge.currentData() is None:
            return
        bridge = int(self.bridge.currentData())
        name, _ = QFileDialog.getSaveFileName(
            self,
            "Export Resistivity Data",
            f"{dataset.display_name}-bridge-{bridge}.csv",
            "Data (*.csv *.tsv *.dat *.itx)",
        )
        if not name:
            return
        try:
            resistance = resistivity = np.full(len(dataset.array("temperature_k")), np.nan)
            setting = self._bridge_setting(dataset, bridge)
            if setting.get("kind") == "resistance":
                resistance, resistance_error = self._bridge_values(
                    dataset, bridge, resistivity=False
                )
                with suppress(Exception):
                    resistivity, _ = self._bridge_values(dataset, bridge, resistivity=True)
            else:
                resistivity, resistance_error = self._bridge_values(
                    dataset, bridge, resistivity=True
                )
            segments = self._segments(dataset)
            segment_id = np.full(len(resistance), -1.0)
            nominal = np.full(len(resistance), np.nan)
            direction = np.full(len(resistance), 0.0)
            for segment in segments:
                segment_id[segment.indices] = segment.segment_id
                nominal[segment.indices] = segment.nominal_value
                direction[segment.indices] = (
                    1 if segment.direction in {"warming", "increasing"} else -1
                )
            temperature = np.asarray(dataset.array("temperature_k"), dtype=float)
            field = np.asarray(dataset.array("field_oe"), dtype=float)
            p = 1 / (int(self.vrh_dimension.currentData()) + 1)
            nan = np.full(len(temperature), np.nan)
            export_numeric(
                Path(name),
                {
                    "Measurement_order": dataset.array("measurement_order"),
                    "Temperature_K": temperature,
                    "Magnetic_Field_Oe": field,
                    "Magnetic_Field_T": field * 1e-4,
                    "Bridge_number": np.full(len(temperature), bridge),
                    "Resistance_raw_ohm": resistance,
                    "Resistivity_ohm_cm": resistivity,
                    "Resistance_error_ohm": resistance_error
                    if resistance_error is not None
                    else nan,
                    "Geometry_factor_A_over_L_cm": np.full(
                        len(temperature),
                        self._factor(setting) if setting.get("kind") == "resistance" else np.nan,
                    ),
                    "Nominal_control_value": nominal,
                    "Sweep_direction": direction,
                    "Segment_ID": segment_id,
                    "Inv_T_K_1": 1 / temperature,
                    "Thousand_over_T_K_1": 1000 / temperature,
                    "Ln_R_or_rho": np.log(
                        resistivity if np.any(np.isfinite(resistivity)) else resistance
                    ),
                    "T_power_minus_p": temperature ** (-p),
                    "Arrhenius_fit_value": dataset.processed_data.get(
                        f"bridge_{bridge}_arrhenius_fit", nan
                    ),
                    "VRH_fit_value": dataset.processed_data.get(f"bridge_{bridge}_vrh_fit", nan),
                },
                metadata=[dataset.display_name, f"Bridge {bridge}"],
            )
        except Exception as exc:
            show_error(self, "Resistivity Export Error", str(exc))

    def export_figure(self) -> None:
        name, selected_filter = QFileDialog.getSaveFileName(
            self, "Export Resistivity Figure", "resistivity.png", FIGURE_FILE_FILTERS
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
            "plot_type": str(self.plot_type.currentData()),
            "plot_tab": self.plot_tabs.currentIndex(),
            "vertical_unit": str(self.vertical_unit.currentData()),
            "display_mode": str(self.display_mode.currentData()),
            "arrhenius_x": str(self.arrhenius_x.currentData()),
            "field_unit": str(self.field_unit.currentData()),
            "log_y": self.log_y.isChecked(),
            "legend": self.legend.isChecked(),
            "figure_width": self.figure_width.value(),
            "figure_height": self.figure_height.value(),
            "figure_dpi": self.figure_dpi.value(),
            "transparent": self.transparent.isChecked(),
            "splitter_sizes": self.splitter.sizes(),
        }

    def restore_state(self, state: dict[str, Any]) -> None:
        for combo, key in (
            (self.plot_type, "plot_type"),
            (self.vertical_unit, "vertical_unit"),
            (self.display_mode, "display_mode"),
            (self.arrhenius_x, "arrhenius_x"),
            (self.field_unit, "field_unit"),
        ):
            index = combo.findData(state.get(key))
            if index >= 0:
                combo.setCurrentIndex(index)
        self.log_y.setChecked(bool(state.get("log_y", False)))
        self.legend.setChecked(bool(state.get("legend", True)))
        tab_index = int(
            state.get(
                "plot_tab",
                1 if state.get("plot_type") == "rho_h" else 0,
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
        apply_plot_theme(self.rho_t_plot, theme)
        apply_plot_theme(self.rho_h_plot, theme)
        apply_plot_theme(self.residual_widget, theme)
