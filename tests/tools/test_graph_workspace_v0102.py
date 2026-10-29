"""Toolbar, transfer, docking and log regressions; synthetic inputs only."""
from __future__ import annotations

import json

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox

from materials_data_studio.plotting.plot_spec import CurveSpec, PlotSpec
from materials_data_studio.tools.graph_document import load_graph_document
from materials_data_studio.tools.graph_window import GraphEditorWindow
from materials_data_studio.tools.graph_workflows import from_plot_spec


@pytest.fixture
def viewer(qtbot):
    window = GraphEditorWindow()
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    window._discard_approved = True
    qtbot.addWidget(window)
    window.bind_graph_document(from_plot_spec(PlotSpec("Temperature (K)", "Signal (emu)", [
        CurveSpec(np.arange(8.), np.arange(8.) ** 2, "Sample A · FC")])) )
    window.show()
    qtbot.wait(30)
    yield window
    window.close()


@pytest.mark.parametrize("dock_name", ["console_dock", "procedure_dock"])
@pytest.mark.parametrize("state", [Qt.WindowState.WindowMinimized, Qt.WindowState.WindowMaximized])
def test_dock_back_clears_native_state_and_preserves_content(viewer, qtbot, dock_name, state):
    workspace = viewer.command_workspace
    dock = getattr(workspace, dock_name)
    panel, code = dock.widget(), workspace.code
    code.setPlainText("# retained across native docking\nprint(1)")
    for _ in range(2):
        workspace.float_dock(dock)
        dock.move(-12000, 7000)
        dock.setWindowState(state)
        workspace.dock_back(dock)
        qtbot.wait(120)
        assert dock.windowState() == Qt.WindowState.WindowNoState
        assert dock.parentWidget() is viewer and not dock.isWindow() and not dock.isFloating()
        assert dock.isVisible() and not dock.visibleRegion().isEmpty()
        assert panel is dock.widget() and not panel.visibleRegion().isEmpty()
        assert viewer.rect().contains(dock.geometry())
        assert workspace.code is code and "retained" in code.toPlainText()


def test_dock_callbacks_do_not_revive_hidden_or_refloated_panel(viewer, qtbot):
    workspace = viewer.command_workspace
    dock = workspace.procedure_dock
    workspace.float_dock(dock)
    workspace.dock_back(dock)
    dock.hide()  # Before queued work has been delivered.
    qtbot.wait(160)
    assert dock.isHidden()
    workspace.float_dock(dock)
    workspace.dock_back(dock)
    workspace.float_dock(dock)
    qtbot.wait(160)
    assert dock.isFloating() and dock.isVisible()


def test_tabified_dock_returns_with_visible_content(viewer, qtbot):
    workspace = viewer.command_workspace
    viewer.tabifyDockWidget(workspace.console_dock, workspace.procedure_dock)
    workspace.procedure_dock.show()
    for dock in (workspace.console_dock, workspace.procedure_dock):
        workspace.float_dock(dock)
        workspace.dock_back(dock)
        qtbot.wait(120)
        assert not dock.widget().visibleRegion().isEmpty()
        assert viewer.rect().contains(dock.geometry())


def test_resize_gives_extra_width_to_canvas_without_right_gap(viewer, qtbot):
    viewer.command_workspace.console_dock.hide()
    viewer.resize(1200, 800)
    qtbot.wait(50)
    before = viewer.splitter.sizes()
    viewer.resize(2400, 1000)
    qtbot.wait(50)
    after = viewer.splitter.sizes()
    assert after[1] > before[1] + 900
    # Qt may first restore a non-stretch pane's preferred width after it was
    # squeezed at 1200 px (style/font dependent); it must not scale with canvas.
    assert after[0] <= max(before[0], 270) + 10
    assert after[2] <= max(before[2], 340) + 10
    assert viewer.options.geometry().right() == viewer.splitter.rect().right()
    # A wide user-resized Properties pane must not leave unused splitter space.
    viewer.splitter.setSizes([200, 1000, 900])
    qtbot.wait(30)
    assert viewer.options.width() > 480
    assert viewer.options.geometry().right() == viewer.splitter.rect().right()


def test_procedure_log_coalesces_only_consecutive_edits(viewer):
    workspace = viewer.command_workspace
    workspace.output.clear()
    for n in range(4):
        workspace.code.setPlainText(f"print({n})")
        workspace.flush()
    text = workspace.output.toPlainText()
    assert text.count("GUI Edit Python procedure") == 1
    assert "4 consecutive updates" in text
    assert viewer.editor.history.count() == 4  # Do not coalesce scientific undo/audit history.
    workspace.execute("Print 42")
    workspace.code.setPlainText("print(5)")
    workspace.flush()
    assert workspace.output.toPlainText().count("GUI Edit Python procedure") == 2
    assert "> Print 42" in workspace.output.toPlainText()
    viewer.editor.history.undo()
    assert viewer.editor.document.current.environment["python_source"] == "print(3)"
    workspace.output.clear()
    workspace.code.setPlainText("print(6)")
    workspace.flush()
    assert workspace.output.toPlainText().strip() == "// GUI Edit Python procedure"


def test_graph_toolbar_open_save_and_import_use_existing_workflows(viewer, monkeypatch, tmp_path):
    def warning(_parent, title, message):
        raise AssertionError(f"{title}: {message}")
    monkeypatch.setattr(QMessageBox, "warning", warning)
    actions = [a for a in viewer.quick_toolbar.actions() if not a.isSeparator()]
    assert actions[:3] == [viewer.open_action, viewer.save_action, viewer.import_action]
    assert all(not a.icon().isNull() for a in actions)
    opened, imported = [], []
    path = tmp_path / "state.mdsg"
    monkeypatch.setattr(viewer, "open_path", opened.append)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(path), ""))
    viewer.open_action.trigger()
    assert opened == [path]
    import materials_data_studio.tools.graph_window as module
    monkeypatch.setattr(module, "import_text", lambda target: imported.append(target))
    viewer.import_action.trigger()
    assert imported == [viewer]
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(path), "MDS Graph Document (*.mdsg)"))
    viewer.save_action.trigger()
    assert path.is_file()
    assert load_graph_document(path).current.graphs == viewer.editor.document.current.graphs
    viewer.bind_graph_document(from_plot_spec(PlotSpec("X", "Y", [CurveSpec(np.arange(2.), np.arange(2.), "New")])) )
    assert [a for a in viewer.quick_toolbar.actions() if not a.isSeparator()][:3] == actions[:3]


def test_transfer_wave_names_are_readable_unique_and_values_preserved():
    x, y = np.arange(4.), np.arange(4.) + 3
    labels = ["Sample A · FC", "Sample A / FC", "日本語 試料", "Long" * 70, "Long" * 70, "123"]
    doc = from_plot_spec(PlotSpec("Temperature (K)", "M (emu/mol)", [CurveSpec(x, y, label) for label in labels]))
    waves = doc.render_source().waves
    assert waves[0].name == "Sample_A_FC_Temperature_K_x"
    assert waves[1].name == "Sample_A_FC_M_emu_mol_y"
    assert len({w.name.casefold() for w in waves}) == 12
    assert all(len(w.name) <= 63 and ":" not in w.name for w in waves)
    assert any("日本語_試料" in w.name for w in waves)
    for index, trace in enumerate(doc.graph("MDS_Plot").traces):
        for ref, values in ((trace.x_wave, x), (trace.y_wave, y)):
            wave = doc.render_source().wave(ref)
            assert wave is not None
            np.testing.assert_array_equal(wave.data, values)
            assert json.loads(wave.wave_note)["source_curve"] == labels[index]


def test_main_toolbar_transfers_current_plot(qtbot, monkeypatch):
    from materials_data_studio.ui.main_window import MainWindow
    window = MainWindow()
    qtbot.addWidget(window)
    action = window.edit_graph_action
    assert action in window.main_toolbar.actions() and action in window.file_menu.actions()
    assert not action.icon().isNull()
    class Plot:
        _materials_plot_spec = PlotSpec("T (K)", "Signal", [CurveSpec(np.arange(3.), np.arange(3.), "Demo")])
    monkeypatch.setattr(window, "_active_interactive_plot", lambda: (None, None, Plot()))
    captured = []
    monkeypatch.setattr(window, "open_native_graph", captured.append)
    action.trigger()
    assert captured[0].render_source().waves[0].name == "Demo_T_K_x"
    window.close()
