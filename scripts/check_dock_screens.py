"""Exercise only our generated editor windows on available native Qt screens."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtWidgets import QApplication, QToolButton

from materials_data_studio.tools.graph_window import GraphEditorWindow


def settle(milliseconds=150):
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


def main():
    with tempfile.TemporaryDirectory(prefix="mds-native-dock-test-") as work:
        os.environ["MDS_SETTINGS_DIRECTORY"] = work
        os.environ["MDS_RECOVERY_DIRECTORY"] = str(Path(work)/"recovery")
        app = QApplication.instance() or QApplication([])
        window = GraphEditorWindow()
        window._discard_approved = True
        window.resize(1200, 800)
        window.show()
        app.processEvents()
        workspace = window.command_workspace
        workspace.code.setPlainText("# Generated docking stress test\nprint(1)")
        editor = workspace.code
        records = []
        for screen in app.screens():
            bounds = screen.availableGeometry()
            for tabified in (False, True):
                if tabified:
                    window.tabifyDockWidget(workspace.console_dock, workspace.procedure_dock)
                for dock in (workspace.console_dock, workspace.procedure_dock):
                    panel = dock.widget()
                    for state in (Qt.WindowState.WindowNoState, Qt.WindowState.WindowMinimized, Qt.WindowState.WindowMaximized):
                        for _repeat in range(2):
                            workspace.float_dock(dock)
                            dock.resize(min(700, bounds.width()), min(500, bounds.height()))
                            dock.move(bounds.x()+30, bounds.y()+30)
                            dock.setWindowState(state)
                            settle()
                            floating_ratio = dock.devicePixelRatioF()
                            toggle = next(button for button in dock.titleBarWidget().findChildren(QToolButton) if button.property("mdsDockToggle"))
                            toggle.click()  # The actual title-bar action, not a different test path.
                            settle(200)
                            assert dock.parentWidget() is window and not dock.isFloating() and not dock.isWindow() and dock.isVisible()
                            assert dock.windowState() == Qt.WindowState.WindowNoState
                            assert window.rect().contains(dock.geometry())
                            assert panel is dock.widget() and not panel.visibleRegion().isEmpty()
                            assert workspace.code is editor and "Generated docking" in editor.toPlainText()
                            records.append({"screen_geometry": [bounds.x(), bounds.y(), bounds.width(), bounds.height()], "screen_scale": screen.devicePixelRatio(), "floating_scale": floating_ratio, "dock": dock.objectName(), "tabified": tabified, "previous_state": state.value, "returned_visible": True})
        # A later hide or separate action must win over pending Dock back work.
        workspace.dock_back(workspace.procedure_dock)
        workspace.procedure_dock.hide()
        settle(200)
        assert workspace.procedure_dock.isHidden()
        window.close()
        print(json.dumps({"platform": app.platformName(), "screen_count": len(app.screens()), "checks": records}, indent=2))


if __name__ == "__main__":
    main()
