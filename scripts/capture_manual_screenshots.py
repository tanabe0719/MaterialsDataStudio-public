"""Reproduce the Japanese user manual screenshots with the real Qt widgets."""

# ruff: noqa: E402

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
TEMP_ROOT = Path(tempfile.mkdtemp(prefix="materials-data-studio-manual-"))
os.environ.setdefault("QT_QPA_PLATFORM", "windows" if sys.platform == "win32" else "offscreen")
os.environ["QT_SCALE_FACTOR"] = "1"
os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "0"
os.environ["MPLCONFIGDIR"] = str(TEMP_ROOT / "matplotlib")

from pyqtgraph import ViewBox
from PySide6.QtCore import (
    QCoreApplication,
    QEventLoop,
    QPoint,
    QSettings,
    Qt,
    QTimer,
    QUrl,
)
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QMenu,
    QScrollArea,
    QTabWidget,
    QWidget,
)

from materials_data_studio.config import APP_NAME, ORGANIZATION_NAME
from materials_data_studio.core.models import (
    AxisType,
    Dataset,
    ModuleType,
    NormalizationMode,
    source_sha256,
)
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.modules.mpms.parser import parse_mpms_dc
from materials_data_studio.modules.ppms.heat_capacity import fit_debye_einstein
from materials_data_studio.modules.xrd.cif_backend import load_cif
from materials_data_studio.modules.xrd.parser import parse_xrd_rasx, parse_xrd_text
from materials_data_studio.modules.xrd.peak_fitting.optimizer import fit_pattern
from materials_data_studio.modules.xrd.simulation import simulate_cif
from materials_data_studio.plotting.interactive_export import InteractiveExportDialog
from materials_data_studio.plotting.styles import default_plot_style
from materials_data_studio.tools.windows import (
    MolarMassWindow,
    RasxConverterWindow,
    ShannonRadiiWindow,
)
from materials_data_studio.ui.main_window import MainWindow

DEMO = ROOT / "docs" / "demo_data"
OUTPUT = ROOT / "docs" / "screenshots"
WINDOW_SIZE = (1440, 900)
DIALOG_SCREENSHOTS = {
    "19_preferences_dark.png",
    "20_session_save_dialog.png",
    "20b_session_open_dialog.png",
    "21_data_export_dialog.png",
    "22_user_manual_dialog.png",
    "24_molecular_weight.png",
    "25_shannon_radii.png",
    "26_rasx_converter.png",
    "27_file_menu.png",
    "28_help_menu.png",
    "29_dark_toolbar.png",
    "30_interactive_export_data.png",
    "31_interactive_export_raster.png",
    "32_igor_preferences.png",
    "33_interactive_export_h5xp.png",
}
FULL_SCREENSHOTS = {
    "01_main_window.png",
    "02_xrd_multidataset.png",
    "03_xrd_background.png",
    "04_cif_editor.png",
    "05_xrd_cif_simulation.png",
    "06_peak_fitting.png",
    "06b_peak_fitting_progress.png",
    "07_mpms_mt.png",
    "08_mpms_curie_weiss.png",
    "09_mpms_mh.png",
    "10_heat_capacity_cp.png",
    "11_heat_capacity_low_t.png",
    "12_heat_capacity_debye_einstein.png",
    "13_resistivity_rho_t.png",
    "14_resistivity_arrhenius.png",
    "15_resistivity_vrh.png",
    "16_resistivity_rho_h.png",
    "17_ppms_custom_plot.png",
    "18_ppms_custom_fit.png",
    "23_tools_launcher.png",
} | DIALOG_SCREENSHOTS
XRD_OVERVIEW_SCREENSHOTS = {
    "01_main_window.png",
    "02_xrd_multidataset.png",
    "03_xrd_background.png",
    "05_xrd_cif_simulation.png",
}


def settle(milliseconds: int = 180) -> None:
    """Process paint/layout events for a deterministic short interval."""
    application = QApplication.instance()
    assert isinstance(application, QApplication)
    deadline = time.monotonic() + milliseconds / 1000.0
    while time.monotonic() < deadline:
        application.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 25)
        time.sleep(0.005)
    application.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 25)


def save_widget(widget: QWidget, filename: str, *, size: tuple[int, int] | None = None) -> None:
    """Save an unmodified Qt-native widget grab."""
    if size is not None:
        widget.resize(*size)
    widget.show()
    widget.raise_()
    settle()
    pixmap = widget.grab()
    if pixmap.isNull() or pixmap.width() < 240 or pixmap.height() < 75:
        raise RuntimeError(f"{filename}: Qt returned an invalid {pixmap.size()} screenshot.")
    target = OUTPUT / filename
    if not pixmap.save(str(target), "PNG"):
        raise RuntimeError(f"{filename}: QPixmap.save failed.")
    print(f"captured {filename}: {pixmap.width()}x{pixmap.height()}")


def wait_for(predicate: Callable[[], bool], timeout_s: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError("Documentation calculation did not finish")
        settle(25)


def view_all(plot_widget: QWidget) -> None:
    """Reset both axes immediately before a documentation capture."""
    view_box = plot_widget.getViewBox()  # type: ignore[attr-defined]
    view_box.enableAutoRange(axis=ViewBox.XYAxes, enable=True)
    view_box.updateAutoRange()
    settle(120)


def _is_descendant(widget: QWidget, ancestor: QWidget) -> bool:
    current: QWidget | None = widget
    while current is not None:
        if current is ancestor:
            return True
        parent = current.parentWidget()
        current = parent
    return False


def ensure_visible(root: QWidget, target: QWidget) -> None:
    """Scroll the actual control panel until the requested widget is visible."""
    for tabs in root.findChildren(QTabWidget, "SettingsNavigation"):
        for index in range(tabs.count()):
            if _is_descendant(target, tabs.widget(index)):
                tabs.setCurrentIndex(index)
                settle()
                break
    for scroll in root.findChildren(QScrollArea):
        content = scroll.widget()
        if content is not None and _is_descendant(target, content):
            scroll.ensureWidgetVisible(target, 20, 20)
            settle()
            return


def scroll_by(root: QWidget, target: QWidget, pixels: int) -> None:
    """Move the containing documentation panel a small additional distance."""
    for scroll in root.findChildren(QScrollArea):
        content = scroll.widget()
        if content is not None and _is_descendant(target, content):
            bar = scroll.verticalScrollBar()
            bar.setValue(min(bar.maximum(), bar.value() + pixels))
            settle()
            return


def group(root: QWidget, title: str) -> QGroupBox:
    return next(item for item in root.findChildren(QGroupBox) if item.title() == title)


def select_dataset(dataset_list, display_name: str) -> Dataset:  # type: ignore[no-untyped-def]
    if hasattr(dataset_list, "_items"):
        from materials_data_studio.core.measurement_branches import root_dataset
        for dataset in dataset_list.datasets():
            if dataset.metadata.get("measurement_parent") and root_dataset(dataset_list.state, dataset).display_name == display_name:
                dataset_list.list_widget.setCurrentItem(dataset_list._items[dataset.dataset_id])
                settle(60)
                return dataset
        for dataset in dataset_list.datasets():
            if not dataset.metadata.get("measurement_parent") and dataset.display_name == display_name:
                dataset_list.list_widget.setCurrentItem(dataset_list._items[dataset.dataset_id])
                settle(60)
                return dataset
        raise RuntimeError(f"Dataset not found in tree: {display_name}")
    for row in range(dataset_list.list_widget.count()):
        dataset_list.list_widget.setCurrentRow(row)
        selected = dataset_list.current_dataset()
        if selected is not None and selected.display_name == display_name:
            settle(60)
            return selected
    raise RuntimeError(f"Dataset not found in GUI list: {display_name}")


def set_module_visibility(state: ProjectState, module: ModuleType, names: set[str]) -> None:
    for dataset in state.datasets:
        if dataset.module_type is module:
            from materials_data_studio.core.measurement_branches import root_dataset
            dataset.visible = root_dataset(state, dataset).display_name in names


def add_xrd_text(state: ProjectState, relative_name: str) -> Dataset:
    path = Path("docs") / "demo_data" / relative_name
    parser = parse_xrd_rasx if path.suffix.lower() == ".rasx" else parse_xrd_text
    parsed = parser(ROOT / path)
    dataset = Dataset(
        path.stem,
        ModuleType.XRD,
        {"x": parsed.x, "intensity": parsed.intensity},
        source_filename=path.as_posix(),
        source_hash=source_sha256(ROOT / path),
        source_bytes=(ROOT / path).read_bytes(),
        metadata={
            "source_axis": AxisType.TWO_THETA.value,
            "wavelength_a": 1.540598,
            "normalization": NormalizationMode.NONE.value,
            "scale": 1.0,
            "offset": 0.0,
            "background_enabled": False,
            "background_preview": False,
            "fit_ranges": [],
            "excluded_ranges": [],
        },
        warnings=parsed.warnings,
        plot_style=default_plot_style(
            len([item for item in state.datasets if item.module_type is ModuleType.XRD]),
            path.stem,
        ),
    )
    state.add_dataset(dataset)
    return dataset


def populate(window: MainWindow) -> tuple[QWidget, QWidget, QWidget]:
    """Load every screenshot input through the real parsers and dataset adapters."""
    state = window.state
    xrd = window._ensure_module_widget(0)
    mpms = window._ensure_module_widget(1)
    ppms = window._ensure_module_widget(2)
    assert xrd is not None and mpms is not None and ppms is not None

    add_xrd_text(state, "xrd_observed.xy")
    add_xrd_text(state, "xrd_rasx_demo.rasx")
    cif_path = Path("docs") / "demo_data" / "demo_phase.cif"
    document = load_cif(ROOT / cif_path)
    pattern = simulate_cif(
        document,
        wavelength_a=1.540598,
        two_theta_range=(15.0, 82.0),
        fwhm_deg=0.18,
        profile_step_deg=0.01,
    )
    xrd._finish_cif_import(  # type: ignore[attr-defined]
        cif_path,
        document,
        {
            "wavelength_a": 1.540598,
            "two_theta_range": (15.0, 82.0),
            "fwhm_deg": 0.18,
            "profile_step_deg": 0.01,
            "relative_scale": 1.0,
            "vertical_offset": 0.0,
            "target_dataset_id": None,
        },
        pattern,
    )

    for filename in ("mpms_mt_demo.dc.dat", "mpms_mh_demo.dc.dat"):
        path = Path("docs") / "demo_data" / filename
        mpms._finish_import(path, parse_mpms_dc(ROOT / path))  # type: ignore[attr-defined]

    ppms.heat_capacity.import_files(  # type: ignore[attr-defined]
        [Path("docs/demo_data/ppms_heat_capacity_demo.dat")]
    )
    ppms.resistivity.import_files(  # type: ignore[attr-defined]
        [Path("docs/demo_data/ppms_resistivity_demo.dat")]
    )
    ppms.generic.import_files(  # type: ignore[attr-defined]
        [Path("docs/demo_data/ppms_custom_demo.dat")]
    )
    window.apply_theme("light")
    window.resize(*WINDOW_SIZE)
    window.show()
    settle(300)
    return xrd, mpms, ppms


def capture_xrd(
    window: MainWindow, xrd: QWidget, *, overview_only: bool = False
) -> None:
    state = window.state
    window.module_tabs.setCurrentIndex(0)
    observed = select_dataset(xrd.dataset_list, "xrd_observed")  # type: ignore[attr-defined]
    comparison = next(item for item in state.datasets if item.display_name == "xrd_rasx_demo")
    phase = next(item for item in state.datasets if item.display_name == "demo_phase")
    for dataset in (observed, comparison, phase):
        dataset.metadata["normalization"] = NormalizationMode.MAX_100.value
        dataset.metadata["scale"] = 1.0
        dataset.metadata["offset"] = 0.0
        dataset.visible = True
    xrd.dataset_list.refresh()  # type: ignore[attr-defined]
    select_dataset(xrd.dataset_list, "xrd_observed")  # type: ignore[attr-defined]
    xrd.dataset_list.palette_combo.setCurrentText("Viridis")  # type: ignore[attr-defined]
    xrd.dataset_list._apply_palette()  # type: ignore[attr-defined]
    xrd.stacking.setCurrentIndex(xrd.stacking.findData("constant"))  # type: ignore[attr-defined]
    xrd.stacking_offset.setValue(115.0)  # type: ignore[attr-defined]
    ensure_visible(xrd, group(xrd, "Axis"))
    xrd.refresh_plot()  # type: ignore[attr-defined]
    view_all(xrd.plot_widget)  # type: ignore[attr-defined]
    save_widget(window, "01_main_window.png", size=WINDOW_SIZE)

    phase.visible = False
    xrd.log_y_axis.setChecked(True)  # type: ignore[attr-defined]
    xrd.stacking_offset.setValue(10.0)  # type: ignore[attr-defined]
    xrd.dataset_list.refresh()  # type: ignore[attr-defined]
    select_dataset(xrd.dataset_list, "xrd_observed")  # type: ignore[attr-defined]
    xrd.refresh_plot()  # type: ignore[attr-defined]
    ensure_visible(xrd, group(xrd, "Processing"))
    view_all(xrd.plot_widget)  # type: ignore[attr-defined]
    save_widget(window, "02_xrd_multidataset.png", size=WINDOW_SIZE)

    comparison.visible = False
    xrd.log_y_axis.setChecked(False)  # type: ignore[attr-defined]
    observed.metadata["normalization"] = NormalizationMode.NONE.value
    observed.metadata["scale"] = 1.0
    xrd.stacking.setCurrentIndex(xrd.stacking.findData("none"))  # type: ignore[attr-defined]
    xrd.dataset_list.refresh()  # type: ignore[attr-defined]
    select_dataset(xrd.dataset_list, "xrd_observed")  # type: ignore[attr-defined]
    xrd._estimate_background()  # type: ignore[attr-defined]
    wait_for(lambda: observed.metadata.get("background_preview", False))
    ensure_visible(xrd, xrd.background_card)  # type: ignore[attr-defined]
    view_all(xrd.plot_widget)  # type: ignore[attr-defined]
    save_widget(window, "03_xrd_background.png", size=WINDOW_SIZE)

    if not overview_only:
        document = load_cif(ROOT / "docs/demo_data/demo_phase.cif")
        from materials_data_studio.modules.xrd.cif_editor import CifEditorDialog

        editor = CifEditorDialog(
            document,
            xrd,
            simulation_settings={
                "wavelength_a": 1.540598,
                "two_theta_range": (15.0, 82.0),
                "fwhm_deg": 0.18,
                "profile_step_deg": 0.01,
                "relative_scale": 1.0,
                "vertical_offset": 0.0,
            },
        )
        save_widget(editor, "04_cif_editor.png", size=(900, 760))
        editor.close()

    observed.visible = True
    phase.visible = True
    observed.metadata["normalization"] = NormalizationMode.MAX_100.value
    phase.metadata["normalization"] = NormalizationMode.MAX_100.value
    xrd.stacking.setCurrentIndex(xrd.stacking.findData("constant"))  # type: ignore[attr-defined]
    xrd.stacking_offset.setValue(115.0)  # type: ignore[attr-defined]
    xrd.dataset_list.refresh()  # type: ignore[attr-defined]
    select_dataset(xrd.dataset_list, "demo_phase")  # type: ignore[attr-defined]
    xrd.refresh_plot()  # type: ignore[attr-defined]
    ensure_visible(xrd, xrd.lattice_card)  # type: ignore[attr-defined]
    view_all(xrd.plot_widget)  # type: ignore[attr-defined]
    save_widget(window, "05_xrd_cif_simulation.png", size=WINDOW_SIZE)

    if overview_only:
        return

    observed.metadata["normalization"] = NormalizationMode.NONE.value
    observed.metadata["scale"] = 1.0
    observed.metadata["offset"] = 0.0
    observed.processed_data.pop("background", None)
    observed.metadata["background_enabled"] = False
    observed.metadata["background_preview"] = False
    xrd._open_peak_fitting()  # type: ignore[attr-defined]
    fitting = xrd._peak_fitting_window  # type: ignore[attr-defined]
    if fitting is None:
        raise RuntimeError("Peak-fitting window did not open.")
    dataset_index = fitting.dataset_combo.findData(observed.dataset_id)
    phase_index = fitting.phase_combo.findData(phase.dataset_id)
    fitting.dataset_combo.setCurrentIndex(dataset_index)
    fitting.phase_combo.setCurrentIndex(phase_index)
    fitting.fit_min.setValue(15.0)
    fitting.fit_max.setValue(82.0)
    fitting.le_bail_cycles.setValue(6)
    fitting.background_order.setValue(2)
    fitting._refresh_plots()
    settle()
    progress_lines: list[str] = []

    def report(progress) -> None:  # type: ignore[no-untyped-def]
        detail = (
            f"Cycle {progress.cycle}/{progress.total_cycles} | "
            f"Rwp {progress.r_wp:.6g} | max parameter Δ "
            f"{progress.maximum_parameter_change:.4g} | max area Δ "
            f"{progress.maximum_area_change:.4g}"
        )
        progress_lines.append(detail)
        fitting._fit_progress(
            round(100 * progress.cycle / progress.total_cycles),
            detail,
        )
        settle(5)

    result = fit_pattern(fitting._configuration(), progress_callback=report)
    fitting._fit_finished(result)
    fitting.progress_details.setPlainText("\n".join(progress_lines[-6:]))
    fitting.splitter.setSizes([500, 940])
    save_widget(fitting, "06_peak_fitting.png", size=WINDOW_SIZE)
    ensure_visible(fitting, fitting.progress_details)
    scroll_by(fitting, fitting.progress_details, 80)
    save_widget(fitting, "06b_peak_fitting_progress.png", size=WINDOW_SIZE)
    fitting.hide()


def capture_mpms(window: MainWindow, mpms: QWidget) -> None:
    state = window.state
    window.module_tabs.setCurrentIndex(1)
    set_module_visibility(state, ModuleType.MPMS, {"mpms_mt_demo"})
    mpms.dataset_list.refresh()  # type: ignore[attr-defined]
    mt = select_dataset(mpms.dataset_list, "mpms_mt_demo")  # type: ignore[attr-defined]
    mpms.formula.setText("NaCl")  # type: ignore[attr-defined]
    mpms._calculate_mass()  # type: ignore[attr-defined]
    mpms._apply_sample_information()  # type: ignore[attr-defined]
    mpms.tabs.setCurrentIndex(0)  # type: ignore[attr-defined]
    mpms.mt_mode.setCurrentIndex(mpms.mt_mode.findData("chi_inverse"))  # type: ignore[attr-defined]
    ensure_visible(mpms, group(mpms, "M–T and Curie-Weiss"))
    mpms.refresh_plot()  # type: ignore[attr-defined]
    save_widget(window, "07_mpms_mt.png", size=WINDOW_SIZE)

    mpms.fit_branch.setCurrentText("FC")  # type: ignore[attr-defined]
    mpms.fit_t_min.setValue(80.0)  # type: ignore[attr-defined]
    mpms.fit_t_max.setValue(300.0)  # type: ignore[attr-defined]
    mpms.mu_eff.setValue(3.8)  # type: ignore[attr-defined]
    mpms.chi0.setValue(0.0004)  # type: ignore[attr-defined]
    mpms._run_fit()  # type: ignore[attr-defined]
    wait_for(lambda: "curie_weiss_fit" in mt.analysis_results)
    mpms.residual_group.setChecked(True)  # type: ignore[attr-defined]
    ensure_visible(mpms, group(mpms, "M–T and Curie-Weiss"))
    save_widget(window, "08_mpms_curie_weiss.png", size=WINDOW_SIZE)

    set_module_visibility(state, ModuleType.MPMS, {"mpms_mh_demo"})
    mpms.dataset_list.refresh()  # type: ignore[attr-defined]
    select_dataset(mpms.dataset_list, "mpms_mh_demo")  # type: ignore[attr-defined]
    mpms.formula.setText("NaCl")  # type: ignore[attr-defined]
    mpms._calculate_mass()  # type: ignore[attr-defined]
    mpms._apply_sample_information()  # type: ignore[attr-defined]
    mpms.tabs.setCurrentIndex(1)  # type: ignore[attr-defined]
    mpms.residual_group.setChecked(False)  # type: ignore[attr-defined]
    mpms.residual_plot.clear()  # type: ignore[attr-defined]
    mpms.field_unit.setCurrentIndex(mpms.field_unit.findData("kOe"))  # type: ignore[attr-defined]
    mpms.magnetization_unit.setCurrentIndex(  # type: ignore[attr-defined]
        mpms.magnetization_unit.findData("emu/g")  # type: ignore[attr-defined]
    )
    ensure_visible(mpms, group(mpms, "M–H Sweeps"))
    mpms.refresh_plot()  # type: ignore[attr-defined]
    save_widget(window, "09_mpms_mh.png", size=WINDOW_SIZE)


def capture_heat_capacity(window: MainWindow, ppms: QWidget) -> None:
    window.module_tabs.setCurrentIndex(2)
    ppms.tabs.setCurrentWidget(ppms.heat_capacity)  # type: ignore[attr-defined]
    heat = ppms.heat_capacity  # type: ignore[attr-defined]
    heat.splitter.setSizes([280, 720, 440])
    select_dataset(heat.dataset_list, "ppms_heat_capacity_demo")
    heat.unit.setCurrentIndex(heat.unit.findData("molar"))
    heat.plot_tabs.setCurrentIndex(0)
    heat.refresh_plot()
    ensure_visible(heat, group(heat, "Sample Information"))
    save_widget(window, "10_heat_capacity_cp.png", size=WINDOW_SIZE)

    heat.low_min.setValue(2.0)
    heat.low_max.setValue(10.0)
    heat.fix_low_gamma.setChecked(False)
    heat._run_low_fit()
    heat.show_low_residual.setChecked(True)
    heat.show_de_residual.setChecked(False)
    heat.plot_tabs.setCurrentIndex(1)
    heat.ct_plot.setXRange(4.0, 100.0, padding=0)
    heat.residual_widget.setXRange(4.0, 100.0, padding=0)
    ensure_visible(heat, group(heat, "C/T vs T² Low-T Fit"))
    save_widget(window, "11_heat_capacity_low_t.png", size=WINDOW_SIZE)

    dataset = heat.dataset_list.current_dataset()
    assert dataset is not None
    segment, _segments = heat._current_segment(dataset)
    indices = segment.indices
    cp, _error = heat._molar_arrays(dataset)
    heat.de_min.setValue(2.0)
    heat.de_max.setValue(120.0)
    heat.theta_d.setValue(230.0)
    heat.theta_e1.setValue(410.0)
    heat.theta_e2.setValue(790.0)
    heat.weight_d.setValue(0.85)
    heat.weight_e1.setValue(0.70)
    heat.weight_e2.setValue(0.45)
    heat.gamma.setValue(0.006)
    for checkbox in (
        heat.fix_theta_d,
        heat.fix_theta_e1,
        heat.fix_theta_e2,
        heat.fix_weight_d,
        heat.fix_weight_e1,
        heat.fix_weight_e2,
    ):
        checkbox.setChecked(True)
    heat.fix_gamma.setChecked(False)
    heat.constrain_atoms.setChecked(False)
    de_result = fit_debye_einstein(
        heat._data_array(dataset, "temperature_k")[indices],
        cp[indices],
        minimum_k=2.0,
        maximum_k=120.0,
        n_einstein=2,
        initial={
            "gamma": 0.006,
            "theta_d": 230.0,
            "weight_d": 0.85,
            "theta_e1": 410.0,
            "weight_e1": 0.70,
            "theta_e2": 790.0,
            "weight_e2": 0.45,
        },
        fixed={
            "theta_d": 230.0,
            "weight_d": 0.85,
            "theta_e1": 410.0,
            "weight_e1": 0.70,
            "theta_e2": 790.0,
            "weight_e2": 0.45,
        },
    )
    heat._finish_debye_fit(dataset, indices, de_result)
    heat.entropy_min.setValue(2.0)
    heat.entropy_max.setValue(120.0)
    heat._run_entropy()
    heat.show_de_residual.setChecked(True)
    heat.show_low_residual.setChecked(False)
    heat.plot_tabs.setCurrentIndex(0)
    ensure_visible(heat, heat.entropy_result)
    scroll_by(heat, heat.entropy_result, 170)
    save_widget(window, "12_heat_capacity_debye_einstein.png", size=WINDOW_SIZE)


def _set_bridge_geometry(resistivity, bridge_number: int) -> None:  # type: ignore[no-untyped-def]
    selected = resistivity.dataset_list.current_dataset()
    if hasattr(resistivity.dataset_list, "_items"):
        for dataset in resistivity.dataset_list.datasets():
            if dataset.metadata.get("measurement_branch", {}).get("bridge") == bridge_number:
                resistivity.dataset_list.list_widget.setCurrentItem(resistivity.dataset_list._items[dataset.dataset_id])
                resistivity.sample_width.setValue(0.20)
                resistivity.thickness.setValue(0.02)
                resistivity.length.setValue(0.50)
                resistivity._save_bridge()
        if selected is not None:
            resistivity.dataset_list.list_widget.setCurrentItem(resistivity.dataset_list._items[selected.dataset_id])
        return
    index = resistivity.bridge.findData(bridge_number)
    resistivity.bridge.setCurrentIndex(index)
    resistivity.sample_width.setValue(0.20)
    resistivity.thickness.setValue(0.02)
    resistivity.length.setValue(0.50)
    resistivity._save_bridge()


def capture_resistivity(window: MainWindow, ppms: QWidget) -> None:
    ppms.tabs.setCurrentWidget(ppms.resistivity)  # type: ignore[attr-defined]
    resistivity = ppms.resistivity  # type: ignore[attr-defined]
    resistivity.splitter.setSizes([280, 720, 440])
    select_dataset(resistivity.dataset_list, "ppms_resistivity_demo")
    _set_bridge_geometry(resistivity, 1)
    _set_bridge_geometry(resistivity, 2)
    if not hasattr(resistivity.dataset_list, "_items"):
        resistivity.bridge_checks[1].setChecked(True)
        resistivity.bridge_checks[2].setChecked(False)
        resistivity.bridge.setCurrentIndex(resistivity.bridge.findData(1))
    resistivity.vertical_unit.setCurrentIndex(
        resistivity.vertical_unit.findData("resistivity")
    )
    resistivity.display_mode.setCurrentIndex(
        resistivity.display_mode.findData("normal")
    )
    resistivity.plot_tabs.setCurrentIndex(0)
    ensure_visible(resistivity, group(resistivity, "Bridge Controls"))
    resistivity.refresh_plot()
    save_widget(window, "13_resistivity_rho_t.png", size=WINDOW_SIZE)

    resistivity.display_mode.setCurrentIndex(
        resistivity.display_mode.findData("arrhenius")
    )
    resistivity.arrhenius_x.setCurrentIndex(
        resistivity.arrhenius_x.findData("thousand")
    )
    resistivity.fit_min.setValue(40.0)
    resistivity.fit_max.setValue(300.0)
    resistivity._run_fit()
    ensure_visible(resistivity, resistivity.fit_result)
    scroll_by(resistivity, resistivity.fit_result, 80)
    save_widget(window, "14_resistivity_arrhenius.png", size=WINDOW_SIZE)

    if hasattr(resistivity.dataset_list, "_items"):
        branch = next(dataset for dataset in resistivity.dataset_list.datasets()
                      if dataset.metadata.get("measurement_branch", {}).get("bridge") == 2
                      and dataset.metadata["measurement_branch"].get("mode") == "rho_t")
        resistivity.dataset_list.list_widget.setCurrentItem(resistivity.dataset_list._items[branch.dataset_id])
    else:
        resistivity.bridge_checks[1].setChecked(False)
        resistivity.bridge_checks[2].setChecked(True)
        resistivity.bridge.setCurrentIndex(resistivity.bridge.findData(2))
    resistivity.display_mode.setCurrentIndex(
        resistivity.display_mode.findData("vrh")
    )
    resistivity.vrh_dimension.setCurrentIndex(
        resistivity.vrh_dimension.findData(3)
    )
    resistivity.fit_min.setValue(40.0)
    resistivity.fit_max.setValue(300.0)
    resistivity._run_fit()
    ensure_visible(resistivity, resistivity.fit_result)
    scroll_by(resistivity, resistivity.fit_result, 80)
    save_widget(window, "15_resistivity_vrh.png", size=WINDOW_SIZE)

    if not hasattr(resistivity.dataset_list, "_items"):
        resistivity.bridge_checks[1].setChecked(True)
        resistivity.bridge_checks[2].setChecked(False)
        resistivity.bridge.setCurrentIndex(resistivity.bridge.findData(1))
    resistivity.display_mode.setCurrentIndex(
        resistivity.display_mode.findData("normal")
    )
    resistivity.plot_tabs.setCurrentIndex(1)
    resistivity.field_unit.setCurrentIndex(resistivity.field_unit.findData("kOe"))
    for index in range(resistivity.segment.count()):
        if "Increasing" in resistivity.segment.itemText(index):
            resistivity.segment.setCurrentIndex(index)
            break
    if hasattr(resistivity.dataset_list, "_items"):
        field_branch = next(dataset for dataset in resistivity.dataset_list.datasets()
                            if dataset.metadata.get("measurement_branch", {}).get("mode") == "rho_h"
                            and dataset.metadata["measurement_branch"]["bridge"] == 1)
        resistivity.dataset_list.list_widget.setCurrentItem(resistivity.dataset_list._items[field_branch.dataset_id])
    ensure_visible(resistivity, group(resistivity, "Sweep and Plot"))
    resistivity.refresh_plot()
    save_widget(window, "16_resistivity_rho_h.png", size=WINDOW_SIZE)


def capture_custom(window: MainWindow, ppms: QWidget) -> None:
    ppms.tabs.setCurrentWidget(ppms.generic)  # type: ignore[attr-defined]
    generic = ppms.generic  # type: ignore[attr-defined]
    select_dataset(generic.dataset_list, "ppms_custom_demo")
    generic.formula.setText("NaCl")
    generic._calculate_formula()
    generic.new_name.setText("normalized_signal")
    generic.expression.setText("custom_signal * 1e-6 / sample_moles")
    generic._create_column()
    for combo, text in (
        (generic.x_axis, "Temperature (K)"),
        (generic.y_axis, "normalized_signal"),
    ):
        index = combo.findText(text)
        if index < 0:
            raise RuntimeError(f"Custom Plot axis not found: {text}")
        combo.setCurrentIndex(index)
    ensure_visible(generic, group(generic, "Column Calculation"))
    generic.refresh_plot()
    save_widget(window, "17_ppms_custom_plot.png", size=WINDOW_SIZE)

    generic.fit_formula.setText("a*x+b")
    generic.fit_min.setValue(5.0)
    generic.fit_max.setValue(300.0)
    generic._run_fit()
    wait_for(lambda: "generic_fit" in generic.dataset_list.current_dataset().analysis_results)
    ensure_visible(generic, generic.fit_result)
    scroll_by(generic, generic.fit_result, 80)
    save_widget(window, "18_ppms_custom_fit.png", size=WINDOW_SIZE)


def capture_modal(
    action: Callable[[], object],
    filename: str,
    configure: Callable[[QWidget], None] | None = None,
) -> None:
    """Capture the real modal dialog opened by a production action."""
    application = QApplication.instance()
    assert isinstance(application, QApplication)
    captured = False

    def attempt() -> None:
        nonlocal captured
        dialog = application.activeModalWidget()
        if dialog is None:
            QTimer.singleShot(80, attempt)
            return
        if configure is not None:
            configure(dialog)
        settle(140)
        save_widget(dialog, filename)
        captured = True
        if hasattr(dialog, "reject"):
            dialog.reject()  # type: ignore[attr-defined]
        else:
            dialog.close()

    QTimer.singleShot(120, attempt)
    action()
    settle()
    if not captured:
        raise RuntimeError(f"{filename}: no modal dialog was captured.")


def capture_dialogs(window: MainWindow, xrd: QWidget) -> None:
    window.apply_theme("dark")
    save_widget(window, "29_dark_toolbar.png", size=WINDOW_SIZE)

    def select_dark(dialog: QWidget) -> None:
        dialog.resize(620, 760)
        for combo in dialog.findChildren(QComboBox):
            index = combo.findText("Dark")
            if index >= 0:
                combo.setCurrentIndex(index)
        for tabs in dialog.findChildren(QTabWidget):
            for index in range(tabs.count()):
                if tabs.tabText(index) == "Dark colors":
                    tabs.setCurrentIndex(index)
                    break

    capture_modal(window.show_preferences, "19_preferences_dark.png", select_dark)
    window.apply_theme("light")

    def select_igor(dialog: QWidget) -> None:
        dialog.resize(760, 680)
        for tabs in dialog.findChildren(QTabWidget):
            for index in range(tabs.count()):
                if tabs.tabText(index) == "Applications":
                    tabs.setCurrentIndex(index)
                    break
        executable = getattr(dialog, "igor_executable", None)
        if executable is not None:
            executable.setText(
                r"C:\Program Files\WaveMetrics\Igor Pro 10 Folder\IgorBinaries_x64\Igor64.exe"
            )

    capture_modal(window.show_preferences, "32_igor_preferences.png", select_igor)

    interactive_export = InteractiveExportDialog(xrd.current_spec, window)  # type: ignore[attr-defined]
    save_widget(interactive_export, "30_interactive_export_data.png", size=(720, 640))
    interactive_export.close()
    raster_export = InteractiveExportDialog(xrd.current_spec, window)  # type: ignore[attr-defined]
    raster_export.tabs.setCurrentIndex(2)
    raster_export.raster_format.setCurrentIndex(
        raster_export.raster_format.findData("tiff")
    )
    raster_export.raster_dpi.setValue(600)
    raster_export.raster_transparent.setChecked(True)
    save_widget(raster_export, "31_interactive_export_raster.png", size=(720, 640))
    raster_export.close()
    h5xp_export = InteractiveExportDialog(xrd.current_spec, window)  # type: ignore[attr-defined]
    h5xp_export.tabs.setCurrentIndex(3)
    save_widget(h5xp_export, "33_interactive_export_h5xp.png", size=(720, 640))
    h5xp_export.close()

    def session_dialog(dialog: QWidget) -> None:
        if isinstance(dialog, QFileDialog):
            dialog.resize(960, 640)
            dialog.setSidebarUrls(
                [QUrl.fromLocalFile("C:/Windows"), QUrl.fromLocalFile("C:/")]
            )
            dialog.setDirectory("C:/Windows")
            dialog.selectFile("Materials_Data_Studio_demo.mdss")

    capture_modal(window.save_session_as, "20_session_save_dialog.png", session_dialog)

    def open_session_dialog(dialog: QWidget) -> None:
        if isinstance(dialog, QFileDialog):
            dialog.resize(960, 640)
            dialog.setSidebarUrls(
                [QUrl.fromLocalFile("C:/Windows"), QUrl.fromLocalFile("C:/")]
            )
            dialog.setDirectory("C:/Windows")
            dialog.selectFile("Materials_Data_Studio_demo.mdss")

    capture_modal(window.open_session, "20b_session_open_dialog.png", open_session_dialog)

    window.module_tabs.setCurrentIndex(0)
    select_dataset(xrd.dataset_list, "xrd_observed")  # type: ignore[attr-defined]

    def export_dialog(dialog: QWidget) -> None:
        if isinstance(dialog, InteractiveExportDialog):
            dialog.resize(720, 640)
            dialog.tabs.setCurrentIndex(0)

    capture_modal(window.export_data, "21_data_export_dialog.png", export_dialog)

    def manual_dialog(dialog: QWidget) -> None:
        dialog.resize(1120, 820)

    capture_modal(window.show_user_manual, "22_user_manual_dialog.png", manual_dialog)


def capture_menu(window: MainWindow, menu: QMenu, filename: str) -> None:
    """Capture a native top-level menu together with the application window."""
    window.module_tabs.setCurrentIndex(0)
    window.resize(*WINDOW_SIZE)
    window.show()
    window.raise_()
    settle()
    menu_bar = window.menuBar()
    menu_rect = menu_bar.actionGeometry(menu.menuAction())
    menu.popup(
        menu_bar.mapToGlobal(QPoint(menu_rect.left(), menu_bar.height()))
    )
    settle()
    pixmap = window.grab()
    menu_pixmap = menu.grab()
    menu_position = window.mapFromGlobal(menu.pos())
    painter = QPainter(pixmap)
    painter.drawPixmap(menu_position, menu_pixmap)
    painter.end()
    if pixmap.isNull() or menu_pixmap.isNull():
        raise RuntimeError(f"{filename}: widget capture failed.")
    target = OUTPUT / filename
    if not pixmap.save(str(target), "PNG"):
        raise RuntimeError(f"{filename}: QPixmap.save failed.")
    print(f"captured {filename}: {pixmap.width()}x{pixmap.height()}")
    menu.hide()


def capture_tools(window: MainWindow) -> None:
    """Capture the top-level menus and each implemented utility window."""
    capture_menu(window, window.tools_menu, "23_tools_launcher.png")

    molar = window.tool_manager.open_tool("molar_mass")
    if not isinstance(molar, MolarMassWindow):
        raise RuntimeError("Molecular Weight Calculator did not open.")
    for formula in ("CuSO4·5H2O", "La2NiMnO6", "NaCl"):
        molar.formula.setText(formula)
        molar.calculate()
    save_widget(molar, "24_molecular_weight.png", size=(720, 560))
    molar.close()

    shannon = window.tool_manager.open_tool("shannon_radii")
    if not isinstance(shannon, ShannonRadiiWindow):
        raise RuntimeError("Shannon Radii did not open.")
    shannon.select_element("Fe")
    shannon.table.sortItems(1, Qt.SortOrder.AscendingOrder)
    save_widget(shannon, "25_shannon_radii.png", size=(1220, 820))
    shannon.close()

    converter = window.tool_manager.open_tool("rasx_converter")
    if not isinstance(converter, RasxConverterWindow):
        raise RuntimeError("RASX Converter did not open.")
    converter.add_files(
        [
            DEMO / "xrd_rasx_demo.rasx",
            DEMO / "xrd_rasx_demo_secondary.rasx",
        ]
    )
    save_widget(converter, "26_rasx_converter.png", size=(820, 700))
    converter.close()

    window.undo_history.reset(clean=True)
    capture_menu(window, window.file_menu, "27_file_menu.png")
    capture_menu(window, window.help_menu, "28_help_menu.png")


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    dialogs_only = "--dialogs-only" in sys.argv[1:]
    xrd_overview_only = "--xrd-overview-only" in sys.argv[1:]
    menus_only = "--menus-only" in sys.argv[1:]
    if sum((dialogs_only, xrd_overview_only, menus_only)) > 1:
        raise SystemExit("Choose only one capture mode.")
    expected = (
        {"23_tools_launcher.png", "27_file_menu.png"}
        if menus_only
        else DIALOG_SCREENSHOTS
        if dialogs_only
        else XRD_OVERVIEW_SCREENSHOTS
        if xrd_overview_only
        else FULL_SCREENSHOTS
    )
    try:
        QCoreApplication.setApplicationName(APP_NAME)
        QCoreApplication.setOrganizationName(ORGANIZATION_NAME)
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(
            QSettings.Format.IniFormat,
            QSettings.Scope.UserScope,
            str(TEMP_ROOT / "settings"),
        )
        QApplication.setAttribute(
            Qt.ApplicationAttribute.AA_DontUseNativeDialogs, True
        )
        application = QApplication.instance()
        app = application if isinstance(application, QApplication) else QApplication(sys.argv)
        from manual_fonts import configure_capture_fonts
        configure_capture_fonts(app)
        app.setStyle("Fusion")
        state = ProjectState()
        window = MainWindow(state)
        xrd, mpms, ppms = populate(window)
        window.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        if menus_only:
            capture_menu(window, window.tools_menu, "23_tools_launcher.png")
            capture_menu(window, window.file_menu, "27_file_menu.png")
        elif xrd_overview_only:
            capture_xrd(window, xrd, overview_only=True)
        elif not dialogs_only:
            capture_xrd(window, xrd)
            capture_mpms(window, mpms)
            capture_heat_capacity(window, ppms)
            capture_resistivity(window, ppms)
            capture_custom(window, ppms)
        if not xrd_overview_only and not menus_only:
            capture_tools(window)
            capture_dialogs(window, xrd)
        window.undo_history.mark_clean()
        window.close()
        settle()
        produced = {path.name for path in OUTPUT.glob("*.png")}
        missing = sorted(expected - produced)
        if missing:
            raise RuntimeError("Missing screenshot(s): " + ", ".join(missing))
        print(f"Captured/verified {len(expected)} reproducible screenshots in {OUTPUT}")
        return 0
    finally:
        shutil.rmtree(TEMP_ROOT, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
