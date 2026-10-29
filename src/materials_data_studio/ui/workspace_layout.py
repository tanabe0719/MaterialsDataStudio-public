"""Central-plot-first columns with explicit, reversible panel controls."""
from __future__ import annotations

from contextlib import suppress

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QAction, QColor, QPainter
from PySide6.QtWidgets import (
    QMainWindow,
    QMenu,
    QSplitter,
    QSplitterHandle,
    QVBoxLayout,
    QWidget,
)

from materials_data_studio.core.settings import create_settings
from materials_data_studio.ui.localization import tr


class ResizeHandle(QSplitterHandle):
    def __init__(self, orientation, parent):
        super().__init__(orientation, parent)
        self.setMouseTracking(True)
        self.setToolTip("Drag to resize; double-click to reset column widths")

    def paintEvent(self, event):
        painter = QPainter(self)
        color = self.palette().highlight().color() if self.underMouse() else self.palette().mid().color()
        painter.setPen(QColor(color))
        if self.orientation() == Qt.Orientation.Horizontal:
            painter.drawLine(self.width()//2, 2, self.width()//2, self.height()-2)
        else:
            painter.drawLine(2, self.height()//2, self.width()-2, self.height()//2)

    def mouseDoubleClickEvent(self, event):
        parent = self.splitter()
        if isinstance(parent, WorkspaceSplitter):
            parent.reset_columns()
        event.accept()

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)


class WorkspaceSplitter(QSplitter):
    panel_actions: list[QAction]
    view_actions: list[QAction]

    def createHandle(self):
        return ResizeHandle(self.orientation(), self)

    def reset_columns(self):
        if self.count() != 3:
            return
        for index in (0, 2):
            widget = self.widget(index)
            if widget is not None:
                widget.show()
        for button in getattr(self, "panel_actions", []):
            button.setChecked(True)
        self.setSizes([270, max(200, self.width()-630), 340])
        self._remember()

    def _remember(self, *_args):
        key = self.property("workspaceKey")
        if key and self.count() == 3:
            settings = create_settings()
            # Keep usable expanded widths when temporarily focusing the plot.
            if all((widget := self.widget(i)) is not None and not widget.isHidden() for i in (0, 2)):
                settings.setValue(f"ui/columns/{key}", self.sizes())


def install_workspace(layout: QVBoxLayout, splitter: WorkspaceSplitter, key: str) -> None:
    splitter.setProperty("workspaceKey", key)
    splitter.setHandleWidth(7)
    splitter.setChildrenCollapsible(False)
    for index in range(3):
        splitter.setStretchFactor(index, 1 if index == 1 else 0)
    saved = create_settings().value(f"ui/columns/{key}")
    sizes = [270, 850, 340]
    if isinstance(saved, list) and len(saved) == 3:
        with suppress(ValueError, TypeError):
            sizes = [max(0, int(value)) for value in saved]
    splitter.setSizes(sizes)
    splitter.splitterMoved.connect(splitter._remember)
    splitter.panel_actions = []
    splitter.view_actions = []
    for title, index in (("Datasets panel", 0), ("Settings panel", 2)):
        button = QAction(title, splitter)
        button.setCheckable(True)
        button.setChecked(True)
        def toggle(shown, number=index):
            if not shown:
                splitter._remember()
            widget = splitter.widget(number)
            if widget is not None:
                widget.setVisible(shown)
            create_settings().setValue(f"ui/panels/{key}/{number}", shown)
            if shown:
                values = create_settings().value(f"ui/columns/{key}")
                if isinstance(values, list) and len(values) == 3:
                    splitter.setSizes([int(v) for v in values])
        button.toggled.connect(toggle)
        splitter.view_actions.append(button)
        splitter.panel_actions.append(button)
    focus = QAction("Focus graph" if key == "graph_editor" else "Focus plot", splitter)
    def focus_plot():
        splitter._remember()
        for button in splitter.panel_actions:
            button.setChecked(False)
        workspace = getattr(splitter.window(), "command_workspace", None)
        if workspace is not None:
            window = splitter.window()
            if isinstance(window, QMainWindow) and window.property("mdsFocusedDockState") is None:
                window.setProperty("mdsFocusedDockState", window.saveState())
            for name in ("console_dock", "procedure_dock"):
                dock = getattr(workspace, name)
                create_settings().setValue(f"ui/dockHeight/{name}", dock.height())
                dock.setProperty("mdsFocusWasVisible", dock.isVisible())
                dock.hide()
    focus.triggered.connect(focus_plot)
    splitter.view_actions.append(focus)
    reset = QAction("Reset layout", splitter)
    reset.triggered.connect(splitter.reset_columns)
    splitter.view_actions.append(reset)
    if key == "graph_editor":
        restore = QAction("Restore panels", splitter)
        def restore_panels():
            window = splitter.window()
            state = window.property("mdsFocusedDockState")
            if isinstance(window, QMainWindow) and isinstance(state, QByteArray):
                window.restoreState(state)
                window.setProperty("mdsFocusedDockState", None)
            for button in splitter.panel_actions:
                button.setChecked(True)
        restore.triggered.connect(restore_panels)
        splitter.view_actions.append(restore)
    layout.addWidget(splitter, 1)
    for index, button in zip((0, 2), splitter.panel_actions, strict=True):
        shown = str(create_settings().value(f"ui/panels/{key}/{index}", "true")).lower() != "false"
        button.setChecked(shown)
    for index in (0, 1):
        widget = splitter.widget(index)
        if widget is not None:
            widget.setProperty("panelFrame", True)


def with_residual_controls(splitter: QSplitter, residual: QWidget, key: str) -> QWidget:
    """Keep residual visibility independent of curve refresh and remember height."""
    host = QWidget()
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    toggle = QAction("Residual panel", host)
    toggle.setCheckable(True)
    toggle.setChecked(str(create_settings().value(f"ui/residualVisible/{key}", "true")).lower() != "false")
    residual.setProperty("mdsPanelHidden", not toggle.isChecked())
    residual.setProperty("mdsResidualToggle", toggle)
    residual.setProperty("mdsResidualSplitter", splitter)
    residual.setProperty("mdsResidualKey", key)
    def update(shown):
        create_settings().setValue(f"ui/residualVisible/{key}", shown)
        if not shown and residual.isVisible():
            create_settings().setValue(f"ui/residualSizes/{key}", splitter.sizes())
        residual.setProperty("mdsPanelHidden", not shown)
        spec = getattr(residual, "_materials_plot_spec", None)
        residual.setVisible(shown and bool(getattr(spec, "curves", [])))
        if shown:
            sizes = create_settings().value(f"ui/residualSizes/{key}", [620, 180])
            if isinstance(sizes, list) and len(sizes) == 2:
                splitter.setSizes([int(v) for v in sizes])
    toggle.toggled.connect(update)
    reset = QAction("Restore plot height", host)
    reset.triggered.connect(lambda: splitter.setSizes([620, 180]))
    host.setProperty("viewActions", [toggle, reset])
    layout.addWidget(splitter, 1)
    def remember(_position, _index):
        if residual.isVisible():
            create_settings().setValue(f"ui/residualSizes/{key}", splitter.sizes())
    splitter.splitterMoved.connect(remember)
    return host


def set_residual_available(residual: QWidget, available: bool) -> None:
    first_available = available and not bool(residual.property("mdsResidualAvailable"))
    residual.setProperty("mdsResidualAvailable", available)
    residual.setVisible(available and not bool(residual.property("mdsPanelHidden")))
    if first_available and not residual.isHidden():
        splitter = residual.property("mdsResidualSplitter")
        key = residual.property("mdsResidualKey")
        sizes = create_settings().value(f"ui/residualSizes/{key}", [620, 180])
        if isinstance(splitter, QSplitter) and isinstance(sizes, list) and len(sizes) == 2:
            splitter.setSizes([int(value) for value in sizes])
    toggle = residual.property("mdsResidualToggle")
    if isinstance(toggle, QAction):
        toggle.setEnabled(available)


def restore_graph_panels(window: QMainWindow) -> None:
    saved = create_settings().value("ui/graphDockState")
    if isinstance(saved, QByteArray):
        window.restoreState(saved)


def save_graph_panels(window: QMainWindow) -> None:
    create_settings().setValue("ui/graphDockState", window.saveState())
    workspace = getattr(window, "command_workspace", None)
    if workspace is not None:
        for name in ("console_dock", "procedure_dock"):
            dock = getattr(workspace, name)
            if dock.isVisible() and not dock.isFloating():
                horizontal = window.dockWidgetArea(dock) in {Qt.DockWidgetArea.LeftDockWidgetArea, Qt.DockWidgetArea.RightDockWidgetArea}
                create_settings().setValue(f"ui/dockExtent/{name}", dock.width() if horizontal else dock.height())


def populate_view_menu(menu: QMenu, splitter: WorkspaceSplitter, *, prepend: bool = False) -> None:
    """Use the active workspace's own actions so checks never affect other tabs."""
    actions = list(splitter.view_actions)
    center = splitter.widget(1)
    residual = center.property("viewActions") if center is not None else None
    if isinstance(residual, list):
        actions.extend(action for action in residual if isinstance(action, QAction))
    anchor = menu.actions()[0] if prepend and menu.actions() else None
    if not prepend:
        menu.clear()
    for action in actions:
        source = str(action.property("mdsCaptionSource") or action.text())
        rendered = tr(source)
        action.setProperty("mdsCaptionSource", source)
        action.setProperty("mdsCaptionRendered", rendered)
        action.setText(rendered)
        menu.insertAction(anchor, action) if anchor is not None else menu.addAction(action)
    if anchor is not None:
        menu.insertSeparator(anchor)
