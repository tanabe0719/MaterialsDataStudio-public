"""Panel state, action scope and bilingual interaction regressions."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from materials_data_studio.core.settings import create_settings
from materials_data_studio.ui.action_layout import ActionBar, DiagnosticLabel
from materials_data_studio.ui.localization import JA, set_language, translate_tree
from materials_data_studio.ui.panels import CardCollapse, install_settings_panel
from materials_data_studio.ui.settings_actions import ExecutionFooter
from materials_data_studio.ui.workspace_layout import (
    WorkspaceSplitter,
    install_workspace,
    set_residual_available,
    with_residual_controls,
)


def test_fold_preserves_conditionally_hidden_rows_and_keyboard_state(qtbot: Any):
    card = QGroupBox("Analysis")
    card.setProperty("settingsKey", "fold-test")
    card.setProperty("sectionSource", "Analysis")
    form = QFormLayout(card)
    kept, conditional = QPushButton("Run Fit"), QPushButton("Internal")
    form.addRow(kept)
    form.addRow(conditional)
    form.setRowVisible(conditional, False)
    control = CardCollapse(card)
    qtbot.addWidget(card)
    card.show()
    qtbot.wait(10)
    assert kept.isVisible() and conditional.isHidden()
    control.toggle.setFocus()
    qtbot.keyClick(control.toggle, Qt.Key.Key_Space)
    assert not kept.isVisible() and control.closed
    qtbot.keyClick(control.toggle, Qt.Key.Key_Space)
    assert kept.isVisible() and conditional.isHidden()
    control.toggle.click()
    another = QGroupBox("Analysis")
    qtbot.addWidget(another)
    another.setProperty("settingsKey", "fold-test")
    another.setProperty("sectionSource", "Analysis")
    QVBoxLayout(another).addWidget(QLabel("Preserved"))
    assert CardCollapse(another).closed
    translate_tree(card, "ja")
    assert control.toggle.accessibleName() == JA["Expand or collapse section"]


def test_settings_tabs_stay_fixed_and_export_hidden_rows_stay_hidden(qtbot: Any):
    from PySide6.QtWidgets import QSpinBox
    root = QWidget()
    QVBoxLayout(root)
    export = QGroupBox("Figure Export", root)
    form = QFormLayout(export)
    old = QSpinBox()
    form.addRow("Old width", old)
    scroll = QScrollArea()
    qtbot.addWidget(scroll)
    install_settings_panel(scroll, root, "test")
    scroll.resize(340, 250)
    scroll.show()
    tabs = scroll.findChild(QTabWidget, "SettingsNavigation")
    assert tabs is not None
    tabs.setCurrentIndex(3)
    qtbot.wait(10)
    assert scroll.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert scroll.findChild(QLabel, "SettingsTarget").isVisible()
    assert tabs.tabBar().isVisible()
    control = export.property("sectionController")
    control.toggle.click()
    control.toggle.click()
    assert old.isHidden()
    assert len(tabs.currentWidget().findChildren(QScrollArea)) == 1


def test_center_absorbs_resize_and_focus_is_reversible(qtbot: Any):
    window = QWidget()
    qtbot.addWidget(window)
    layout = QVBoxLayout(window)
    splitter = WorkspaceSplitter(Qt.Orientation.Horizontal)
    for _ in range(3):
        panel = QWidget()
        panel.setMinimumSize(50, 50)
        splitter.addWidget(panel)
    splitter.setSizes([270, 500, 340])
    install_workspace(layout, splitter, "test")
    window.resize(1200, 600)
    window.show()
    qtbot.wait(10)
    first = splitter.sizes()
    window.resize(1500, 600)
    qtbot.wait(10)
    second = splitter.sizes()
    assert abs(second[0]-first[0]) < 3 and abs(second[2]-first[2]) < 3
    assert second[1] > first[1] + 250
    next(a for a in splitter.view_actions if a.text() == "Focus plot").trigger()
    assert splitter.widget(0).isHidden() and splitter.widget(2).isHidden()
    assert not splitter.widget(1).isHidden()
    for button in splitter.panel_actions:
        button.trigger()
    qtbot.wait(10)
    assert all(not splitter.widget(i).isHidden() for i in range(3))
    qtbot.mouseDClick(splitter.handle(1), Qt.MouseButton.LeftButton)
    assert splitter.sizes()[0] > 100 and splitter.sizes()[2] > 100


def test_sticky_execution_uses_focused_section_and_respects_disabled_parent(qtbot: Any):
    host = QWidget()
    qtbot.addWidget(host)
    layout = QVBoxLayout(host)
    cards, calls = [], []
    for title in ("Curie-Weiss Fit", "Other Fit"):
        card = QGroupBox(title)
        row = QVBoxLayout(card)
        button = QPushButton("Run Fit")
        button.clicked.connect(lambda _checked=False, name=title: calls.append(name))
        row.addWidget(button)
        result = QPlainTextEdit()
        result.setReadOnly(True)
        row.addWidget(result)
        cards.append(card)
        layout.addWidget(card)
    footer = ExecutionFooter(cards, host)
    layout.addWidget(footer)
    host.show()
    cards[1].findChild(QPushButton).setFocus()
    qtbot.wait(10)
    assert calls == []
    footer.execute.click()
    assert calls == ["Other Fit"]
    cards[1].findChild(QPlainTextEdit).setPlainText("Asynchronous result: 42")
    assert "42" in footer.result_text
    cards[1].setEnabled(False)
    qtbot.wait(10)
    assert not footer.execute.isEnabled()
    footer.run()
    assert calls == ["Other Fit"]
    set_language("ja")
    assert footer.execute.text() == JA["Run Fit"]
    assert footer.context.text().startswith(JA["Active analysis"])
    set_language("en")


def test_residual_hidden_by_user_stays_hidden_after_refresh(qtbot: Any):
    from types import SimpleNamespace

    from PySide6.QtWidgets import QSplitter
    residual = QWidget()
    residual._materials_plot_spec = SimpleNamespace(curves=[1])
    splitter = QSplitter(Qt.Orientation.Vertical)
    splitter.addWidget(QWidget())
    splitter.addWidget(residual)
    host = with_residual_controls(splitter, residual, "test")
    qtbot.addWidget(host)
    host.resize(500, 600)
    host.show()
    toggle = residual.property("mdsResidualToggle")
    toggle.trigger()
    set_residual_available(residual, True)
    assert residual.isHidden()
    toggle.trigger()
    assert not residual.isHidden()
    set_residual_available(residual, False)
    assert residual.isHidden() and not toggle.isEnabled()


def test_diagnostics_translate_hints_but_preserve_dataset_names(qtbot: Any):
    label = DiagnosticLabel("Import or drop measurement files to begin.")
    qtbot.addWidget(label)
    translate_tree(label, "ja")
    assert label.full_text == JA["Import or drop measurement files to begin."]
    label.setText("Data")  # A user's filename, not a UI label.
    translate_tree(label, "ja")
    assert label.full_text == "Data"
    translate_tree(label, "en")
    assert label.full_text == "Data"


def test_main_window_pinned_actions_remain_inside_small_viewport(qtbot: Any):
    from materials_data_studio.ui.main_window import MainWindow
    window = MainWindow()
    qtbot.addWidget(window)
    window.resize(940, 490)
    window.show()
    mpms = window._ensure_module_widget(1)
    window.module_tabs.setCurrentIndex(1)
    tabs = mpms.findChild(QTabWidget, "SettingsNavigation")
    tabs.setCurrentIndex(2)
    qtbot.wait(30)
    footer = mpms.findChild(ExecutionFooter)
    viewport = mpms.findChild(QScrollArea, "SettingsPanel").viewport()
    assert footer is not None and footer.isVisible()
    assert viewport.rect().contains(footer.mapTo(viewport, footer.rect().bottomRight()))
    assert viewport.rect().contains(footer.mapTo(viewport, footer.rect().topLeft()))
    window.undo_history.reset(clean=True)


def test_action_groups_wrap_without_changing_order_or_callbacks(qtbot: Any):
    first, second, remove = [QPushButton(text) for text in ("Edit selected column", "Duplicate", "Delete")]
    calls = []
    for button in (first, second, remove):
        name = button.text()
        button.clicked.connect(lambda _checked=False, name=name: calls.append(name))
    bar = ActionBar([[first, second], [remove]])
    qtbot.addWidget(bar)
    bar.resize(180, 200)
    bar.show()
    qtbot.wait(10)
    assert bar.width() == 180
    assert first.parent() is second.parent() and remove.parent() is not first.parent()
    first.click()
    remove.click()
    assert calls == ["Edit selected column", "Delete"]
    assert first.geometry().bottom() < second.geometry().top()


def test_graph_focus_preserves_procedure_and_restores_docks(qtbot: Any):
    from materials_data_studio.tools.graph_window import GraphEditorWindow
    graph = GraphEditorWindow()
    graph.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    qtbot.addWidget(graph)
    graph._discard_approved = True
    graph.show()
    workspace = graph.command_workspace
    workspace.procedure_dock.show()
    workspace.code.setPlainText("# synthetic unexecuted procedure\nvalue = 42")
    qtbot.wait(20)
    next(a for a in graph.view_menu.actions() if a.text() == "Focus graph").trigger()
    assert workspace.console_dock.isHidden() and workspace.procedure_dock.isHidden()
    next(a for a in graph.view_menu.actions() if a.text() == "Restore panels").trigger()
    qtbot.wait(20)
    assert not workspace.console_dock.isHidden() and not workspace.procedure_dock.isHidden()
    assert workspace.code.toPlainText().endswith("value = 42")
    workspace.procedure_dock.hide()
    graph.close()
    assert create_settings().value("ui/graphDockState") is not None
    reopened = GraphEditorWindow()
    qtbot.addWidget(reopened)
    reopened._discard_approved = True
    assert reopened.command_workspace.procedure_dock.isHidden()


def test_diagnostic_details_keep_primary_message_and_extended_tooltip(qtbot: Any, monkeypatch: Any):
    from materials_data_studio.ui.action_layout import diagnostic_row
    host = QWidget()
    qtbot.addWidget(host)
    label = DiagnosticLabel('Full primary diagnostic that is longer than the visible label')
    row = diagnostic_row(label, host)
    QVBoxLayout(host).addWidget(row)
    label.setToolTip('synthetic.dat\nAdditional parser warning')
    captured = []
    monkeypatch.setattr('materials_data_studio.ui.action_layout.show_details', lambda _owner, text: captured.append(text))
    row.findChild(QPushButton).click()
    assert label.full_text in captured[0]
    assert 'synthetic.dat' in captured[0] and 'Additional parser warning' in captured[0]


def test_view_menu_routes_to_active_tab_and_localizes_without_header_buttons(qtbot: Any):
    from shiboken6 import isValid

    from materials_data_studio.ui.main_window import MainWindow
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    titles = [action.text() for action in window.menuBar().actions()]
    assert titles[-2:] == ['View', 'Help']
    forbidden = {'Datasets panel','Settings panel','Focus plot','Focus graph','Reset layout','Restore panels','Residual panel','Restore plot height','Show Command Window','Show Procedure Window'}
    assert not any(button.text() in forbidden for button in window.findChildren(QPushButton))
    xrd = window.active_widget()
    window.refresh_view_menu()
    action = window.view_menu.actions()[0]
    assert action.isCheckable() and action.isChecked()
    action.trigger()
    assert xrd.splitter.widget(0).isHidden()
    window.module_tabs.setCurrentIndex(1)
    window.refresh_view_menu()
    assert isValid(action) and window.view_menu.actions()[0] is not action
    assert window.view_menu.actions()[0].isChecked()
    window.module_tabs.setCurrentIndex(2)
    ppms = window.active_widget()
    window.refresh_view_menu()
    assert any(a.text() == 'Residual panel' for a in window.view_menu.actions())
    ppms.tabs.setCurrentIndex(2)
    window.refresh_view_menu()
    assert not any(a.text() == 'Residual panel' for a in window.view_menu.actions())
    window.module_tabs.setCurrentIndex(0)
    window.refresh_view_menu()
    assert window.view_menu.actions()[0] is action and not action.isChecked()
    set_language('ja')
    window.refresh_view_menu()
    assert window.view_menu.title() == '表示'
    assert window.view_menu.actions()[0].text() == 'データセット欄'
    set_language('en')
    window.refresh_view_menu()
    assert action.text() == 'Datasets panel'
    window.undo_history.reset(clean=True)


def test_graph_has_single_view_before_help_with_native_dock_actions(qtbot: Any):
    from materials_data_studio.tools.graph_window import GraphEditorWindow
    graph = GraphEditorWindow()
    qtbot.addWidget(graph)
    graph._discard_approved = True
    titles = [action.text() for action in graph.menuBar().actions()]
    assert titles.count('View') == 1 and titles[-2:] == ['View','Help']
    assert not any(b.text() in {'Datasets panel','Settings panel','Focus graph','Reset layout','Restore panels','Show Command Window','Show Procedure Window'} for b in graph.findChildren(QPushButton))
    assert graph.command_workspace.console_dock.toggleViewAction() in graph.view_menu.actions()
    assert graph.command_workspace.procedure_dock.toggleViewAction() in graph.view_menu.actions()
    set_language('ja')
    assert graph.view_menu.title() == '表示'
    assert graph.command_workspace.procedure_dock.toggleViewAction().text() == 'プロシージャウインドウ'
    set_language('en')
