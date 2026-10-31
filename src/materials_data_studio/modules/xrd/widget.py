"""Interactive XRD workspace connected to the pure analysis layer."""

from __future__ import annotations

import dataclasses
import hashlib
import logging
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING, Any, cast

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, Qt, QThreadPool, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from materials_data_studio.core.exceptions import InvalidAxisConversionError
from materials_data_studio.core.export import export_numeric
from materials_data_studio.core.models import (
    AxisType,
    Dataset,
    ModuleType,
    NormalizationMode,
    PlotStyle,
    source_sha256,
)
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.core.source_io import ImportedSource, import_source
from materials_data_studio.core.workers import Worker
from materials_data_studio.modules.xrd.background import arpls_background
from materials_data_studio.modules.xrd.cif_backend import (
    CifDocument,
    SpaceGroupInfo,
    UnitCell,
    constrain_unit_cell,
    extract_cif_symmetry,
    extract_cif_tables,
    load_cif,
    update_cif_tables,
)
from materials_data_studio.modules.xrd.cif_editor import CifEditorDialog
from materials_data_studio.modules.xrd.constants import WAVELENGTH_PRESETS_A
from materials_data_studio.modules.xrd.dataset_tree import XrdDatasetTree
from materials_data_studio.modules.xrd.models import TofCalibration
from materials_data_studio.modules.xrd.parser import (
    XrdParseResult,
    parse_xrd_rasx,
    parse_xrd_text,
)
from materials_data_studio.modules.xrd.peak_fitting.models import PeakFitResult
from materials_data_studio.modules.xrd.simulation import (
    OccupancyPreviewModel,
    SimulatedPattern,
    broaden_pattern,
    display_pattern_data,
    preview_pattern_for_cell,
    preview_pattern_for_occupancies,
    restore_occupancy_preview_model,
    simulate_cif,
    store_occupancy_preview_model,
)
from materials_data_studio.modules.xrd.transforms import (
    convert_x_axis,
    normalization_factor,
    process_intensity,
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
    install_legend_toggle,
    render_plot,
    update_rendered_plot_data,
)
from materials_data_studio.plotting.styles import default_plot_style
from materials_data_studio.ui.dialogs import show_error, show_warning
from materials_data_studio.ui.numeric_inputs import (
    FlexibleDoubleSpinBox as QDoubleSpinBox,
)
from materials_data_studio.ui.panels import dataset_context, install_settings_panel, reveal_settings
from materials_data_studio.ui.style_inputs import StyleCombo
from materials_data_studio.ui.tasks import run_task
from materials_data_studio.ui.workspace_layout import WorkspaceSplitter, install_workspace

LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from materials_data_studio.modules.xrd.peak_fitting.fitting_window import (
        XrdPeakFittingWindow,
    )

AXIS_LABELS = {
    AxisType.TWO_THETA: "2θ (deg)",
    AxisType.D_SPACING: "d (Å)",
    AxisType.INV_D: "1/d (Å⁻¹)",
    AxisType.TOF: "TOF",
}
AXIS_EXPORT_NAMES = {
    AxisType.TWO_THETA: "TwoTheta_deg",
    AxisType.D_SPACING: "d_A",
    AxisType.INV_D: "InvD_A^-1",
    AxisType.TOF: "TOF",
}


@dataclasses.dataclass(slots=True)
class _LiveCifRequest:
    generation: int
    source_path: Path
    document: CifDocument
    settings: dict[str, Any]
    workspace_generation: int = 0


class XrdWidget(QWidget):
    """XRD dataset comparison, transforms, background, and export."""

    _LATTICE_SLIDER_CENTER = 100_000
    _ANGLE_SLIDER_SCALE = 1_000
    _LIVE_CIF_SETTLE_MS = 90

    def __init__(self, state: ProjectState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._workers: set[Worker] = set()
        self.current_spec = PlotSpec("2θ (deg)", "Intensity")
        self.dataset_list = XrdDatasetTree(state, ModuleType.XRD)
        self.plot_widget = create_plot_widget()
        self._lattice_loading = False
        self._lattice_slider_active = False
        self._lattice_baseline: dict[str, float] = {}
        self._occupancy_values: dict[int, float] = {}
        self._occupancy_labels: dict[int, str] = {}
        self._occupancy_preview_models: dict[str, OccupancyPreviewModel] = {}
        self._lattice_generation = 0
        self._live_cif_worker: Worker | None = None
        self._pending_live_cif: _LiveCifRequest | None = None
        self._live_cif_previews: dict[str, SimulatedPattern] = {}
        self._peak_fitting_window: XrdPeakFittingWindow | None = None
        self._peak_fitting_state: dict[str, Any] = {}
        self._phase_search_window: Any = None
        self._phase_search_state: dict[str, Any] = {}
        self._selected_symmetry: SpaceGroupInfo | None = None
        self._lattice_update_timer = QTimer(self)
        self._lattice_update_timer.setSingleShot(True)
        self._lattice_update_timer.setInterval(self._LIVE_CIF_SETTLE_MS)
        self._lattice_update_timer.timeout.connect(self._apply_lattice_parameters)
        self._peak_label = pg.TextItem(anchor=(0.5, 1.15))
        self._peak_label.setZValue(1000)
        self._peak_label.hide()
        cast(Any, self.plot_widget.scene()).sigMouseMoved.connect(self._show_cif_peak_hover)
        self._build_controls()
        self.legend_context_action = install_legend_toggle(
            self.plot_widget,
            self.legend_enabled.setChecked,
            checked=self.legend_enabled.isChecked(),
        )
        self.legend_enabled.toggled.connect(self._sync_legend_context_action)
        self._build_layout()
        self.dataset_list.current_dataset_changed.connect(self._load_selected)
        self.dataset_list.dataset_activated.connect(self._edit_dataset_if_cif)
        self.dataset_list.datasets_changed.connect(self.refresh_plot)
        self.dataset_list.datasets_changed.connect(self._update_peak_fitting_availability)
        self.refresh()

    def _build_controls(self) -> None:
        self.display_axis = QComboBox()
        for axis, label in AXIS_LABELS.items():
            self.display_axis.addItem(label, axis.value)
        self.display_axis.currentIndexChanged.connect(self.refresh_plot)
        self.log_y_axis = QCheckBox("Logarithmic intensity axis")
        self.log_y_axis.toggled.connect(self._log_scale_changed)

        self.wavelength_preset = QComboBox()
        self.wavelength_preset.addItems(list(WAVELENGTH_PRESETS_A))
        self.wavelength_preset.currentTextChanged.connect(self._preset_changed)
        self.wavelength = QDoubleSpinBox()
        self.wavelength.setDecimals(6)
        self.wavelength.setRange(0.01, 10.0)
        self.wavelength.setValue(1.540598)
        self.wavelength.valueChanged.connect(self._apply_selected_settings)
        self.apply_wavelength_all = QPushButton("Apply Wavelength to All")
        self.apply_wavelength_all.clicked.connect(self._apply_wavelength_to_all)

        self.normalization = QComboBox()
        for mode, label in (
            (NormalizationMode.NONE, "No normalization"),
            (NormalizationMode.MAX_1, "Maximum = 1"),
            (NormalizationMode.MAX_100, "Maximum = 100"),
            (NormalizationMode.AREA, "Integrated area"),
        ):
            self.normalization.addItem(label, mode.value)
        self.normalization.currentIndexChanged.connect(self._apply_selected_settings)
        self.apply_normalization_all = QPushButton("Apply Normalization to All")
        self.apply_normalization_all.clicked.connect(self._apply_normalization_to_all)
        self.scale = QDoubleSpinBox()
        self.scale.setRange(-1e9, 1e9)
        self.scale.setDecimals(6)
        self.scale.setValue(1.0)
        self.scale.valueChanged.connect(self._apply_selected_settings)
        self.offset = QDoubleSpinBox()
        self.offset.setRange(-1e12, 1e12)
        self.offset.setDecimals(6)
        self.offset.valueChanged.connect(self._apply_selected_settings)
        self.stacking = QComboBox()
        for label, value in (
            ("No stacking", "none"),
            ("Constant offset", "constant"),
            ("Automatic offset", "automatic"),
            ("Individual offsets", "individual"),
        ):
            self.stacking.addItem(label, value)
        self.stacking.currentIndexChanged.connect(self.refresh_plot)
        self.stacking_offset = QDoubleSpinBox()
        self.stacking_offset.setRange(-1e12, 1e12)
        self.stacking_offset.setValue(100)
        self.stacking_offset.valueChanged.connect(self.refresh_plot)
        self.stack_value_label = QLabel("Stack offset")

        self.difa = QDoubleSpinBox()
        self.difc = QDoubleSpinBox()
        self.tzero = QDoubleSpinBox()
        for field in (self.difa, self.difc, self.tzero):
            field.setRange(-1e12, 1e12)
            field.setDecimals(6)
            field.valueChanged.connect(self._apply_selected_settings)

        self.style_color = QPushButton("Choose…")
        self.style_color.clicked.connect(self._choose_style_color)
        self.line_style = StyleCombo("line")
        self.line_style.addItems(["-", "--", ":", "-.", "none"])
        self.line_width = QDoubleSpinBox()
        self.line_width.setRange(0, 20)
        self.line_width.setValue(1.6)
        self.marker = StyleCombo("marker")
        self.marker.addItems(["none", "o", "s", "^", "v", "d", "x", "+"])
        self.marker_size = QDoubleSpinBox()
        self.marker_size.setRange(0, 50)
        self.marker_size.setValue(6)
        self.legend_text = QLineEdit()
        for control in (
            self.line_style,
            self.line_width,
            self.marker,
            self.marker_size,
            self.legend_text,
        ):
            if isinstance(control, QComboBox):
                control.currentIndexChanged.connect(self._apply_style)
            elif isinstance(control, QLineEdit):
                control.editingFinished.connect(self._apply_style)
            else:
                control.valueChanged.connect(self._apply_style)
        reset_style = QPushButton("Reset Style")
        reset_style.clicked.connect(self._reset_style)
        self.reset_style_button = reset_style
        self.edit_cif_button = QPushButton("Edit CIF")
        self.edit_cif_button.clicked.connect(self._edit_selected_cif)
        self.lattice_fields: dict[str, QDoubleSpinBox] = {}
        self.lattice_sliders: dict[str, QSlider] = {}
        for name in ("a", "b", "c", "alpha", "beta", "gamma"):
            field = QDoubleSpinBox()
            field.setDecimals(8)
            field.setMinimumWidth(104)
            field.setRange(
                0.000001,
                1_000_000.0 if name in {"a", "b", "c"} else 179.999999,
            )
            field.valueChanged.connect(
                lambda _value, parameter=name: self._lattice_field_changed(parameter)
            )
            self.lattice_fields[name] = field
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setSingleStep(1)
            if name in {"a", "b", "c"}:
                slider.setRange(50_000, 150_000)
                slider.setValue(self._LATTICE_SLIDER_CENTER)
                slider.setPageStep(1_000)
            else:
                slider.setRange(1, 179_999)
                slider.setValue(90 * self._ANGLE_SLIDER_SCALE)
                slider.setPageStep(100)
            slider.valueChanged.connect(
                lambda value, parameter=name: self._lattice_slider_changed(parameter, value)
            )
            slider.sliderReleased.connect(self._flush_lattice_recalculation)
            self.lattice_sliders[name] = slider
        self.lattice_relative_scale = QDoubleSpinBox()
        self.lattice_relative_scale.setDecimals(6)
        self.lattice_relative_scale.setRange(0.0, 1.0e6)
        self.lattice_relative_scale.setValue(1.0)
        self.lattice_relative_scale.valueChanged.connect(
            lambda _value: self._lattice_profile_field_changed("relative_scale")
        )
        self.lattice_relative_scale_slider = QSlider(Qt.Orientation.Horizontal)
        self.lattice_relative_scale_slider.setRange(0, 500_000)
        self.lattice_relative_scale_slider.setValue(self._LATTICE_SLIDER_CENTER)
        self.lattice_relative_scale_slider.setSingleStep(1)
        self.lattice_relative_scale_slider.setPageStep(1_000)
        self.lattice_relative_scale_slider.valueChanged.connect(
            lambda value: self._lattice_profile_slider_changed("relative_scale", value)
        )
        self.lattice_fwhm = QDoubleSpinBox()
        self.lattice_fwhm.setDecimals(4)
        self.lattice_fwhm.setRange(0.001, 20.0)
        self.lattice_fwhm.setValue(0.15)
        self.lattice_fwhm.valueChanged.connect(
            lambda _value: self._lattice_profile_field_changed("fwhm_deg")
        )
        self.lattice_fwhm_slider = QSlider(Qt.Orientation.Horizontal)
        self.lattice_fwhm_slider.setRange(1_000, 500_000)
        self.lattice_fwhm_slider.setValue(self._LATTICE_SLIDER_CENTER)
        self.lattice_fwhm_slider.setSingleStep(1)
        self.lattice_fwhm_slider.setPageStep(1_000)
        self.lattice_fwhm_slider.valueChanged.connect(
            lambda value: self._lattice_profile_slider_changed("fwhm_deg", value)
        )
        self.lattice_symmetry = QLabel()
        self.lattice_symmetry.setWordWrap(True)
        self.occupancy_site = QComboBox()
        self.occupancy_site.currentIndexChanged.connect(self._occupancy_site_changed)
        self.occupancy_value = QDoubleSpinBox()
        self.occupancy_value.setDecimals(6)
        self.occupancy_value.setRange(0.0, 1.0)
        self.occupancy_value.setSingleStep(0.01)
        self.occupancy_value.valueChanged.connect(self._occupancy_field_changed)
        self.occupancy_slider = QSlider(Qt.Orientation.Horizontal)
        self.occupancy_slider.setRange(0, 10_000)
        self.occupancy_slider.setValue(10_000)
        self.occupancy_slider.setSingleStep(1)
        self.occupancy_slider.setPageStep(100)
        self.occupancy_slider.valueChanged.connect(self._occupancy_slider_changed)
        self.occupancy_slider.sliderReleased.connect(self._flush_lattice_recalculation)

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
        self.compatibility_warning = QLabel()
        self.compatibility_warning.setWordWrap(True)
        self.compatibility_warning.setStyleSheet("color: #A16207;")

        self.arpls_smoothness = QDoubleSpinBox()
        self.arpls_smoothness.setDecimals(1)
        self.arpls_smoothness.setRange(1.0, 1.0e15)
        self.arpls_smoothness.setValue(1.0e5)
        self.arpls_smoothness.setSingleStep(1.0e5)
        self.arpls_ratio = QDoubleSpinBox()
        self.arpls_ratio.setDecimals(6)
        self.arpls_ratio.setRange(0.000001, 0.5)
        self.arpls_ratio.setValue(0.01)
        self.arpls_iterations = QSpinBox()
        self.arpls_iterations.setRange(1, 1000)
        self.arpls_iterations.setValue(50)
        estimate = QPushButton("Estimate Background")
        apply = QPushButton("Apply Background")
        clear = QPushButton("Clear Background")
        estimate.clicked.connect(self._estimate_background)
        apply.clicked.connect(lambda: self._set_background_enabled(True))
        clear.clicked.connect(self._clear_background)
        self.background_buttons = (estimate, apply, clear)
        self.open_peak_fitting_button = QPushButton("Open Peak Fitting")
        self.open_peak_fitting_button.setProperty("primary", True)
        self.open_peak_fitting_button.clicked.connect(self._open_peak_fitting)
        self.peak_fitting_availability = QLabel()
        self.peak_fitting_availability.setWordWrap(True)
        self.open_phase_search_button = QPushButton("Open Phase Search–Match")
        self.open_phase_search_button.setProperty("primary", True)
        self.open_phase_search_button.clicked.connect(self._open_phase_search)

    def _build_layout(self) -> None:
        left = QGroupBox("Datasets")
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(self.dataset_list)
        import_button = QPushButton("Import XRD Data")
        import_button.setProperty("primary", True)
        import_button.clicked.connect(self.choose_import_files)
        left_layout.addWidget(import_button)

        center = QGroupBox("Interactive Plot")
        center_layout = QVBoxLayout(center)
        center_layout.addWidget(self.plot_widget)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        axis_card = QGroupBox("Axis")
        self.axis_card = axis_card
        axis_form = QFormLayout(axis_card)
        self.axis_form = axis_form
        axis_form.addRow("Display axis", self.display_axis)
        axis_form.addRow(self.log_y_axis)
        axis_form.addRow("Wavelength preset", self.wavelength_preset)
        axis_form.addRow("Wavelength (Å)", self.wavelength)
        axis_form.addRow(self.apply_wavelength_all)
        axis_form.addRow("TOF DIFA", self.difa)
        axis_form.addRow("TOF DIFC", self.difc)
        axis_form.addRow("TOF TZERO", self.tzero)
        axis_form.addRow(self.compatibility_warning)
        processing_card = QGroupBox("Processing")
        self.processing_card = processing_card
        processing_form = QFormLayout(processing_card)
        processing_form.addRow("Normalization", self.normalization)
        processing_form.addRow(self.apply_normalization_all)
        processing_form.addRow("Scale", self.scale)
        processing_form.addRow("Offset", self.offset)
        processing_form.addRow("Stacking", self.stacking)
        processing_form.addRow(self.stack_value_label, self.stacking_offset)
        style_card = QGroupBox("Dataset Style")
        style_form = QFormLayout(style_card)
        style_form.addRow("Color", self.style_color)
        style_form.addRow("Line style", self.line_style)
        style_form.addRow("Line width", self.line_width)
        style_form.addRow("Marker", self.marker)
        style_form.addRow("Marker size", self.marker_size)
        style_form.addRow("Legend text", self.legend_text)
        self.phase_position_form = QFormLayout()
        self.phase_position_card = QGroupBox("Phase annotation position")
        self.phase_position_card.setLayout(self.phase_position_form)
        self.phase_lift = QDoubleSpinBox()
        self.phase_lift.setRange(0, 1000)
        self.phase_lift.valueChanged.connect(self._apply_phase_position)
        self.phase_tick_offset = QDoubleSpinBox()
        self.phase_tick_offset.setRange(-1000, 1000)
        self.phase_tick_offset.valueChanged.connect(self._apply_phase_position)
        self.phase_tick_length = QDoubleSpinBox()
        self.phase_tick_length.setRange(.01, 100)
        self.phase_tick_length.valueChanged.connect(self._apply_phase_position)
        self.phase_position_form.addRow("Above peak (%)", self.phase_lift)
        self.phase_position_form.addRow("Below pattern (%)", self.phase_tick_offset)
        self.phase_position_form.addRow("Tick length (%)", self.phase_tick_length)
        style_form.addRow(self.phase_position_card)
        style_form.addRow(self.reset_style_button)
        style_form.addRow(self.edit_cif_button)
        figure_card = QGroupBox("Figure Export")
        figure_form = QFormLayout(figure_card)
        figure_form.addRow(self.legend_enabled)
        figure_form.addRow("Width (cm)", self.figure_width)
        figure_form.addRow("Height (cm)", self.figure_height)
        figure_form.addRow("DPI", self.figure_dpi)
        figure_form.addRow(self.transparent)
        figure_form.addRow(self.export_current_view)
        self.background_card = QGroupBox("arPLS Background")
        background_layout = QVBoxLayout(self.background_card)
        form = QFormLayout()
        form.addRow("Smoothness λ", self.arpls_smoothness)
        form.addRow("Convergence ratio", self.arpls_ratio)
        form.addRow("Maximum iterations", self.arpls_iterations)
        background_layout.addLayout(form)
        for button in self.background_buttons:
            background_layout.addWidget(button)
        self.lattice_card = QGroupBox("Lattice Parameters")
        lattice_form = QFormLayout(self.lattice_card)
        lattice_form.addRow("Symmetry", self.lattice_symmetry)
        for name, label in (
            ("a", "a (Å)"),
            ("b", "b (Å)"),
            ("c", "c (Å)"),
            ("alpha", "α (deg)"),
            ("beta", "β (deg)"),
            ("gamma", "γ (deg)"),
        ):
            row = QHBoxLayout()
            row.addWidget(self.lattice_fields[name])
            row.addWidget(self.lattice_sliders[name], 1)
            lattice_form.addRow(label, row)
        scale_row = QHBoxLayout()
        scale_row.addWidget(self.lattice_relative_scale)
        scale_row.addWidget(self.lattice_relative_scale_slider, 1)
        lattice_form.addRow("Relative scale", scale_row)
        fwhm_row = QHBoxLayout()
        fwhm_row.addWidget(self.lattice_fwhm)
        fwhm_row.addWidget(self.lattice_fwhm_slider, 1)
        lattice_form.addRow("FWHM (deg)", fwhm_row)
        occupancy_heading = QLabel("Site occupancy")
        occupancy_heading.setProperty("sectionHeading", True)
        lattice_form.addRow(occupancy_heading)
        lattice_form.addRow("Atom site", self.occupancy_site)
        occupancy_row = QHBoxLayout()
        occupancy_row.addWidget(self.occupancy_value)
        occupancy_row.addWidget(self.occupancy_slider, 1)
        lattice_form.addRow("Occupancy", occupancy_row)
        live_note = QLabel("Changes are symmetry-constrained and recalculated automatically.")
        live_note.setWordWrap(True)
        lattice_form.addRow(live_note)
        self.lattice_card.setVisible(False)
        self.peak_fitting_card = QGroupBox("CIF-Constrained Peak Fitting")
        peak_fitting_layout = QVBoxLayout(self.peak_fitting_card)
        peak_fitting_layout.addWidget(self.open_phase_search_button)
        peak_fitting_layout.addWidget(self.open_peak_fitting_button)
        peak_fitting_layout.addWidget(self.peak_fitting_availability)
        right_layout.addWidget(axis_card)
        right_layout.addWidget(processing_card)
        right_layout.addWidget(style_card)
        right_layout.addWidget(figure_card)
        right_layout.addWidget(self.background_card)
        right_layout.addWidget(self.lattice_card)
        right_layout.addWidget(self.peak_fitting_card)
        right_layout.addStretch(1)
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        install_settings_panel(right_scroll, right, "xrd", "Selected dataset")

        splitter = WorkspaceSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(center)
        splitter.addWidget(right_scroll)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 4)
        splitter.setStretchFactor(2, 1)
        splitter.setSizes([280, 900, 320])
        self.splitter = splitter
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        install_workspace(layout, splitter, "xrd")

    def choose_import_files(self) -> None:
        """Open a multi-file XRD chooser."""
        names, _ = QFileDialog.getOpenFileNames(
            self,
            "Import XRD Data",
            "",
            "XRD data (*.xy *.dat *.txt *.csv *.rasx *.cif);;CIF structure (*.cif);;"
            "RASX profile (*.rasx);;All files (*)",
        )
        if names:
            self.import_files([Path(name) for name in names])

    def import_files(self, paths: list[Path]) -> None:
        """Import multiple XRD text files with per-file source-axis selection."""
        for path in paths:
            try:
                if path.suffix.lower() == ".cif":
                    self._import_cif(path)
                    continue
                parser = parse_xrd_rasx if path.suffix.lower() == ".rasx" else parse_xrd_text
                run_task(
                    self,
                    f"Import {path.name}",
                    lambda source=path, reader=parser: import_source(source, reader),
                    lambda result, source=path: self._finish_text_import(source, result),
                    state=self.state,
                )
            except Exception as exc:
                LOGGER.exception("XRD import failed for %s", path)
                show_error(self, "XRD Import Error", f"{path.name}: {exc}")

    def _finish_text_import(self, path: Path, result: object) -> None:
        """Complete an XRD import on the GUI thread after background parsing."""
        imported = result if isinstance(result, ImportedSource) else None
        result = imported.result if imported else result
        if not isinstance(result, XrdParseResult):
            self._import_error(path, "The parser returned an invalid result.", "")
            return
        preview = result
        import_options = {}
        if result.ambiguous and path.suffix.lower() != ".rasx":
            from materials_data_studio.ui.import_preview import ImportPreview
            dialog = ImportPreview(path, imported.data if imported else path.read_bytes(), self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                dialog.deleteLater()
                return
            try:
                preview = dialog.arrays()
                import_options = dialog.options()
            except Exception as exc:
                self._import_error(path, str(exc), "")
                dialog.deleteLater()
                return
            dialog.deleteLater()
        source_axis = AxisType.TOF if import_options.get("axis") == "tof" else AxisType.TWO_THETA
        style = default_plot_style(len(self.dataset_list.datasets()), legend_text=path.stem)
        dataset = Dataset(
            display_name=path.stem,
            module_type=ModuleType.XRD,
            source_filename=str(path),
            source_hash=imported.sha256 if imported else source_sha256(path),
            source_bytes=imported.data if imported else path.read_bytes(),
            raw_data={"x": preview.x, "intensity": preview.intensity, **({"intensity_error": preview.intensity_error} if preview.intensity_error is not None else {})},
            metadata={
                "source_axis": source_axis.value,
                "import_options": import_options,
                "intensity_unit": import_options.get("y_unit", "counts"),
                "source_format": path.suffix.lower().removeprefix(".") or "text",
                "wavelength_a": 1.540598,
                "radiation": "Cu Kα1/2",
                "normalization": NormalizationMode.NONE.value,
                "scale": 1.0,
                "offset": 0.0,
                "background_enabled": False,
                "fit_ranges": [],
                "excluded_ranges": [],
            },
            warnings=preview.warnings,
            plot_style=style,
        )
        self.state.add_dataset(dataset)
        if preview.invalid_rows:
            LOGGER.warning(
                "%s: imported %d valid rows and rejected %d rows",
                path,
                preview.valid_rows,
                preview.invalid_rows,
            )
        self.refresh()

    def _import_error(self, path: Path, message: str, traceback_text: str) -> None:
        LOGGER.error("Import failed for %s: %s\n%s", path, message, traceback_text)
        show_error(self, "XRD Import Error", f"{path.name}: {message}")

    def _import_cif(self, path: Path) -> None:
        """Edit and simulate a CIF as a non-destructive phase dataset."""
        document = load_cif(path)
        dialog = CifEditorDialog(document, self, simulation_settings={"radiation": self.wavelength_preset.currentText(), "wavelength_a": self.wavelength.value()})
        if dialog.exec() != CifEditorDialog.DialogCode.Accepted:
            return
        settings = {
            "wavelength_a": dialog.wavelength.value(),
            "radiation": dialog.radiation.currentText(),
            "two_theta_range": (
                dialog.two_theta_min.value(),
                dialog.two_theta_max.value(),
            ),
            "fwhm_deg": dialog.fwhm.value() if dialog.broadened.isChecked() else None,
            "profile_step_deg": dialog.profile_step.value(),
            "relative_scale": dialog.relative_scale.value(),
            "vertical_offset": dialog.vertical_offset.value(),
            "target_dataset_id": None,
        }
        self._queue_cif(path, document, settings)

    def _queue_cif(self, path: Path, document: CifDocument, settings: dict[str, Any]) -> None:
        target = next((d for d in self.state.datasets if d.dataset_id == settings.get("target_dataset_id")), None)
        run_task(self, "Calculate CIF pattern", lambda: simulate_cif(document,
                 wavelength_a=settings["wavelength_a"], two_theta_range=settings["two_theta_range"],
                 fwhm_deg=settings["fwhm_deg"], profile_step_deg=settings["profile_step_deg"], radiation=settings.get("radiation", "Custom")),
                 lambda result: self._finish_cif_import(path, document, settings, result), state=self.state, dataset=target)

    def _finish_cif_import(
        self,
        path: Path,
        document: object,
        settings: dict[str, Any],
        result: object,
    ) -> None:
        """Create a phase dataset after background diffraction simulation."""
        if not isinstance(document, CifDocument) or not isinstance(result, SimulatedPattern):
            self._import_error(path, "The CIF simulator returned an invalid result.", "")
            return
        pattern = result
        symmetry = extract_cif_symmetry(document)
        x_values, intensity = display_pattern_data(pattern, settings.get("radiation", "Custom"))
        assert x_values is not None and intensity is not None
        style = default_plot_style(len(self.dataset_list.datasets()), legend_text=path.stem)
        style.line_style = "-"
        style.marker = "none"
        target_id = settings.get("target_dataset_id")
        target = next(
            (dataset for dataset in self.state.datasets if dataset.dataset_id == target_id),
            None,
        )
        if target is not None:
            target.processed_data.clear()
            target.processed_data["x"] = np.asarray(x_values, dtype=float)
            target.processed_data["intensity"] = np.asarray(intensity, dtype=float)
            target.metadata.update(
                {
                    "wavelength_a": settings["wavelength_a"],
                    "radiation": settings.get("radiation", "Custom"),
                    "scale": settings["relative_scale"],
                    "offset": settings["vertical_offset"],
                    "edited_cif": document.edited_text,
                    "stick_two_theta_deg": pattern.two_theta_deg.tolist(),
                    "stick_intensity": pattern.intensity.tolist(),
                    "stick_hkl_labels": pattern.hkl_labels or [],
                    "stick_d_spacing_a": (
                        pattern.d_spacing_a.tolist() if pattern.d_spacing_a is not None else []
                    ),
                    "space_group_name": symmetry.name,
                    "space_group_number": symmetry.number,
                    "crystal_system": symmetry.crystal_system,
                    "fwhm_deg": settings["fwhm_deg"],
                    "profile_step_deg": settings["profile_step_deg"],
                    "two_theta_range": list(settings["two_theta_range"]),
                    "background_enabled": False,
                    "background_preview": False,
                }
            )
            target.plot_style.line_style = "-"
            target.plot_style.marker = "none"
            if pattern.occupancy_model is not None:
                self._occupancy_preview_models[target.dataset_id] = pattern.occupancy_model
            store_occupancy_preview_model(
                pattern.occupancy_model, target.processed_data, target.metadata
            )
            self.state.modified = True
            self.refresh()
            return
        dataset = Dataset(
            display_name=path.stem,
            module_type=ModuleType.XRD,
            source_filename=str(path),
            source_hash=hashlib.sha256(document.source_bytes or document.original_text.encode("utf-8")).hexdigest(),
            source_bytes=document.source_bytes or document.original_text.encode("utf-8"),
            raw_data={"x": x_values, "intensity": intensity},
            metadata={
                "source_axis": AxisType.TWO_THETA.value,
                "wavelength_a": settings["wavelength_a"],
                "radiation": settings.get("radiation", "Custom"),
                "normalization": NormalizationMode.NONE.value,
                "scale": settings["relative_scale"],
                "offset": settings["vertical_offset"],
                "background_enabled": False,
                "fit_ranges": [],
                "excluded_ranges": [],
                "is_simulated_phase": True,
                "edited_cif": document.edited_text,
                "stick_two_theta_deg": pattern.two_theta_deg.tolist(),
                "stick_intensity": pattern.intensity.tolist(),
                "stick_hkl_labels": pattern.hkl_labels or [],
                "stick_d_spacing_a": (
                    pattern.d_spacing_a.tolist() if pattern.d_spacing_a is not None else []
                ),
                "space_group_name": symmetry.name,
                "space_group_number": symmetry.number,
                "crystal_system": symmetry.crystal_system,
                "fwhm_deg": settings["fwhm_deg"],
                "profile_step_deg": settings["profile_step_deg"],
                "two_theta_range": list(settings["two_theta_range"]),
            },
            plot_style=style,
        )
        self.state.add_dataset(dataset)
        if pattern.occupancy_model is not None:
            self._occupancy_preview_models[dataset.dataset_id] = pattern.occupancy_model
        store_occupancy_preview_model(
            pattern.occupancy_model, dataset.processed_data, dataset.metadata
        )
        self.refresh()

    def _edit_selected_cif(self) -> None:
        """Reopen an imported phase, preserve its source, and recalculate after edits."""
        dataset = self.dataset_list.current_dataset()
        if dataset is None or not dataset.metadata.get("is_simulated_phase"):
            show_warning(
                self,
                "CIF Editor",
                "Select a simulated CIF phase before opening the structure editor.",
            )
            return
        source_path = Path(dataset.source_filename)
        original_text = (
            dataset.source_bytes.decode("utf-8", errors="replace")
            if dataset.source_bytes is not None
            else str(dataset.metadata.get("edited_cif", ""))
        )
        document = CifDocument(
            source_path,
            original_text,
            str(dataset.metadata.get("edited_cif", original_text)),
        )
        dialog = CifEditorDialog(
            document,
            self,
            simulation_settings={
                "wavelength_a": dataset.metadata.get("wavelength_a", 1.540598),
                "radiation": dataset.metadata.get("radiation", "Custom"),
                "two_theta_range": dataset.metadata.get("two_theta_range", (1.0, 90.0)),
                "fwhm_deg": dataset.metadata.get("fwhm_deg", 0.15),
                "profile_step_deg": dataset.metadata.get("profile_step_deg", 0.01),
                "relative_scale": dataset.metadata.get("scale", 1.0),
                "vertical_offset": dataset.metadata.get("offset", 0.0),
            },
        )
        if dialog.exec() != CifEditorDialog.DialogCode.Accepted:
            return
        settings: dict[str, Any] = {
            "wavelength_a": dialog.wavelength.value(),
            "radiation": dialog.radiation.currentText(),
            "two_theta_range": (
                dialog.two_theta_min.value(),
                dialog.two_theta_max.value(),
            ),
            "fwhm_deg": dialog.fwhm.value() if dialog.broadened.isChecked() else None,
            "profile_step_deg": dialog.profile_step.value(),
            "relative_scale": dialog.relative_scale.value(),
            "vertical_offset": dialog.vertical_offset.value(),
            "target_dataset_id": dataset.dataset_id,
        }
        self._queue_cif(source_path, document, settings)

    def _apply_lattice_parameters(self) -> None:
        """Apply live symmetry-constrained cell values and recalculate the pattern."""
        dataset = self.dataset_list.current_dataset()
        if dataset is None or not dataset.metadata.get("is_simulated_phase"):
            return
        source_path = Path(dataset.source_filename)
        original_text = (
            dataset.source_bytes.decode("utf-8", errors="replace")
            if dataset.source_bytes is not None
            else str(dataset.metadata.get("edited_cif", ""))
        )
        document = CifDocument(
            source_path,
            original_text,
            str(dataset.metadata.get("edited_cif", original_text)),
        )
        try:
            _old_cell, sites, _warnings = extract_cif_tables(document)
            for index, occupancy in self._occupancy_values.items():
                if 0 <= index < len(sites):
                    sites[index].occupancy = occupancy
            symmetry = self._selected_symmetry or extract_cif_symmetry(document)
            cell = UnitCell(**{name: field.value() for name, field in self.lattice_fields.items()})
            cell = constrain_unit_cell(cell, symmetry)
            update_cif_tables(document, cell, sites, symmetry)
        except Exception as exc:
            show_error(self, "CIF Validation Error", str(exc))
            return
        settings: dict[str, Any] = {
            "wavelength_a": float(dataset.metadata.get("wavelength_a", 1.540598)),
            "radiation": dataset.metadata.get("radiation", "Custom"),
            "two_theta_range": tuple(dataset.metadata.get("two_theta_range", (1.0, 90.0))),
            "fwhm_deg": dataset.metadata.get("fwhm_deg", 0.15),
            "profile_step_deg": float(dataset.metadata.get("profile_step_deg", 0.01)),
            "relative_scale": float(dataset.metadata.get("scale", 1.0)),
            "vertical_offset": float(dataset.metadata.get("offset", 0.0)),
            "target_dataset_id": dataset.dataset_id,
        }
        request = _LiveCifRequest(
            self._lattice_generation,
            source_path,
            document,
            settings,
            self.state.generation,
        )
        if self._live_cif_worker is not None:
            self._pending_live_cif = request
            return
        self._start_live_cif_recalculation(request)

    def _start_live_cif_recalculation(self, request: _LiveCifRequest) -> None:
        worker = Worker(
            simulate_cif,
            request.document,
            wavelength_a=request.settings["wavelength_a"],
            two_theta_range=request.settings["two_theta_range"],
            fwhm_deg=None,
        )
        worker.signals.result.connect(
            lambda result: self._finish_live_cif_recalculation(request, result)
        )
        worker.signals.error.connect(
            lambda message, trace: self._live_cif_error(request, message, trace)
        )
        worker.signals.finished.connect(lambda active=worker: self._live_cif_finished(active))
        self._live_cif_worker = worker
        self._workers.add(worker)
        QThreadPool.globalInstance().start(worker)

    def _finish_live_cif_recalculation(
        self,
        request: _LiveCifRequest,
        result: object,
    ) -> None:
        if request.generation != self._lattice_generation or request.workspace_generation != self.state.generation:
            return
        if not isinstance(result, SimulatedPattern):
            self._live_cif_error(
                request,
                "The CIF simulator returned an invalid result.",
                "",
            )
            return
        target = next(
            (
                dataset
                for dataset in self.state.datasets
                if dataset.dataset_id == request.settings["target_dataset_id"]
            ),
            None,
        )
        if target is None:
            return
        fwhm = target.metadata.get("fwhm_deg", 0.15)
        pattern = (
            result
            if fwhm is None
            else broaden_pattern(
                result,
                two_theta_range=tuple(request.settings["two_theta_range"]),
                fwhm_deg=float(fwhm),
                profile_step_deg=float(target.metadata.get("profile_step_deg", 0.01)),
                radiation=str(target.metadata.get("radiation", "Custom")),
            )
        )
        x_values, intensity = display_pattern_data(pattern, target.metadata.get("radiation", "Custom"))
        target.processed_data.clear()
        target.processed_data["x"] = np.asarray(x_values, dtype=float)
        target.processed_data["intensity"] = np.asarray(intensity, dtype=float)
        symmetry = extract_cif_symmetry(request.document)
        metadata: dict[str, Any] = {
            "stick_two_theta_deg": pattern.two_theta_deg.tolist(),
            "stick_intensity": pattern.intensity.tolist(),
            "stick_hkl_labels": pattern.hkl_labels or [],
            "stick_d_spacing_a": (
                pattern.d_spacing_a.tolist() if pattern.d_spacing_a is not None else []
            ),
            "space_group_name": symmetry.name,
            "space_group_number": symmetry.number,
            "crystal_system": symmetry.crystal_system,
            "background_enabled": False,
            "background_preview": False,
        }
        metadata["edited_cif"] = request.document.edited_text
        target.metadata.update(metadata)
        if result.occupancy_model is not None:
            self._occupancy_preview_models[target.dataset_id] = result.occupancy_model
        store_occupancy_preview_model(
            result.occupancy_model, target.processed_data, target.metadata
        )
        self._live_cif_previews.pop(target.dataset_id, None)
        self.state.modified = True
        self.refresh_plot()

    def _live_cif_error(
        self,
        request: _LiveCifRequest,
        message: str,
        traceback_text: str,
    ) -> None:
        if request.generation != self._lattice_generation or request.workspace_generation != self.state.generation:
            LOGGER.debug(
                "Discarded obsolete CIF recalculation error for %s: %s",
                request.source_path,
                message,
            )
        elif self._pending_live_cif is None:
            self._import_error(request.source_path, message, traceback_text)
        else:
            LOGGER.warning(
                "Discarded obsolete CIF recalculation error for %s: %s",
                request.source_path,
                message,
            )

    def _live_cif_finished(self, worker: Worker) -> None:
        self._workers.discard(worker)
        if self._live_cif_worker is worker:
            self._live_cif_worker = None
        pending = self._pending_live_cif
        self._pending_live_cif = None
        if pending is not None and pending.generation == self._lattice_generation and pending.workspace_generation == self.state.generation:
            self._start_live_cif_recalculation(pending)

    def _edit_dataset_if_cif(self, dataset: Dataset | None) -> None:
        if dataset is not None and dataset.metadata.get("is_simulated_phase"):
            self._edit_selected_cif()

    def _preset_changed(self, name: str) -> None:
        value = WAVELENGTH_PRESETS_A.get(name)
        self.wavelength.setEnabled(value is None)
        if value is not None:
            self.wavelength.setValue(value)
        self._apply_selected_settings()

    def _log_scale_changed(self, enabled: bool) -> None:
        """Switch stacking controls between additive and multiplicative semantics."""
        labels = (
            {
                "constant": "Constant multiplier",
                "automatic": "Automatic multiplier",
                "individual": "Individual offsets",
            }
            if enabled
            else {
                "constant": "Constant offset",
                "automatic": "Automatic offset",
                "individual": "Individual offsets",
            }
        )
        for mode, label in labels.items():
            index = self.stacking.findData(mode)
            if index >= 0:
                self.stacking.setItemText(index, label)
        self.stack_value_label.setText("Stack factor" if enabled else "Stack offset")
        if enabled:
            self.stacking_offset.setRange(1.0, 1e12)
        else:
            self.stacking_offset.setRange(-1e12, 1e12)
        self.refresh_plot()

    def _load_selected(self, dataset: Dataset | None) -> None:
        dataset_context(self, dataset)
        if dataset is None:
            return
        kind = dataset.metadata.get("phase_annotation")
        self.axis_card.setEnabled(not kind)
        self.processing_card.setEnabled(not kind)
        self.phase_position_card.setVisible(bool(kind))
        for control, key, default in ((self.phase_lift, "marker_lift_percent", 8.),
                                      (self.phase_tick_offset, "tick_offset_percent", 12.),
                                      (self.phase_tick_length, "tick_length_percent", 5.)):
            control.blockSignals(True)
            control.setValue(float(dataset.metadata.get(key, default)))
            control.blockSignals(False)
        self.phase_position_form.setRowVisible(self.phase_lift, kind == "matched")
        self.phase_position_form.setRowVisible(self.phase_tick_offset, kind == "ticks")
        self.phase_position_form.setRowVisible(self.phase_tick_length, kind == "ticks")
        controls = [
            self.wavelength,
            self.normalization,
            self.scale,
            self.offset,
            self.arpls_smoothness,
            self.arpls_ratio,
            self.arpls_iterations,
        ]
        controls.extend(
            [
                self.difa,
                self.difc,
                self.tzero,
                self.line_style,
                self.line_width,
                self.marker,
                self.marker_size,
                self.legend_text,
            ]
        )
        for control in controls:
            control.blockSignals(True)
        self.wavelength_preset.blockSignals(True)
        self.wavelength_preset.setCurrentText(str(dataset.metadata.get("radiation", "Cu Kα1")))
        self.wavelength_preset.blockSignals(False)
        self.wavelength.setValue(float(dataset.metadata.get("wavelength_a", 1.540598)))
        self.normalization.setCurrentIndex(
            max(
                0,
                self.normalization.findData(
                    dataset.metadata.get("normalization", NormalizationMode.NONE.value)
                ),
            )
        )
        self.scale.setValue(float(dataset.metadata.get("scale", 1.0)))
        self.offset.setValue(float(dataset.metadata.get("offset", 0.0)))
        self.arpls_smoothness.setValue(float(dataset.metadata.get("arpls_smoothness", 1.0e5)))
        self.arpls_ratio.setValue(float(dataset.metadata.get("arpls_convergence_ratio", 0.01)))
        self.arpls_iterations.setValue(int(dataset.metadata.get("arpls_max_iterations", 50)))
        calibration = dataset.metadata.get("tof_calibration", {})
        if not isinstance(calibration, dict):
            calibration = {}
        self.difa.setValue(float(calibration.get("difa", 0.0)))
        self.difc.setValue(float(calibration.get("difc", 0.0)))
        self.tzero.setValue(float(calibration.get("tzero", 0.0)))
        self.line_style.setCurrentText(dataset.plot_style.line_style)
        self.line_width.setValue(dataset.plot_style.line_width)
        self.marker.setCurrentText(dataset.plot_style.marker)
        self.marker_size.setValue(dataset.plot_style.marker_size)
        self.legend_text.setText(dataset.plot_style.legend_text or dataset.display_name)
        self.style_color.setStyleSheet(f"background-color: {dataset.plot_style.color};")
        is_cif = bool(dataset.metadata.get("is_simulated_phase"))
        has_structure = is_cif and not dataset.metadata.get("is_reference_pattern")
        self.background_card.setVisible(not is_cif and not kind)
        self.lattice_card.setVisible(has_structure)
        if has_structure:
            reveal_settings(self.lattice_card)
        self.edit_cif_button.setEnabled(has_structure)
        self.edit_cif_button.setToolTip(
            "Edit the selected CIF structure and recalculate its pattern."
            if has_structure
            else "Reference sticks have no atomic coordinates. Fetch the CIF in Phase Search and add the phase again, or select a simulated CIF dataset."
        )
        if has_structure:
            cached_model = restore_occupancy_preview_model(dataset.processed_data, dataset.metadata)
            if cached_model is not None:
                self._occupancy_preview_models[dataset.dataset_id] = cached_model
            self._lattice_loading = True
            source_path = Path(dataset.source_filename)
            original_text = (
                dataset.source_bytes.decode("utf-8", errors="replace")
                if dataset.source_bytes is not None
                else str(dataset.metadata.get("edited_cif", ""))
            )
            try:
                document = CifDocument(
                    source_path,
                    original_text,
                    str(dataset.metadata.get("edited_cif", original_text)),
                )
                cell, sites, _warnings = extract_cif_tables(document)
                self._selected_symmetry = extract_cif_symmetry(document)
                cell = constrain_unit_cell(cell, self._selected_symmetry)
                self.lattice_symmetry.setText(
                    f"{self._selected_symmetry.name} (No. "
                    f"{self._selected_symmetry.number}) — "
                    f"{self._selected_symmetry.crystal_system.title()}"
                )
                for name, field in self.lattice_fields.items():
                    value = float(getattr(cell, name))
                    field.setValue(value)
                    self._lattice_baseline[name] = value
                    slider = self.lattice_sliders[name]
                    slider.setValue(
                        self._LATTICE_SLIDER_CENTER
                        if name in {"a", "b", "c"}
                        else round(value * self._ANGLE_SLIDER_SCALE)
                    )
                relative_scale = float(dataset.metadata.get("scale", 1.0))
                stored_fwhm = dataset.metadata.get("fwhm_deg", 0.15)
                fwhm = 0.15 if stored_fwhm is None else float(stored_fwhm)
                self.lattice_relative_scale.setValue(relative_scale)
                self.lattice_fwhm.setValue(fwhm)
                self._lattice_baseline["relative_scale"] = (
                    abs(relative_scale) if relative_scale != 0 else 1.0
                )
                self._lattice_baseline["fwhm_deg"] = fwhm
                self._sync_lattice_profile_slider("relative_scale")
                self._sync_lattice_profile_slider("fwhm_deg")
                self._load_occupancy_sites(dataset, sites)
                self._set_lattice_editability()
            except Exception as exc:
                LOGGER.warning("Could not load lattice parameters: %s", exc)
            finally:
                self._lattice_loading = False
        else:
            self._selected_symmetry = None
            self._occupancy_values.clear()
            self._occupancy_labels.clear()
            self.occupancy_site.clear()
        for control in controls:
            control.blockSignals(False)

    def _set_lattice_editability(self) -> None:
        symmetry = self._selected_symmetry
        if symmetry is None:
            return
        editable = {
            "cubic": {"a"},
            "tetragonal": {"a", "c"},
            "orthorhombic": {"a", "b", "c"},
            "hexagonal": {"a", "c"},
            "monoclinic": {"a", "b", "c", "beta"},
        }.get(symmetry.crystal_system, set(self.lattice_fields))
        if symmetry.crystal_system == "trigonal":
            editable = {"a", "c"} if ":H" in symmetry.name.upper() else {"a", "alpha"}
        for name in self.lattice_fields:
            enabled = name in editable
            self.lattice_fields[name].setEnabled(enabled)
            self.lattice_sliders[name].setEnabled(enabled)

    def _load_occupancy_sites(self, dataset: Dataset, sites: list[Any]) -> None:
        """Populate editable site occupancies for the selected simulated phase."""
        preferred = int(dataset.metadata.get("occupancy_site_index", 0))
        self.occupancy_site.blockSignals(True)
        self.occupancy_site.clear()
        self._occupancy_values = {index: float(site.occupancy) for index, site in enumerate(sites)}
        self._occupancy_labels = {index: str(site.label) for index, site in enumerate(sites)}
        for index, site in enumerate(sites):
            self.occupancy_site.addItem(
                f"{site.label} ({site.species})",
                index,
            )
        if sites:
            self.occupancy_site.setCurrentIndex(max(0, min(preferred, len(sites) - 1)))
        self.occupancy_site.blockSignals(False)
        self._occupancy_site_changed(self.occupancy_site.currentIndex())

    def _occupancy_site_changed(self, combo_index: int) -> None:
        if combo_index < 0:
            self.occupancy_value.setEnabled(False)
            self.occupancy_slider.setEnabled(False)
            return
        site_index = int(self.occupancy_site.itemData(combo_index))
        occupancy = self._occupancy_values.get(site_index, 1.0)
        self._lattice_loading = True
        self.occupancy_value.setValue(occupancy)
        self.occupancy_slider.setValue(round(occupancy * 10_000))
        self._lattice_loading = False
        self.occupancy_value.setEnabled(True)
        self.occupancy_slider.setEnabled(True)
        dataset = self.dataset_list.current_dataset()
        if dataset is not None and dataset.metadata.get("is_simulated_phase"):
            dataset.metadata["occupancy_site_index"] = site_index

    def _occupancy_field_changed(self, value: float) -> None:
        if self._lattice_loading or self.occupancy_site.currentIndex() < 0:
            return
        site_index = int(self.occupancy_site.currentData())
        self._occupancy_values[site_index] = value
        self._lattice_loading = True
        self.occupancy_slider.setValue(round(value * 10_000))
        self._lattice_loading = False
        if not self._lattice_slider_active:
            self._schedule_live_cif_recalculation(preview=True, occupancy=True)

    def _occupancy_slider_changed(self, value: int) -> None:
        if self._lattice_loading or self.occupancy_site.currentIndex() < 0:
            return
        self._lattice_slider_active = True
        try:
            self.occupancy_value.setValue(value / 10_000.0)
        finally:
            self._lattice_slider_active = False
        self._schedule_live_cif_recalculation(preview=True, occupancy=True)

    def _constrain_lattice_fields(self, changed_parameter: str) -> None:
        symmetry = self._selected_symmetry
        if symmetry is None:
            return
        cell = constrain_unit_cell(
            UnitCell(**{name: field.value() for name, field in self.lattice_fields.items()}),
            symmetry,
            changed_parameter=changed_parameter,
        )
        self._lattice_loading = True
        for name, field in self.lattice_fields.items():
            field.setValue(float(getattr(cell, name)))
            if name in {"a", "b", "c"}:
                baseline = self._lattice_baseline.get(name, field.value())
                if baseline > 0:
                    ratio = round(field.value() / baseline * self._LATTICE_SLIDER_CENTER)
                    self.lattice_sliders[name].setValue(
                        max(
                            self.lattice_sliders[name].minimum(),
                            min(ratio, self.lattice_sliders[name].maximum()),
                        )
                    )
            else:
                self.lattice_sliders[name].setValue(round(field.value() * self._ANGLE_SLIDER_SCALE))
        self._lattice_loading = False

    def _lattice_field_changed(self, name: str) -> None:
        if self._lattice_loading:
            return
        self._constrain_lattice_fields(name)
        baseline = self._lattice_baseline.get(name, self.lattice_fields[name].value())
        self._lattice_loading = True
        if name in {"a", "b", "c"} and baseline > 0:
            ratio = round(
                self.lattice_fields[name].value() / baseline * self._LATTICE_SLIDER_CENTER
            )
            self.lattice_sliders[name].setValue(
                max(
                    self.lattice_sliders[name].minimum(),
                    min(ratio, self.lattice_sliders[name].maximum()),
                )
            )
        elif name not in {"a", "b", "c"}:
            self.lattice_sliders[name].setValue(
                round(self.lattice_fields[name].value() * self._ANGLE_SLIDER_SCALE)
            )
        self._lattice_loading = False
        if not self._lattice_slider_active:
            self._schedule_live_cif_recalculation(preview=True)

    def _lattice_slider_changed(self, name: str, value: int) -> None:
        if self._lattice_loading:
            return
        if name in {"a", "b", "c"}:
            baseline = self._lattice_baseline.get(name, self.lattice_fields[name].value())
            field_value = baseline * value / self._LATTICE_SLIDER_CENTER
        else:
            field_value = value / self._ANGLE_SLIDER_SCALE
        self._lattice_slider_active = True
        try:
            self.lattice_fields[name].setValue(field_value)
        finally:
            self._lattice_slider_active = False
        self._schedule_live_cif_recalculation(preview=True)

    def _lattice_profile_field(self, name: str) -> QDoubleSpinBox:
        return self.lattice_relative_scale if name == "relative_scale" else self.lattice_fwhm

    def _lattice_profile_slider(self, name: str) -> QSlider:
        return (
            self.lattice_relative_scale_slider
            if name == "relative_scale"
            else self.lattice_fwhm_slider
        )

    def _sync_lattice_profile_slider(self, name: str) -> None:
        field = self._lattice_profile_field(name)
        slider = self._lattice_profile_slider(name)
        baseline = self._lattice_baseline.get(name, field.value())
        ratio = round(field.value() / baseline * self._LATTICE_SLIDER_CENTER) if baseline > 0 else 0
        slider.setValue(max(slider.minimum(), min(ratio, slider.maximum())))

    def _lattice_profile_slider_changed(self, name: str, value: int) -> None:
        if self._lattice_loading:
            return
        field = self._lattice_profile_field(name)
        baseline = self._lattice_baseline.get(name, field.value())
        field.setValue(baseline * value / self._LATTICE_SLIDER_CENTER)

    def _lattice_profile_field_changed(self, name: str) -> None:
        if self._lattice_loading:
            return
        dataset = self.dataset_list.current_dataset()
        if dataset is None or not dataset.metadata.get("is_simulated_phase"):
            return
        field = self._lattice_profile_field(name)
        self._lattice_loading = True
        self._sync_lattice_profile_slider(name)
        if name == "relative_scale":
            self.scale.blockSignals(True)
            self.scale.setValue(field.value())
            self.scale.blockSignals(False)
        self._lattice_loading = False
        if name == "relative_scale":
            dataset.metadata["scale"] = field.value()
            self._refresh_live_plot_data()
        else:
            dataset.metadata["fwhm_deg"] = field.value()
            self._apply_current_cif_profile(dataset)
        self.state.modified = True

    def _apply_current_cif_profile(self, dataset: Dataset) -> None:
        positions = np.asarray(dataset.metadata.get("stick_two_theta_deg", []), dtype=float)
        intensities = np.asarray(dataset.metadata.get("stick_intensity", []), dtype=float)
        if len(positions) == 0 or len(positions) != len(intensities):
            return
        d_spacing_values = np.asarray(dataset.metadata.get("stick_d_spacing_a", []), dtype=float)
        sticks = SimulatedPattern(
            positions,
            intensities,
            hkl_labels=list(dataset.metadata.get("stick_hkl_labels", [])),
            d_spacing_a=(d_spacing_values if len(d_spacing_values) == len(positions) else None),
        )
        stored_range = dataset.metadata.get("two_theta_range", (1.0, 90.0))
        two_theta_range = (float(stored_range[0]), float(stored_range[1]))
        if dataset.metadata.get("fwhm_deg", .15) is None:
            px, py = display_pattern_data(sticks, dataset.metadata.get("radiation", "Custom"))
            dataset.processed_data["x"], dataset.processed_data["intensity"] = px, py
            self._refresh_live_plot_data()
            return
        profile = broaden_pattern(
            sticks,
            two_theta_range=two_theta_range,
            fwhm_deg=float(dataset.metadata.get("fwhm_deg", 0.15)),
            profile_step_deg=float(dataset.metadata.get("profile_step_deg", 0.01)),
            radiation=str(dataset.metadata.get("radiation", "Custom")),
        )
        assert profile.profile_x is not None and profile.profile_y is not None
        dataset.processed_data["x"] = profile.profile_x
        dataset.processed_data["intensity"] = profile.profile_y
        self._refresh_live_plot_data()

    def _occupancy_mapping(self) -> dict[str, float]:
        return {
            label: self._occupancy_values[index]
            for index, label in self._occupancy_labels.items()
            if index in self._occupancy_values
        }

    def _preview_current_cif_occupancies(self, dataset: Dataset) -> bool:
        """Update relative peak intensities immediately from cached structure factors."""
        model = self._occupancy_preview_models.get(dataset.dataset_id)
        if model is None:
            model = restore_occupancy_preview_model(dataset.processed_data, dataset.metadata)
            if model is not None:
                self._occupancy_preview_models[dataset.dataset_id] = model
        if model is None:
            return False
        current = self._live_cif_previews.get(dataset.dataset_id)
        if current is None:
            positions = np.asarray(dataset.metadata.get("stick_two_theta_deg", []), dtype=float)
            intensities = np.asarray(dataset.metadata.get("stick_intensity", []), dtype=float)
            labels = dataset.metadata.get("stick_hkl_labels", [])
            spacings = np.asarray(dataset.metadata.get("stick_d_spacing_a", []), dtype=float)
            if (
                not isinstance(labels, list)
                or len(positions) == 0
                or len(positions) != len(intensities)
            ):
                return False
            current = SimulatedPattern(
                positions,
                intensities,
                hkl_labels=[str(label) for label in labels],
                d_spacing_a=spacings if len(spacings) == len(positions) else None,
                occupancy_model=model,
            )
        else:
            current.occupancy_model = model
        try:
            preview = preview_pattern_for_occupancies(current, self._occupancy_mapping())
            stored_range = dataset.metadata.get("two_theta_range", (1.0, 90.0))
            two_theta_range = (float(stored_range[0]), float(stored_range[1]))
            fwhm = dataset.metadata.get("fwhm_deg", 0.15)
            pattern = (
                preview
                if fwhm is None
                else broaden_pattern(
                    preview,
                    two_theta_range=two_theta_range,
                    fwhm_deg=float(fwhm),
                    profile_step_deg=float(dataset.metadata.get("profile_step_deg", 0.01)),
                    radiation=str(dataset.metadata.get("radiation", "Custom")),
                )
            )
        except Exception as exc:
            LOGGER.debug("Live CIF occupancy preview was skipped: %s", exc)
            return False
        px, py = display_pattern_data(pattern, dataset.metadata.get("radiation", "Custom"))
        dataset.processed_data["x"] = px
        dataset.processed_data["intensity"] = py
        self._live_cif_previews[dataset.dataset_id] = preview
        self._refresh_live_plot_data()
        return True

    def _preview_current_cif_geometry(self, dataset: Dataset) -> None:
        """Move lattice-dependent peaks immediately without running pymatgen."""
        current = self._live_cif_previews.get(dataset.dataset_id)
        positions = np.asarray(
            current.two_theta_deg
            if current is not None
            else dataset.metadata.get("stick_two_theta_deg", []),
            dtype=float,
        )
        intensities = np.asarray(
            current.intensity
            if current is not None
            else dataset.metadata.get("stick_intensity", []),
            dtype=float,
        )
        stored_labels = (
            current.hkl_labels
            if current is not None
            else dataset.metadata.get("stick_hkl_labels", [])
        )
        labels = [str(label) for label in stored_labels] if isinstance(stored_labels, list) else []
        if (
            len(positions) == 0
            or len(positions) != len(intensities)
            or len(positions) != len(labels)
        ):
            return
        stored_range = dataset.metadata.get("two_theta_range", (1.0, 90.0))
        two_theta_range = (float(stored_range[0]), float(stored_range[1]))
        cell = UnitCell(**{name: field.value() for name, field in self.lattice_fields.items()})
        try:
            preview = preview_pattern_for_cell(
                SimulatedPattern(
                    positions,
                    intensities,
                    hkl_labels=labels,
                    d_spacing_a=np.asarray(
                        dataset.metadata.get("stick_d_spacing_a", []), dtype=float
                    ),
                    occupancy_model=self._occupancy_preview_models.get(dataset.dataset_id),
                ),
                cell=cell,
                wavelength_a=float(dataset.metadata.get("wavelength_a", 1.540598)),
                two_theta_range=two_theta_range,
            )
            fwhm = dataset.metadata.get("fwhm_deg", 0.15)
            pattern = (
                preview
                if fwhm is None
                else broaden_pattern(
                    preview,
                    two_theta_range=two_theta_range,
                    fwhm_deg=float(fwhm),
                    profile_step_deg=float(dataset.metadata.get("profile_step_deg", 0.01)),
                    radiation=str(dataset.metadata.get("radiation", "Custom")),
                )
            )
        except Exception as exc:
            # The exact background calculation remains authoritative. A preview
            # failure must not interrupt slider interaction or show a dialog.
            LOGGER.debug("Live CIF geometry preview was skipped: %s", exc)
            return
        x_values, y_values = display_pattern_data(pattern, dataset.metadata.get("radiation", "Custom"))
        dataset.processed_data["x"] = np.asarray(x_values, dtype=float)
        dataset.processed_data["intensity"] = np.asarray(y_values, dtype=float)
        self._live_cif_previews[dataset.dataset_id] = preview
        self._refresh_live_plot_data()

    def _schedule_live_cif_recalculation(self, *, preview: bool, occupancy: bool = False) -> None:
        """Invalidate old work, render a fast preview, and debounce exact work."""
        dataset = self.dataset_list.current_dataset()
        if dataset is None or not dataset.metadata.get("is_simulated_phase"):
            return
        self._lattice_generation += 1
        self.state.modified = True
        if preview:
            occupancy_rendered = (
                self._preview_current_cif_occupancies(dataset) if occupancy else False
            )
            if not occupancy_rendered:
                self._preview_current_cif_geometry(dataset)
        # Restarting a single-shot timer makes continuous dragging latest-only;
        # the expensive simulator runs once after interaction settles.
        self._lattice_update_timer.start(self._LIVE_CIF_SETTLE_MS)

    def _flush_lattice_recalculation(self) -> None:
        """Start the exact calculation immediately when a slider is released."""
        if not self._lattice_update_timer.isActive():
            return
        self._lattice_update_timer.stop()
        self._apply_lattice_parameters()

    def _schedule_cif_recalculation(self) -> None:
        self._schedule_live_cif_recalculation(preview=True)

    def _refresh_live_plot_data(self) -> None:
        """Keep existing graphics items and replace only arrays during live editing."""
        spec = self.build_plot_spec()
        self.current_spec = spec
        if not update_rendered_plot_data(self.plot_widget, spec):
            self.refresh_plot()

    def _apply_selected_settings(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return
        previous_wavelength = float(dataset.metadata.get("wavelength_a", 1.540598))
        previous_radiation = dataset.metadata.get("radiation", "Custom")
        dataset.metadata.update(
            {
                "wavelength_a": self.wavelength.value(),
                "radiation": self.wavelength_preset.currentText(),
                "normalization": str(self.normalization.currentData()),
                "scale": self.scale.value(),
                "offset": self.offset.value(),
                "tof_calibration": {
                    "difa": self.difa.value(),
                    "difc": self.difc.value(),
                    "tzero": self.tzero.value(),
                },
            }
        )
        if dataset.metadata.get("is_simulated_phase") and not self._lattice_loading:
            self._lattice_loading = True
            self.lattice_relative_scale.setValue(self.scale.value())
            self._sync_lattice_profile_slider("relative_scale")
            self._lattice_loading = False
        self.state.modified = True
        self.refresh_plot()
        if dataset.metadata.get("is_simulated_phase") and not np.isclose(
            previous_wavelength, self.wavelength.value()
        ):
            self._schedule_cif_recalculation()
        elif dataset.metadata.get("is_simulated_phase") and previous_radiation != dataset.metadata.get("radiation"):
            self._apply_current_cif_profile(dataset)

    def _apply_normalization_to_all(self) -> None:
        mode = str(self.normalization.currentData())
        datasets = [
            dataset for dataset in self.state.datasets if dataset.module_type is ModuleType.XRD
        ]
        for dataset in datasets:
            dataset.metadata["normalization"] = mode
        if datasets:
            self.state.modified = True
            self.refresh_plot()

    def _apply_wavelength_to_all(self) -> None:
        wavelength = self.wavelength.value()
        self._lattice_update_timer.stop()
        datasets = [
            dataset for dataset in self.state.datasets if dataset.module_type is ModuleType.XRD
        ]
        for dataset in datasets:
            dataset.metadata["wavelength_a"] = wavelength
            dataset.metadata["radiation"] = self.wavelength_preset.currentText()
            if dataset.metadata.get("is_simulated_phase"):
                self._recalculate_cif_dataset(dataset)
        if datasets:
            self.state.modified = True
            self.refresh_plot()

    def _recalculate_cif_dataset(self, dataset: Dataset) -> None:
        source_path = Path(dataset.source_filename)
        original_text = (
            dataset.source_bytes.decode("utf-8", errors="replace")
            if dataset.source_bytes is not None
            else str(dataset.metadata.get("edited_cif", ""))
        )
        document = CifDocument(
            source_path,
            original_text,
            str(dataset.metadata.get("edited_cif", original_text)),
        )
        settings: dict[str, Any] = {
            "wavelength_a": float(dataset.metadata.get("wavelength_a", 1.540598)),
            "radiation": dataset.metadata.get("radiation", "Custom"),
            "two_theta_range": tuple(dataset.metadata.get("two_theta_range", (1.0, 90.0))),
            "fwhm_deg": dataset.metadata.get("fwhm_deg", 0.15),
            "profile_step_deg": float(dataset.metadata.get("profile_step_deg", 0.01)),
            "relative_scale": float(dataset.metadata.get("scale", 1.0)),
            "vertical_offset": float(dataset.metadata.get("offset", 0.0)),
            "target_dataset_id": dataset.dataset_id,
        }
        self._queue_cif(source_path, document, settings)

    def _choose_style_color(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return
        color = QColorDialog.getColor(QColor(dataset.plot_style.color), self)
        if color.isValid():
            dataset.plot_style.color = color.name()
            if dataset.metadata.get("phase_annotation"):
                dataset.plot_style.marker_fill_color = color.name()
                dataset.plot_style.marker_edge_color = color.name()
            self.style_color.setStyleSheet(f"background-color: {color.name()};")
            self.state.modified = True
            self.dataset_list.refresh_style_indicators()
            self.refresh_plot()

    def _apply_style(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return
        dataset.plot_style.line_style = self.line_style.currentText()
        dataset.plot_style.line_width = self.line_width.value()
        dataset.plot_style.marker = self.marker.currentText()
        dataset.plot_style.marker_size = self.marker_size.value()
        dataset.plot_style.legend_text = self.legend_text.text().strip()
        self.state.modified = True
        self.dataset_list.refresh_style_indicators()
        self.refresh_plot()

    def _apply_phase_position(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None or not dataset.metadata.get("phase_annotation"):
            return
        dataset.metadata.update(marker_lift_percent=self.phase_lift.value(),
                                tick_offset_percent=self.phase_tick_offset.value(),
                                tick_length_percent=self.phase_tick_length.value())
        self.state.modified = True
        self.refresh_plot()

    def _reset_style(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return
        dataset.plot_style = default_plot_style(dataset.plot_order, dataset.display_name)
        self._load_selected(dataset)
        self.state.modified = True
        self.dataset_list.refresh_style_indicators()
        self.refresh_plot()

    def _estimate_background(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return
        try:
            values = dataset.array("intensity")
            options = {
                "smoothness": self.arpls_smoothness.value(),
                "convergence_ratio": self.arpls_ratio.value(),
                "max_iterations": self.arpls_iterations.value(),
            }
            cancelled = Event()
            run_task(
                self,
                "Estimate arPLS background",
                lambda: arpls_background(values, **options, cancel_event=cancelled),
                lambda background: self._finish_background(dataset, background, options),
                state=self.state,
                dataset=dataset,
                cancelled=cancelled,
            )
        except Exception as exc:
            show_error(self, "Background Error", str(exc))

    def _finish_background(
        self, dataset: Dataset, background: np.ndarray, options: dict[str, Any]
    ) -> None:
        dataset.processed_data["background"] = background
        dataset.metadata["background_method"] = "arpls"
        dataset.metadata["arpls_smoothness"] = options["smoothness"]
        dataset.metadata["arpls_convergence_ratio"] = options["convergence_ratio"]
        dataset.metadata["arpls_max_iterations"] = options["max_iterations"]
        dataset.metadata["background_enabled"] = False
        dataset.metadata["background_preview"] = True
        self.state.modified = True
        self.refresh_plot()

    def _set_background_enabled(self, enabled: bool) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return
        if "background" not in dataset.processed_data:
            show_warning(self, "Background", "Estimate a background before applying it.")
            return
        dataset.metadata["background_enabled"] = enabled
        dataset.metadata["background_preview"] = not enabled
        from materials_data_studio.core.processing import record_operation
        if enabled:
            record_operation(dataset, "background", {"smoothness": dataset.metadata.get("arpls_smoothness", 1e5), "convergence_ratio": dataset.metadata.get("arpls_convergence_ratio", .01), "max_iterations": dataset.metadata.get("arpls_max_iterations", 50)})
        self.state.modified = True
        self.refresh_plot()

    def _clear_background(self) -> None:
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            return
        dataset.processed_data.pop("background", None)
        from materials_data_studio.core.processing import record_operation
        record_operation(dataset, "clear_background", {})
        dataset.metadata["background_enabled"] = False
        dataset.metadata["background_preview"] = False
        self.state.modified = True
        self.refresh_plot()

    def build_plot_spec(self) -> PlotSpec:
        """Build one PlotSpec using each dataset's wavelength and processing state."""
        display_axis = AxisType(str(self.display_axis.currentData()))
        curves: list[CurveSpec] = []
        incompatible: list[str] = []
        stacking_mode = str(self.stacking.currentData())
        automatic_offset = 0.0
        automatic_log_top: float | None = None
        log_scale = self.log_y_axis.isChecked()
        stack_value = self.stacking_offset.value()
        displayed = {}
        datasets = self.state.visible_datasets(ModuleType.XRD)
        parents = [dataset for dataset in datasets if not dataset.metadata.get("phase_annotation")]
        for dataset_index, dataset in enumerate(parents):
            source_axis = AxisType(
                str(dataset.metadata.get("source_axis", AxisType.TWO_THETA.value))
            )
            calibration = None
            calibration_data = dataset.metadata.get("tof_calibration")
            if isinstance(calibration_data, dict):
                calibration = TofCalibration(**calibration_data)
            try:
                source_x = np.asarray(dataset.array("x"), dtype=float)
                if dataset.metadata.get("is_simulated_phase") and source_axis is AxisType.TWO_THETA:
                    theta = np.deg2rad(source_x / 2.0)
                    source_x = (
                        source_x
                        + float(dataset.metadata.get("simulation_zero_shift_deg", 0.0))
                        + float(
                            dataset.metadata.get("simulation_displacement_coefficient_deg", 0.0)
                        )
                        * np.cos(theta)
                    )
                x_values = convert_x_axis(
                    source_x,
                    source_axis,
                    display_axis,
                    wavelength_a=float(dataset.metadata.get("wavelength_a", 1.540598)),
                    tof_calibration=calibration,
                )
                normalization = NormalizationMode(
                    str(dataset.metadata.get("normalization", NormalizationMode.NONE.value))
                )
                intensity = np.asarray(dataset.array("intensity"), dtype=float)
                scale = float(dataset.metadata.get("scale", 1.0))
                offset = float(dataset.metadata.get("offset", 0.0))
                y_values = process_intensity(
                    x_values,
                    intensity,
                    normalization=normalization,
                    scale=scale,
                    offset=offset,
                    background=dataset.processed_data.get("background"),
                    subtract_background=bool(dataset.metadata.get("background_enabled", False)),
                )
                vertical_offset = 0.0
                vertical_multiplier = 1.0
                if log_scale and stacking_mode == "constant":
                    exponent = float(np.clip(dataset_index * np.log(stack_value), -700.0, 700.0))
                    vertical_multiplier = float(np.exp(exponent))
                elif log_scale and stacking_mode == "automatic":
                    positive = y_values[np.isfinite(y_values) & (y_values > 0)]
                    if positive.size and automatic_log_top is not None:
                        vertical_multiplier = (
                            automatic_log_top * stack_value / float(np.min(positive))
                        )
                elif stacking_mode == "constant":
                    vertical_offset = dataset_index * self.stacking_offset.value()
                elif stacking_mode == "automatic":
                    vertical_offset = automatic_offset
                y_values = y_values * vertical_multiplier + vertical_offset
                if log_scale and stacking_mode == "automatic":
                    displayed_positive = y_values[np.isfinite(y_values) & (y_values > 0)]
                    if displayed_positive.size:
                        automatic_log_top = float(np.max(displayed_positive))
                elif stacking_mode == "automatic":
                    finite = y_values[np.isfinite(y_values)]
                    if finite.size:
                        automatic_offset += max(
                            float(np.ptp(finite)) * 1.1,
                            self.stacking_offset.value(),
                        )
            except Exception:
                incompatible.append(dataset.display_name)
                continue
            curves.append(
                CurveSpec(
                    x=x_values,
                    y=y_values,
                    label=dataset.display_name,
                    style=dataset.plot_style,
                )
            )
            displayed[dataset.dataset_id] = (dataset, x_values, y_values)
            background = dataset.processed_data.get("background")
            if (
                background is not None
                and dataset.metadata.get("background_preview", False)
                and not dataset.metadata.get("background_enabled", False)
            ):
                background_values = (
                    np.asarray(background, dtype=float)
                    * normalization_factor(x_values, intensity, normalization)
                    * scale
                    + offset
                ) * vertical_multiplier + vertical_offset
                curves.append(
                    CurveSpec(
                        x=x_values,
                        y=background_values,
                        label=f"{dataset.display_name} background",
                        style=PlotStyle(
                            color="#F97316",
                            line_style="--",
                            line_width=1.5,
                            marker="none",
                            legend_text=f"{dataset.display_name} background",
                        ),
                    )
                )
        from materials_data_studio.modules.xrd.phase_overlays import (
            annotation_data,
            visible_marker_masks,
        )
        marker_masks = visible_marker_masks(datasets)
        for dataset in datasets:
            if not dataset.metadata.get("phase_annotation"):
                continue
            parent_info = displayed.get(dataset.metadata.get("phase_parent"))
            if parent_info is None:
                continue
            parent, px, py = parent_info
            try:
                positions = convert_x_axis(dataset.array("x"), AxisType.TWO_THETA, display_axis,
                                           wavelength_a=float(parent.metadata.get("wavelength_a", 1.540598)),
                                           tof_calibration=TofCalibration(**parent.metadata["tof_calibration"]) if parent.metadata.get("tof_calibration") else None)
                if dataset.metadata["phase_annotation"] == "matched":
                    positions = positions[marker_masks[dataset.dataset_id]]
                ax, ay = annotation_data(dataset, px, py, positions, log=log_scale)
                curves.append(CurveSpec(ax, ay, dataset.display_name, dataset.plot_style))
            except (ValueError, InvalidAxisConversionError):
                incompatible.append(dataset.display_name)
        if incompatible:
            LOGGER.warning(
                "XRD datasets incompatible with %s: %s",
                display_axis,
                ", ".join(incompatible),
            )
            self.compatibility_warning.setText(
                "Not plotted because the selected axis is unavailable: " + ", ".join(incompatible)
            )
        else:
            self.compatibility_warning.clear()
        return PlotSpec(
            x_label=AXIS_LABELS[display_axis],
            y_label="Intensity",
            curves=curves,
            legend_enabled=self.legend_enabled.isChecked(),
            figure_width_cm=self.figure_width.value(),
            figure_height_cm=self.figure_height.value(),
            dpi=self.figure_dpi.value(),
            transparent=self.transparent.isChecked(),
            y_log_scale=self.log_y_axis.isChecked(),
        )

    def refresh_plot(self) -> None:
        needed = self.display_axis.currentData() == AxisType.TOF.value or any(d.metadata.get("source_axis") == AxisType.TOF.value for d in self.dataset_list.datasets())
        for control in (self.difa, self.difc, self.tzero):
            self.axis_form.setRowVisible(control, needed)
        """Update the interactive plot from current state."""
        self.current_spec = self.build_plot_spec()
        render_plot(self.plot_widget, self.current_spec)
        plot_item = self.plot_widget.getPlotItem()
        assert plot_item is not None
        plot_item.addItem(self._peak_label)
        self._peak_label.hide()

    def igor_reflection_traces(self):  # type: ignore[no-untyped-def]
        """Return visible CIF reflection positions as editable native XY traces."""
        from materials_data_studio.plotting.igor_export import GraphExportTrace, split_axis_unit

        display_axis = AxisType(str(self.display_axis.currentData()))
        curves = self.current_spec.curves
        finite_y = (
            np.concatenate(
                [np.asarray(curve.y, dtype=float)[np.isfinite(curve.y)] for curve in curves]
            )
            if curves
            else np.asarray([], dtype=float)
        )
        if finite_y.size:
            if self.current_spec.y_log_scale:
                positive = finite_y[finite_y > 0]
                baseline = float(np.min(positive)) if positive.size else 1.0
            else:
                baseline = float(np.min(finite_y))
        else:
            baseline = 1.0 if self.current_spec.y_log_scale else 0.0
        _x_label, x_unit = split_axis_unit(self.current_spec.x_label)
        _y_label, y_unit = split_axis_unit(self.current_spec.y_label)
        traces = []
        for dataset in self.state.visible_datasets(ModuleType.XRD):
            if not dataset.metadata.get("is_simulated_phase"):
                continue
            preview = self._live_cif_previews.get(dataset.dataset_id)
            positions = np.asarray(
                preview.two_theta_deg
                if preview is not None
                else dataset.metadata.get("stick_two_theta_deg", []),
                dtype=float,
            )
            if positions.size == 0:
                continue
            try:
                converted = convert_x_axis(
                    positions,
                    AxisType.TWO_THETA,
                    display_axis,
                    wavelength_a=float(dataset.metadata.get("wavelength_a", 1.540598)),
                )
            except Exception:
                continue
            traces.append(
                GraphExportTrace(
                    converted,
                    np.full(converted.shape, baseline, dtype=float),
                    f"{dataset.display_name} Bragg positions",
                    PlotStyle(
                        color=dataset.plot_style.color,
                        line_style="none",
                        marker="|",
                        marker_size=max(5.0, dataset.plot_style.marker_size),
                    ),
                    role="reflection_positions",
                    x_unit=x_unit,
                    y_unit=y_unit,
                )
            )
        return traces

    def _show_cif_peak_hover(self, position: QPointF) -> None:
        """Show hkl and d when the pointer is close to a selected simulated peak."""
        dataset = self.dataset_list.current_dataset()
        plot_item = self.plot_widget.getPlotItem()
        if (
            dataset is None
            or not dataset.metadata.get("is_simulated_phase")
            or plot_item is None
            or not plot_item.sceneBoundingRect().contains(position)
        ):
            self._peak_label.hide()
            return
        preview = self._live_cif_previews.get(dataset.dataset_id)
        positions = np.asarray(
            preview.two_theta_deg
            if preview is not None
            else dataset.metadata.get("stick_two_theta_deg", []),
            dtype=float,
        )
        theta = np.deg2rad(positions / 2.0)
        positions = (
            positions
            + float(dataset.metadata.get("simulation_zero_shift_deg", 0.0))
            + float(dataset.metadata.get("simulation_displacement_coefficient_deg", 0.0))
            * np.cos(theta)
        )
        d_spacing = np.asarray(
            preview.d_spacing_a
            if preview is not None and preview.d_spacing_a is not None
            else dataset.metadata.get("stick_d_spacing_a", []),
            dtype=float,
        )
        hkl_labels = (
            preview.hkl_labels
            if preview is not None
            else dataset.metadata.get("stick_hkl_labels", [])
        )
        if (
            not isinstance(hkl_labels, list)
            or len(positions) == 0
            or len(d_spacing) != len(positions)
            or len(hkl_labels) != len(positions)
        ):
            self._peak_label.hide()
            return
        try:
            display_positions = convert_x_axis(
                positions,
                AxisType.TWO_THETA,
                AxisType(str(self.display_axis.currentData())),
                wavelength_a=float(dataset.metadata.get("wavelength_a", 1.540598)),
            )
        except Exception:
            self._peak_label.hide()
            return
        view_box = plot_item.getViewBox()
        cursor = view_box.mapSceneToView(position)
        peak_index = int(np.argmin(np.abs(display_positions - cursor.x())))
        peak_x = float(display_positions[peak_index])
        curve = next(
            (
                candidate
                for candidate in self.current_spec.curves
                if candidate.label == dataset.display_name
            ),
            None,
        )
        if curve is None or len(curve.x) == 0:
            self._peak_label.hide()
            return
        plotted_index = int(np.argmin(np.abs(curve.x - peak_x)))
        peak_y = float(curve.y[plotted_index])
        peak_y_view = (
            float(np.log10(peak_y)) if self.log_y_axis.isChecked() and peak_y > 0 else peak_y
        )
        peak_scene = view_box.mapViewToScene(QPointF(peak_x, peak_y_view))
        distance = float(
            np.hypot(
                peak_scene.x() - position.x(),
                peak_scene.y() - position.y(),
            )
        )
        if not np.isfinite(distance) or distance > 16:
            self._peak_label.hide()
            return
        self._peak_label.setColor(
            "#E5E7EB" if str(self.plot_widget.property("materialsTheme")) == "dark" else "#172033"
        )
        self._peak_label.setText(f"{hkl_labels[peak_index]}\nd = {d_spacing[peak_index]:.7g} Å")
        self._peak_label.setPos(peak_x, peak_y_view)
        self._peak_label.show()

    def _sync_legend_context_action(self, checked: bool) -> None:
        self.legend_context_action.blockSignals(True)
        self.legend_context_action.setChecked(checked)
        self.legend_context_action.blockSignals(False)

    def _update_peak_fitting_availability(self) -> None:
        phases = [
            dataset
            for dataset in self.state.datasets
            if dataset.module_type is ModuleType.XRD
            and dataset.metadata.get("is_simulated_phase")
            and dataset.metadata.get("stick_hkl_labels")
        ]
        experimental = [
            dataset
            for dataset in self.state.datasets
            if dataset.module_type is ModuleType.XRD
            and not dataset.metadata.get("is_simulated_phase")
            and not dataset.metadata.get("phase_annotation")
            and str(dataset.metadata.get("source_axis", "two_theta")) == AxisType.TWO_THETA.value
        ]
        enabled = bool(phases and experimental)
        self.open_peak_fitting_button.setEnabled(enabled)
        if not phases:
            explanation = "Load a simulated CIF phase to enable peak fitting."
        elif not experimental:
            explanation = "Load an angle-dispersive experimental 2θ dataset to enable peak fitting."
        else:
            explanation = (
                f"{len(experimental)} compatible dataset(s), {len(phases)} CIF phase(s) available."
            )
        self.peak_fitting_availability.setText(explanation)
        self.open_peak_fitting_button.setToolTip(explanation)
        if self._peak_fitting_window is not None:
            self._peak_fitting_window.refresh_sources()

    def _open_peak_fitting(self) -> None:
        if self._peak_fitting_window is not None:
            self._peak_fitting_window.show()
            self._peak_fitting_window.raise_()
            self._peak_fitting_window.activateWindow()
            return
        from materials_data_studio.modules.xrd.peak_fitting.fitting_window import (
            XrdPeakFittingWindow,
        )

        theme = str(self.plot_widget.property("materialsTheme") or "light")
        window = XrdPeakFittingWindow(
            self.state,
            apply_callback=self._apply_peak_fit_result,
            saved_state=self._peak_fitting_state,
            theme=theme,
            parent=self.window(),
        )
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._peak_fitting_window = window
        window.show()

    def peak_fitting_state(self) -> dict[str, Any]:
        """Return optional fitting-window state for session persistence."""
        if self._peak_fitting_window is not None:
            self._peak_fitting_state = self._peak_fitting_window.serialize_state()
        return dict(self._peak_fitting_state)

    def _open_phase_search(self) -> None:
        from materials_data_studio.modules.xrd.phase_window import PhaseSearchWindow
        current = self.dataset_list.current_dataset()
        preferred = current.dataset_id if current is not None and not current.metadata.get("is_simulated_phase") else None
        if current is not None and current.metadata.get("phase_parent"):
            preferred = current.metadata["phase_parent"]
        if self._phase_search_window is None:
            saved = self._phase_search_state if not preferred or self._phase_search_state.get("dataset_id") == preferred else {}
            self._phase_search_window = PhaseSearchWindow(self.state, self, saved_state=saved, refresh_callback=self.refresh, selected_dataset_id=preferred)
        previous = self._phase_search_window.dataset_combo.currentData()
        self._phase_search_window._preferred_dataset = preferred
        self._phase_search_window.refresh_sources()
        if previous != self._phase_search_window.dataset_combo.currentData():
            self._phase_search_window.dataset_changed()
        self._phase_search_window.show()
        self._phase_search_window.begin_workflow()
        self._phase_search_window.raise_()

    def phase_search_state(self) -> dict[str, Any]:
        if self._phase_search_window is not None:
            self._phase_search_state = self._phase_search_window.serialize_state()
        return dict(self._phase_search_state)

    def restore_phase_search_state(self, state: dict[str, Any]) -> None:
        if self._phase_search_window is not None:
            self._phase_search_window.close()
            self._phase_search_window.deleteLater()
            self._phase_search_window = None
        self._phase_search_state = dict(state)

    def restore_peak_fitting_state(self, state: dict[str, Any]) -> None:
        """Store backward-compatible fitting state until its window is opened."""
        if self._peak_fitting_window is not None:
            self._peak_fitting_window.close()
            self._peak_fitting_window = None
        self._peak_fitting_state = dict(state)
        self._update_peak_fitting_availability()

    def _apply_peak_fit_result(
        self,
        phase_id: str,
        result: PeakFitResult,
        selected: set[str],
    ) -> None:
        phase = next(
            (dataset for dataset in self.state.datasets if dataset.dataset_id == phase_id),
            None,
        )
        if phase is None or not phase.metadata.get("is_simulated_phase"):
            show_error(self, "Apply Peak Fit", "The fitted CIF phase is no longer available.")
            return
        recalculate = False
        if "lattice" in selected:
            document = CifDocument(
                Path(phase.source_filename),
                (
                    phase.source_bytes.decode("utf-8", errors="replace")
                    if phase.source_bytes is not None
                    else str(phase.metadata.get("edited_cif", ""))
                ),
                str(phase.metadata.get("edited_cif", "")),
            )
            cell, sites, _warnings = extract_cif_tables(document)
            symmetry = extract_cif_symmetry(document)
            cell_values = {
                name: (
                    result.parameters[name].value
                    if name in result.parameters
                    else float(getattr(cell, name))
                )
                for name in ("a", "b", "c", "alpha", "beta", "gamma")
            }
            constrained = constrain_unit_cell(
                UnitCell(**cell_values), symmetry, changed_parameter="a"
            )
            update_cif_tables(document, constrained, sites, symmetry)
            phase.metadata["edited_cif"] = document.edited_text
            recalculate = True
        if "wavelength" in selected:
            phase.metadata["wavelength_a"] = result.parameters["wavelength"].value
            recalculate = True
        if "shifts" in selected:
            phase.metadata["simulation_zero_shift_deg"] = result.parameters["zero_shift"].value
            phase.metadata["simulation_displacement_coefficient_deg"] = result.parameters[
                "displacement"
            ].value
        if "profile" in selected:
            phase.metadata["fwhm_deg"] = result.parameters["fwhm"].value
            recalculate = True
        if "scale" in selected and result.groups:
            fwhm = result.parameters["fwhm"].value
            eta = result.parameters["eta"].value
            peak_maximum = (1.0 - eta) * 2.0 * np.sqrt(np.log(2.0)) / (
                fwhm * np.sqrt(np.pi)
            ) + eta * 2.0 / (np.pi * fwhm)
            phase.metadata["scale"] = (
                max(group.fitted_area for group in result.groups) * peak_maximum / 100.0
            )
        self.state.modified = True
        if recalculate:
            self._recalculate_cif_dataset(phase)
        else:
            self.refresh_plot()

    def apply_theme(self, theme: str) -> None:
        """Apply the selected theme to the interactive plot scene."""
        apply_plot_theme(self.plot_widget, theme)
        if self._peak_fitting_window is not None:
            self._peak_fitting_window.apply_theme(theme)
        if self._phase_search_window is not None:
            self._phase_search_window.apply_theme(theme)

    def refresh(self) -> None:
        """Refresh dataset controls and graph after external state changes."""
        self.dataset_list.refresh()
        self.refresh_plot()
        self._update_peak_fitting_availability()

    def export_data(self) -> None:
        """Export the selected dataset's displayed data and background."""
        dataset = self.dataset_list.current_dataset()
        if dataset is None:
            show_warning(self, "Export Data", "Select an XRD dataset first.")
            return
        path_text, _ = QFileDialog.getSaveFileName(
            self,
            "Export XRD Data",
            f"{dataset.display_name}.tsv",
            "Tab-separated (*.dat *.tsv);;CSV (*.csv);;Igor Text Wave (*.itx)",
        )
        if not path_text:
            return
        try:
            spec = self.build_plot_spec()
            curve = next(curve for curve in spec.curves if curve.label == dataset.display_name)
            display_axis = AxisType(str(self.display_axis.currentData()))
            columns = {
                AXIS_EXPORT_NAMES[display_axis]: curve.x,
                "Intensity_raw": dataset.array("intensity")
                if dataset.metadata.get("is_simulated_phase")
                else dataset.raw_data["intensity"],
                "Intensity_processed": curve.y,
            }
            if "background" in dataset.processed_data:
                columns["Background"] = dataset.processed_data["background"]
            export_numeric(Path(path_text), columns, metadata=[dataset.display_name])
        except Exception as exc:
            show_error(self, "Export Error", str(exc))

    def export_figure(self) -> None:
        """Export the shared plot specification through Matplotlib."""
        path_text, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export XRD Figure",
            "xrd-comparison.png",
            FIGURE_FILE_FILTERS,
        )
        if path_text:
            try:
                spec = self.current_spec
                if self.export_current_view.isChecked():
                    x_range, y_range = self.plot_widget.viewRange()
                    export_y_range = (
                        (10.0 ** float(y_range[0]), 10.0 ** float(y_range[1]))
                        if spec.y_log_scale
                        else (float(y_range[0]), float(y_range[1]))
                    )
                    spec = dataclasses.replace(
                        spec,
                        x_range=(float(x_range[0]), float(x_range[1])),
                        y_range=export_y_range,
                    )
                export_figure(resolve_figure_export_path(path_text, selected_filter), spec)
            except Exception as exc:
                show_error(self, "Figure Export Error", str(exc))
