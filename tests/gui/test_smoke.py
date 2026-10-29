from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

from materials_data_studio.core.models import Dataset, ModuleType
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.core.session import save_session

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None
    or importlib.util.find_spec("pyqtgraph") is None,
    reason="Qt GUI dependencies are not installed",
)


def test_main_window_has_registered_tabs(qtbot) -> None:  # type: ignore[no-untyped-def]
    from materials_data_studio.ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    assert [window.module_tabs.tabText(index) for index in range(3)] == [
        "XRD",
        "MPMS",
        "PPMS",
    ]
    assert [action.text() for action in window.menuBar().actions()] == [
        "File",
        "Tools",
        "View",
        "Help",
    ]
    assert [
        action.text() for action in window.file_menu.actions() if not action.isSeparator()
    ] == [
        "New Session",
        "Open Session",
        "Save Session",
        "Save Session As",
        "Import Data",
        "Open Igor Experiment (.pxp)",
        "Open Graph Document (.mdsg)",
        "Save Graph Document",
        "Save Graph Document As",
        "Export Data",
        "Export Figure",
        "Send to Igor Pro",
        "Undo",
        "Redo",
        "Edit current plot in Graph Editor",
        "Analysis Workbench…",
        "Recent files / resume workspace",
    ]
    assert window.tools_menu.title() == "Tools"
    assert [action.text() for action in window.tools_menu.actions()] == [
        "Molecular Weight Calculator",
        "Shannon Radii",
        "RASX Converter",
        "Graph Editor / Igor Import",
    ]
    assert [
        action.text() for action in window.help_menu.actions() if not action.isSeparator()
    ] == [
        "Check for updates…",
        "Check for updates at startup",
        "Preferences",
        "User Manual (English)",
        "ユーザーマニュアル (日本語)",
        "Licenses and Source (English)",
        "ライセンス・対応ソース (日本語)",
        "Create Diagnostic Bundle",
        "About",
        "Context help",
        "Recovery Center…",
    ]
    toolbar_actions = [
        action for action in window.main_toolbar.actions() if not action.isSeparator()
    ]
    assert [action.text() for action in toolbar_actions] == [
        "New Session",
        "Open Session",
        "Save Session",
        "Import Data",
        "Export Data",
        "Export Figure",
        "Send to Igor Pro",
        "Edit current plot in Graph Editor",
        "Undo",
        "Redo",
    ]
    assert all(not action.icon().isNull() for action in toolbar_actions)
    assert window.main_toolbar.iconSize().width() == 15
    assert window.module_tabs.tabBar().property("navigation") == "primary"
    assert not window.module_tabs.tabBar().expanding()


def test_fresh_main_window_remains_clean_and_closes(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtWidgets import QMessageBox

    from materials_data_studio.ui.main_window import MainWindow

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Discard,
    )
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.wait(500)
    assert not window.state.modified
    assert window.undo_stack.count() == 0
    assert window.close()
    assert not window.isVisible()


def test_flexible_numeric_input_allows_insertion_before_commit(qtbot) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtCore import Qt

    from materials_data_studio.ui.numeric_inputs import FlexibleDoubleSpinBox

    field = FlexibleDoubleSpinBox()
    qtbot.addWidget(field)
    field.setDecimals(8)
    field.setRange(0.0, 100.0)
    field.setValue(5.64)
    field.show()
    assert field.text() == "5.64"
    editor = field.lineEdit()
    editor.setFocus()
    editor.setCursorPosition(0)
    qtbot.keyClicks(editor, "1")
    assert editor.text() == "15.64"
    assert field.value() == pytest.approx(5.64)
    qtbot.keyPress(editor, Qt.Key.Key_Return)
    assert field.value() == pytest.approx(15.64)


def test_generated_toolbar_icon_assets_are_theme_complete() -> None:
    from PySide6.QtGui import QImage

    from materials_data_studio.ui.icons import (
        TOOLBAR_ICON_NAMES,
        toolbar_icon_path,
    )

    for theme in ("light", "dark"):
        for name in TOOLBAR_ICON_NAMES:
            image = QImage(str(toolbar_icon_path(name, theme)))
            assert not image.isNull()
            assert image.width() == 64
            assert image.height() == 64
            assert image.hasAlphaChannel()
            assert image.pixelColor(0, 0).alpha() == 0


def test_dark_theme_updates_qt_and_plot_scenes(qtbot) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtWidgets import QApplication

    from materials_data_studio.ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    window.apply_theme("light")
    light_icon_key = window.new_action.icon().cacheKey()
    window.apply_theme("dark")
    from materials_data_studio.ui.theme import DARK
    assert DARK.background in QApplication.instance().styleSheet()
    assert window.new_action.icon().cacheKey() != light_icon_key
    xrd = window.module_tabs.widget(0)
    assert xrd.plot_widget.property("materialsTheme") == "dark"
    window.apply_theme("light")


def test_open_session_action_does_not_pass_checked_bool(
    qtbot, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtWidgets import QFileDialog

    from materials_data_studio.ui.main_window import MainWindow

    session_path = tmp_path / "action-open.mdss"
    source = ProjectState()
    source.add_dataset(
        Dataset(
            "Loaded",
            ModuleType.XRD,
            {"x": np.array([10.0, 20.0]), "intensity": np.array([1.0, 2.0])},
        )
    )
    source.datasets[0].plot_style.line_style = 2  # type: ignore[assignment]
    save_session(source, session_path)
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *args, **kwargs: (str(session_path), ""),
    )
    window = MainWindow()
    qtbot.addWidget(window)

    window.open_action.trigger()

    assert [dataset.display_name for dataset in window.state.datasets] == ["Loaded"]
    assert window.state.path == session_path
    assert window.state.datasets[0].plot_style.line_style == "--"


def test_toolbar_undo_and_redo_restore_workspace_snapshots(qtbot) -> None:  # type: ignore[no-untyped-def]
    from materials_data_studio.ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    window.state.add_dataset(
        Dataset(
            "Undo example",
            ModuleType.XRD,
            {"x": np.array([10.0, 20.0]), "intensity": np.array([1.0, 2.0])},
            metadata={
                "source_axis": "two_theta",
                "wavelength_a": 1.540598,
                "normalization": "none",
                "scale": 1.0,
                "offset": 0.0,
                "background_enabled": False,
            },
        )
    )
    window.active_widget().refresh()
    window.undo_history.checkpoint()
    assert window.undo_action.isEnabled()
    assert not window.redo_action.isEnabled()

    window.undo_action.trigger()
    assert not window.state.datasets
    assert window.redo_action.isEnabled()

    window.redo_action.trigger()
    assert [dataset.display_name for dataset in window.state.datasets] == [
        "Undo example"
    ]
    window.undo_history.mark_clean()


def test_user_manual_action_opens_bundled_markdown(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    from materials_data_studio.ui.main_window import MainWindow
    from materials_data_studio.ui.manual import UserManualDialog

    rendered: list[str] = []
    monkeypatch.setattr(
        UserManualDialog,
        "exec",
        lambda dialog: rendered.append(dialog.browser.toPlainText()) or 0,
    )
    window = MainWindow()
    qtbot.addWidget(window)
    window.manual_action.trigger()
    assert rendered
    assert "Materials Data Studio" in rendered[0]
    assert "XRD" in rendered[0]

    window.japanese_manual_action.trigger()
    assert len(rendered) == 2
    assert "ユーザーマニュアル" in rendered[1]


def test_manual_search_return_finds_text_without_closing(qtbot) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtCore import Qt

    from materials_data_studio.ui.manual import (
        UserManualDialog,
        resolve_user_manual_path,
    )

    dialog = UserManualDialog(resolve_user_manual_path())
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.search.setText("XRD")
    qtbot.keyClick(dialog.search, Qt.Key.Key_Return)
    assert dialog.isVisible()
    assert dialog.find_status.text() in {"Match found", "Wrapped to start"}


def test_manual_table_of_contents_link_navigates_to_section(qtbot) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtCore import Qt

    from materials_data_studio.ui.manual import (
        UserManualDialog,
        resolve_user_manual_path,
    )

    dialog = UserManualDialog(resolve_user_manual_path())
    qtbot.addWidget(dialog)
    dialog.show()
    html = dialog.browser.document().toHtml()
    assert 'href="manual:section-8"' in html
    link_cursor = dialog.browser.document().find("8. Tools")
    link_cursor.setPosition(link_cursor.selectionStart())
    qtbot.mouseClick(
        dialog.browser.viewport(),
        Qt.MouseButton.LeftButton,
        pos=dialog.browser.cursorRect(link_cursor).center(),
    )
    qtbot.wait(20)
    assert dialog.browser.textCursor().block().text() == "8. Tools"
    assert dialog.browser.textCursor().block().blockFormat().headingLevel() > 0
    assert dialog._section_positions["section-8"] > link_cursor.selectionStart()


def test_unvisited_modules_load_only_when_selected(qtbot) -> None:  # type: ignore[no-untyped-def]
    from materials_data_studio.ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    modules = window.registry.modules()
    assert modules[0].widget is not None
    assert modules[1].widget is None
    assert modules[2].widget is None
    window.module_tabs.setCurrentIndex(1)
    assert modules[1].widget is window.module_tabs.widget(1)
    assert modules[2].widget is None
    window.undo_history.reset(clean=True)
    window.state.modified = False
    window.close()


def test_preferences_reset_requires_confirmation(qtbot) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialogButtonBox

    from materials_data_studio.plotting.appearance import DEFAULT_PLOT_APPEARANCE
    from materials_data_studio.ui.preferences import PreferencesDialog

    rejected = PreferencesDialog(
        "dark",
        DEFAULT_PLOT_APPEARANCE,
        confirm_reset=lambda: False,
    )
    qtbot.addWidget(rejected)
    rejected.axis_label_font_size.setValue(17)
    rejected_reset = rejected.buttons.button(QDialogButtonBox.StandardButton.Reset)
    assert rejected_reset is not None
    qtbot.mouseClick(
        rejected_reset,
        Qt.MouseButton.LeftButton,
    )
    assert rejected.selected_theme() == "dark"
    assert rejected.axis_label_font_size.value() == 17

    confirmed = PreferencesDialog(
        "dark",
        DEFAULT_PLOT_APPEARANCE,
        igor_executable=r"C:\Program Files\WaveMetrics\Igor64.exe",
        confirm_reset=lambda: True,
    )
    qtbot.addWidget(confirmed)
    assert [
        confirmed.plot_tabs.tabText(index) for index in range(confirmed.plot_tabs.count())
    ] == ["Elements", "Ticks", "Text & lines", "Light colors", "Dark colors", "Applications"]
    assert confirmed.igor_executable_path().endswith("Igor64.exe")
    confirmed.axis_label_font_size.setValue(17)
    confirmed_reset = confirmed.buttons.button(QDialogButtonBox.StandardButton.Reset)
    assert confirmed_reset is not None
    qtbot.mouseClick(
        confirmed_reset,
        Qt.MouseButton.LeftButton,
    )
    assert confirmed.selected_theme() == "light"
    assert confirmed.plot_appearance() == DEFAULT_PLOT_APPEARANCE
    assert confirmed.igor_executable_path() == ""


def test_preferences_apply_emits_without_closing(qtbot) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialogButtonBox

    from materials_data_studio.plotting.appearance import DEFAULT_PLOT_APPEARANCE
    from materials_data_studio.ui.preferences import PreferencesDialog

    dialog = PreferencesDialog("light", DEFAULT_PLOT_APPEARANCE)
    qtbot.addWidget(dialog)
    dialog.show()
    applied: list[tuple[str, int]] = []
    dialog.applyRequested.connect(
        lambda: applied.append(
            (dialog.selected_theme(), dialog.axis_label_font_size.value())
        )
    )
    dialog.theme.setCurrentIndex(dialog.theme.findData("dark"))
    dialog.axis_label_font_size.setValue(15)
    apply_button = dialog.buttons.button(QDialogButtonBox.StandardButton.Apply)
    assert apply_button is not None
    qtbot.mouseClick(apply_button, Qt.MouseButton.LeftButton)
    assert applied == [("dark", 15)]
    assert dialog.isVisible()


def test_file_export_data_uses_interactive_export_dialog(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    from materials_data_studio.plotting import pyqtgraph_backend
    from materials_data_studio.ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    xrd = window.active_widget()
    exported: list[object] = []
    monkeypatch.setattr(
        pyqtgraph_backend,
        "export_interactive_plot",
        lambda plot: exported.append(plot),
    )
    window.export_data_action.trigger()
    assert exported == [xrd.plot_widget]


def test_file_send_to_igor_is_external_output_only(
    qtbot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    from materials_data_studio.integrations import igor
    from materials_data_studio.plotting.igor_h5xp import IgorExportResult
    from materials_data_studio.ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    window.state.modified = False
    undo_count = window.undo_stack.count()
    captured = []

    def fake_send(self, graph_state, configured_executable=""):  # type: ignore[no-untyped-def]
        captured.append((graph_state, configured_executable))
        path = tmp_path / "sent.h5xp"
        path.write_bytes(b"validated")
        return IgorExportResult(True, path, [], 0, 1)

    monkeypatch.setattr(igor.IgorTransferService, "send", fake_send)
    window.send_to_igor_action.trigger()
    assert len(captured) == 1
    assert captured[0][0].module == "XRD"
    assert window.undo_stack.count() == undo_count
    assert not window.state.modified
    assert window.statusBar().currentMessage() == "Sent to Igor Pro"


def test_xrd_and_mpms_loaded_curves_reach_interactive_plots(qtbot) -> None:  # type: ignore[no-untyped-def]
    from materials_data_studio.modules.mpms.widget import MpmsWidget
    from materials_data_studio.modules.xrd.widget import XrdWidget

    xrd_state = ProjectState()
    xrd_state.add_dataset(
        Dataset(
            "XRD",
            ModuleType.XRD,
            {
                "x": np.linspace(10.0, 80.0, 1000),
                "intensity": np.linspace(1.0, 1000.0, 1000),
            },
            metadata={
                "source_axis": "two_theta",
                "wavelength_a": 1.540598,
                "normalization": "none",
                "scale": 1.0,
                "offset": 0.0,
                "background_enabled": False,
            },
        )
    )
    xrd = XrdWidget(xrd_state)
    qtbot.addWidget(xrd)
    assert len(xrd.current_spec.curves) == 1
    assert len(xrd.plot_widget.listDataItems()) == 1
    xrd.log_y_axis.setChecked(True)
    assert xrd.current_spec.y_log_scale
    assert xrd.plot_widget.listDataItems()[0].opts["logMode"] == [False, True]

    temperature = np.concatenate(
        (np.linspace(2.0, 300.0, 100), np.linspace(300.0, 2.0, 100))
    )
    mpms_state = ProjectState()
    mpms_state.add_dataset(
        Dataset(
            "MPMS",
            ModuleType.MPMS,
            {
                "time_s": np.arange(temperature.size, dtype=float),
                "field_oe": np.full(temperature.size, 1000.0),
                "temperature_k": temperature,
                "moment_emu": 0.001 * (temperature + 10.0),
            },
            metadata={
                "sample_weight_mg": 10.0,
                "molar_mass_g_mol": 100.0,
                "formula": "",
                "automatic_split_index": 99,
                "manual_split_index": None,
                "sweep_segments": [],
            },
        )
    )
    mpms = MpmsWidget(mpms_state)
    qtbot.addWidget(mpms)
    assert len(mpms.current_spec.curves) == 2
    assert len(mpms.mt_plot.listDataItems()) == 2


def test_cif_lattice_controls_are_constrained_live_and_wavelength_recalculates(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtCore import QPointF

    import materials_data_studio.modules.xrd.widget as xrd_widget_module
    from materials_data_studio.modules.xrd.cif_backend import extract_cif_tables
    from materials_data_studio.modules.xrd.cif_editor import CifDocument, CifEditorDialog
    from materials_data_studio.modules.xrd.simulation import SimulatedPattern
    from materials_data_studio.modules.xrd.widget import XrdWidget

    cif_text = """data_NaCl
_symmetry_space_group_name_H-M 'F m -3 m'
_cell_length_a 5.6402
_cell_length_b 5.6402
_cell_length_c 5.6402
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
Na1 Na 0 0 0
Cl1 Cl 0.5 0.5 0.5
"""
    state = ProjectState()
    state.add_dataset(
        Dataset(
            "NaCl",
            ModuleType.XRD,
            {
                "x": np.array([27.37, 31.7]),
                "intensity": np.array([100.0, 20.0]),
            },
            source_filename="NaCl.cif",
            source_bytes=cif_text.encode(),
            metadata={
                "source_axis": "two_theta",
                "wavelength_a": 1.540598,
                "normalization": "none",
                "scale": 1.0,
                "offset": 0.0,
                "background_enabled": False,
                "is_simulated_phase": True,
                "edited_cif": cif_text,
                "stick_two_theta_deg": [27.37, 31.7],
                "stick_intensity": [100.0, 20.0],
                "stick_hkl_labels": ["(1 1 1)", "(2 0 0)"],
                "stick_d_spacing_a": [3.256, 2.82],
            },
        )
    )
    state.add_dataset(
        Dataset(
            "measured",
            ModuleType.XRD,
            {
                "x": np.array([10.0, 20.0]),
                "intensity": np.array([5.0, 8.0]),
            },
            metadata={
                "source_axis": "two_theta",
                "wavelength_a": 1.540598,
                "normalization": "none",
                "scale": 1.0,
                "offset": 0.0,
                "background_enabled": False,
            },
        )
    )
    widget = XrdWidget(state)
    qtbot.addWidget(widget)
    editor = CifEditorDialog(
        CifDocument(Path("NaCl.cif"), cif_text, cif_text),
        widget,
    )
    qtbot.addWidget(editor)
    assert editor.two_theta_min.value() == pytest.approx(1.0)
    assert len(widget.lattice_sliders) == 6
    assert not hasattr(widget, "apply_lattice_button")
    assert widget.lattice_fields["a"].isEnabled()
    assert not widget.lattice_fields["b"].isEnabled()
    profile_x_before = np.asarray(
        state.datasets[0].metadata["stick_two_theta_deg"], dtype=float
    )
    profile_y_before = np.asarray(state.datasets[0].metadata["stick_intensity"], dtype=float)
    maximum_before = profile_x_before[int(np.argmax(profile_y_before))]
    widget.lattice_sliders["a"].setValue(110000)
    assert widget.lattice_fields["a"].value() == pytest.approx(
        widget.lattice_fields["b"].value()
    )
    assert widget.lattice_fields["a"].value() == pytest.approx(
        widget.lattice_fields["c"].value()
    )
    profile_x_after = np.asarray(state.datasets[0].processed_data["x"], dtype=float)
    profile_y_after = np.asarray(
        state.datasets[0].processed_data["intensity"], dtype=float
    )
    maximum_after = profile_x_after[int(np.argmax(profile_y_after))]
    assert maximum_after < maximum_before
    assert widget._lattice_update_timer.isActive()
    assert widget._lattice_update_timer.interval() == widget._LIVE_CIF_SETTLE_MS
    widget._lattice_update_timer.stop()
    widget.show()
    qtbot.wait(20)
    peak_position = widget.plot_widget.getViewBox().mapViewToScene(
        QPointF(maximum_after, 100.0)
    )
    widget._show_cif_peak_hover(peak_position)
    assert widget._peak_label.isVisible()
    assert "(1 1 1)" in widget._peak_label.toPlainText()
    widget.lattice_relative_scale_slider.setValue(150000)
    assert widget.lattice_relative_scale.value() == pytest.approx(1.5)
    assert widget.scale.value() == pytest.approx(1.5)
    assert state.datasets[0].metadata["scale"] == pytest.approx(1.5)
    widget.lattice_fwhm_slider.setValue(200000)
    assert widget.lattice_fwhm.value() == pytest.approx(0.3)
    assert state.datasets[0].metadata["fwhm_deg"] == pytest.approx(0.3)
    assert len(state.datasets[0].processed_data["x"]) == 8901
    assert widget.occupancy_site.count() == 2
    assert widget.occupancy_site.currentText() == "Na1 (Na)"
    widget.occupancy_slider.setValue(7500)
    assert widget.occupancy_value.value() == pytest.approx(0.75)
    assert widget._lattice_update_timer.interval() == widget._LIVE_CIF_SETTLE_MS
    widget._lattice_update_timer.stop()

    scheduled: list[bool] = []
    monkeypatch.setattr(
        widget,
        "_schedule_cif_recalculation",
        lambda: scheduled.append(True),
    )
    widget.wavelength.setValue(0.71073)
    assert scheduled
    assert state.datasets[0].metadata["wavelength_a"] == pytest.approx(0.71073)
    recalculated: list[str] = []
    monkeypatch.setattr(
        widget,
        "_recalculate_cif_dataset",
        lambda dataset: recalculated.append(dataset.dataset_id),
    )
    widget.apply_wavelength_all.click()
    assert all(
        dataset.metadata["wavelength_a"] == pytest.approx(0.71073)
        for dataset in state.datasets
    )
    assert recalculated == [state.datasets[0].dataset_id]

    class CapturingPool:
        def __init__(self) -> None:
            self.started = []

        def start(self, worker) -> None:  # type: ignore[no-untyped-def]
            self.started.append(worker)

    pool = CapturingPool()
    monkeypatch.setattr(
        xrd_widget_module.QThreadPool,
        "globalInstance",
        staticmethod(lambda: pool),
    )
    monkeypatch.setattr(
        xrd_widget_module,
        "simulate_cif",
        lambda *_args, **_kwargs: SimulatedPattern(
            np.array([20.0]),
            np.array([100.0]),
            hkl_labels=["(1 0 0)"],
            d_spacing_a=np.array([2.0]),
        ),
    )
    for lattice_a in (6.0, 6.5, 7.0):
        widget.lattice_fields["a"].setValue(lattice_a)
        widget._lattice_update_timer.stop()
        widget._apply_lattice_parameters()
    assert len(pool.started) == 1
    assert widget._pending_live_cif is not None
    assert widget._pending_live_cif.generation == widget._lattice_generation
    pool.started[0].run()
    assert len(pool.started) == 2
    assert widget._pending_live_cif is None
    pool.started[1].run()
    assert widget._live_cif_worker is None
    final_document = CifDocument(
        Path("NaCl.cif"),
        cif_text,
        str(state.datasets[0].metadata["edited_cif"]),
    )
    final_cell, final_sites, _warnings = extract_cif_tables(final_document)
    assert final_cell.a == pytest.approx(7.0)
    assert final_sites[0].occupancy == pytest.approx(0.75)


def test_ppms_tabs_import_and_plot_both_file_types(qtbot, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from materials_data_studio.modules.ppms.widget import PpmsWidget

    heat_path = tmp_path / "heat.dat"
    heat_path.write_text(
        """[Header]
INFO,10,MASS:Sample Mass (mg)
INFO,100,MOLWGHT:Formula Weight (g/mole)
INFO,2,ATOMS:Atoms per Formula Unit
[Data]
Time Stamp (sec),Field (Oersted),Sample Temp (Kelvin),Samp HC (uJ/K),Samp HC Err (uJ/K)
1,0,2,10,1
2,0,3,20,1
3,0,4,30,1
"""
    )
    resistance_path = tmp_path / "resistance.dat"
    resistance_path.write_text(
        """[Header]
INFO,Sample A,Sample1 Name
INFO,Ohm,Sample1 Units
[Data]
Time Stamp (sec),Temperature (K),Magnetic Field (Oe),Bridge 1 Resistance (Ohms)
1,2,0,10
2,3,0,11
3,4,0,12
"""
    )
    generic_path = tmp_path / "generic.dat"
    generic_path.write_text(
        """[Header]
INFO,5.5,SAMPLE_WEIGHT
[Data]
Temperature (K),Custom Signal
1,4
2,7
3,10
"""
    )
    state = ProjectState()
    widget = PpmsWidget(state)
    qtbot.addWidget(widget)
    widget.import_files([heat_path, resistance_path, generic_path])
    assert [item.metadata["ppms_kind"] for item in state.datasets if not item.metadata.get("measurement_parent")] == [
        "heat_capacity",
        "resistivity",
        "generic",
    ]
    assert len(widget.heat_capacity.plot_widget.listDataItems()) == 1
    assert len(widget.resistivity.plot_widget.listDataItems()) == 1
    assert len(widget.generic.plot_widget.listDataItems()) == 1
    assert widget.heat_capacity.plot_tabs.count() == 2
    assert widget.resistivity.plot_tabs.count() == 2
    widget.tabs.setCurrentWidget(widget.generic)
    widget.import_dropped_files([heat_path])
    assert widget.tabs.currentWidget() is widget.generic
    assert state.datasets[-1].metadata["ppms_kind"] == "generic"
    assert widget.generic.variables_label.isReadOnly()
    assert "\n" in widget.generic.variables_label.toPlainText()
    widget.generic.variables_label.selectAll()
    assert widget.generic.variables_label.textCursor().hasSelection()
    widget.generic.marker_fill.setText("#abcdef")
    widget.generic.marker_fill.editingFinished.emit()
    assert widget.generic.dataset_list.current_dataset().plot_style.marker_fill_color == (
        "#abcdef"
    )
    assert hasattr(widget.heat_capacity, "marker_fill")
    assert hasattr(widget.resistivity, "marker_fill")
    widget.resistivity.plot_type.setCurrentIndex(
        widget.resistivity.plot_type.findData("rho_t")
    )
    widget.resistivity.display_mode.setCurrentIndex(
        widget.resistivity.display_mode.findData("arrhenius")
    )
    assert widget.resistivity.run_fit.isEnabled()
