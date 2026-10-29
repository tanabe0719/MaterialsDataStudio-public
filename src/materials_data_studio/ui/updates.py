"""Shared, nonblocking update UI for the main window and Graph Editor."""
from __future__ import annotations

import json
import sys
from typing import Any

from PySide6.QtCore import QObject, QProcess, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from materials_data_studio.core.settings import create_settings
from materials_data_studio.integrations import updates
from materials_data_studio.ui.localization import StatusLabel, tr, translate_tree


def startup_enabled() -> bool:
    return bool(create_settings().value(updates.STARTUP_SETTING, True, type=bool))


def set_startup_enabled(enabled: bool) -> None:
    create_settings().setValue(updates.STARTUP_SETTING, enabled)
    current = getattr(QApplication.instance(), "_mds_update_controller", None)
    if current is not None and current.dialog is not None and isValid(current.dialog):
        current.dialog.sync_startup()


class NotesBrowser(QTextBrowser):
    def loadResource(self, _kind: int, _name: QUrl | str) -> Any:
        # Release Markdown must not load images/local files as side effects.
        return None


class UpdateDialog(QDialog):
    def __init__(self, controller: UpdateController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("Software updates")
        self.resize(750, 580)
        layout = QVBoxLayout(self)
        self.status = StatusLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.version_label = QLabel()
        self.version_label.setProperty("mdsLiteralData", True)
        layout.addWidget(self.version_label)
        self.notes = NotesBrowser()
        self.notes.setOpenLinks(False)
        self.notes.anchorClicked.connect(self.open_link)
        layout.addWidget(self.notes, 1)
        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)
        self.disable_startup = QCheckBox("Do not check for updates at startup")
        self.disable_startup.setChecked(not startup_enabled())
        self.disable_startup.toggled.connect(lambda value: set_startup_enabled(not value))
        layout.addWidget(self.disable_startup)
        row = QHBoxLayout()
        self.release_notes = QPushButton("Release notes on GitHub")
        self.release_notes.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(controller.release_url())))
        row.addWidget(self.release_notes)
        self.recheck = QPushButton("Check again")
        self.recheck.clicked.connect(lambda: controller.check(force=True))
        self.recheck.hide()
        row.addWidget(self.recheck)
        row.addStretch(1)
        self.action = QPushButton("Check again")
        self.action.setProperty("primary", True)
        self.action.clicked.connect(controller.activate)
        row.addWidget(self.action)
        self.close_button = QPushButton("Later")
        self.close_button.setDefault(True)
        self.close_button.clicked.connect(self.reject)
        row.addWidget(self.close_button)
        layout.addLayout(row)

    def sync_startup(self) -> None:
        previous = self.disable_startup.blockSignals(True)
        self.disable_startup.setChecked(not startup_enabled())
        self.disable_startup.blockSignals(previous)

    @staticmethod
    def open_link(url: QUrl) -> None:
        if url.scheme() == "https" and not url.userInfo():
            QDesktopServices.openUrl(url)

    def reject(self) -> None:
        if self.controller.process is not None:
            self.controller.cancel()
        super().reject()

    def closeEvent(self, event: Any) -> None:
        self.reject()
        event.accept()


class UpdateController(QObject):
    def __init__(self, application: QApplication) -> None:
        super().__init__(application)
        self.process: QProcess | None = None
        self.dialog: UpdateDialog | None = None
        self.owner: QWidget | None = None
        self.plan: dict[str, Any] | None = None
        self.ready = False
        self.automatic = False
        self.operation = ""
        self.buffer = bytearray()
        self.received: dict[str, Any] | None = None
        self.protocol_error = False
        self.manual_used = False
        application.aboutToQuit.connect(self.cancel)

    def release_url(self) -> str:
        return updates.REPOSITORY + "/releases/tag/v" + self.plan["version"] if self.plan else updates.RELEASES

    def show(self, parent: QWidget | None = None) -> UpdateDialog:
        self.remember_owner(parent)
        if self.dialog is not None and not isValid(self.dialog):
            self.dialog = None
        if self.dialog is None:
            dialog = UpdateDialog(self, self.owner)
            self.dialog = dialog
            dialog.destroyed.connect(lambda: self.dialog_destroyed(dialog))
        self.dialog.sync_startup()
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
        return self.dialog

    def remember_owner(self, parent: QWidget | None) -> None:
        if self.owner is not None and isValid(self.owner):
            return
        self.owner = None
        if parent is None:
            return
        owners = [w for w in QApplication.topLevelWidgets() if hasattr(w, "tool_manager") and hasattr(w, "state")]
        self.owner = owners[0] if owners else parent
        self.owner.destroyed.connect(self.owner_destroyed)

    def owner_destroyed(self) -> None:
        self.owner = None
        self.cancel(reset=False)

    def dialog_destroyed(self, dialog: UpdateDialog) -> None:
        if self.dialog is dialog:
            self.dialog = None

    def check(self, parent: QWidget | None = None, *, automatic: bool = False, force: bool = False) -> None:
        self.remember_owner(parent)
        if automatic and self.manual_used:
            return
        if not automatic:
            self.manual_used = True
        if self.process is not None:
            if not automatic:
                self.automatic = False
                dialog = self.show(parent)
                if self.operation == "check":
                    dialog.status.setText("Checking for updates…")
            return
        if automatic and not startup_enabled():
            return
        if not automatic and self.ready and self.plan and not force:
            dialog = self.show(parent)
            dialog.status.setText("Update downloaded. Save your work and restart to install it.")
            dialog.notes.setMarkdown(self.plan.get("notes") or tr("Read the release notes on GitHub before updating."))
            self.reset_controls()
            return
        self.automatic = automatic
        self.plan = None
        self.ready = False
        if not automatic:
            dialog = self.show(parent)
            dialog.status.setText("Checking for updates…")
            dialog.notes.clear()
        self.start("check")

    def start(self, operation: str) -> None:
        self.operation = operation
        self.buffer.clear()
        self.received = None
        self.protocol_error = False
        process = QProcess(self)
        self.process = process
        if getattr(sys, "frozen", False):
            arguments = ["--mds-update-worker", operation]
        else:
            arguments = ["-m", "materials_data_studio", "--mds-update-worker", operation]
        process.setProgram(sys.executable)
        process.setArguments(arguments)
        process.readyReadStandardOutput.connect(self.read_output)
        process.readyReadStandardError.connect(lambda: process.readAllStandardError())
        process.finished.connect(lambda code, status: self.finished(process, code if status == QProcess.ExitStatus.NormalExit else 1))
        process.errorOccurred.connect(lambda error: self.failed_start(process, error))
        process.started.connect(lambda: self.write_request(process, operation))
        if self.dialog:
            self.dialog.version_label.setVisible(self.plan is not None)
            self.dialog.action.setEnabled(False)
            self.dialog.recheck.setEnabled(False)
            self.dialog.close_button.setText("Cancel download" if operation == "download" else "Cancel")
            translate_tree(self.dialog)
            self.dialog.progress.setRange(0, 0)
            self.dialog.progress.show()
        # Windows may emit FailedToStart synchronously inside start().
        # Establish busy controls first so its error handler can restore them.
        process.start()

    def write_request(self, process: QProcess, operation: str) -> None:
        if operation == "download":
            process.write(json.dumps(self.plan).encode("utf-8"))
        process.closeWriteChannel()

    def failed_start(self, process: QProcess, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self.finished(process, 1)

    def read_output(self) -> None:
        if self.process is None:
            return
        self.buffer.extend(self.process.readAllStandardOutput().data())
        if len(self.buffer) > updates.MAX_MESSAGE:
            self.protocol_error = True
            self.process.kill()
            return
        while b"\n" in self.buffer:
            raw, _, rest = self.buffer.partition(b"\n")
            self.buffer = bytearray(rest)
            try:
                event = json.loads(raw)
                if event["event"] == "result" and isinstance(event["value"], dict):
                    self.received = event["value"]
                elif event["event"] == "progress" and self.dialog:
                    self.dialog.progress.setRange(0, 100)
                    self.dialog.progress.setValue(max(0, min(100, int(event["value"]))))
                elif event["event"] == "error":
                    self.protocol_error = True
            except (ValueError, KeyError, TypeError):
                self.protocol_error = True

    def finished(self, process: QProcess, code: int) -> None:
        if process is not self.process:
            return
        self.read_output()
        self.process = None
        process.deleteLater()
        result = self.received
        if code or self.protocol_error or self.buffer.strip() or result is None:
            if not self.automatic:
                self.show().status.setText("Update operation failed. Please check your connection and try again.")
                self.reset_controls()
            return
        if self.operation == "check":
            candidate = result.get("plan")
            try:
                self.plan = updates.validate_plan(candidate) if candidate is not None else None
            except (ValueError, TypeError, AttributeError):
                self.plan = None
                if not self.automatic:
                    self.show().status.setText("The update information is invalid. Please try again later.")
                    self.reset_controls()
                return
            if self.automatic and (self.plan is None or not startup_enabled()):
                return
            self.offer(bool(result.get("supported")))
        elif result.get("ready") is True:
            self.ready = True
            self.automatic = False
            self.show().status.setText("Update downloaded. Save your work and restart to install it.")
            self.reset_controls()
        else:
            self.show().status.setText("Update operation failed. Please check your connection and try again.")
            self.reset_controls()

    def offer(self, supported: bool) -> None:
        if self.automatic and (self.plan is None or not startup_enabled()):
            return
        if self.automatic and QApplication.activeModalWidget() is not None:
            QTimer.singleShot(500, lambda: self.offer(supported) if self.plan else None)
            return
        dialog = self.show()
        if self.plan:
            dialog.status.setText("An update is available:")
            dialog.notes.setMarkdown(self.plan.get("notes") or tr("Read the release notes on GitHub before updating."))
        elif supported:
            dialog.status.setText("You are using the latest available version.")
            dialog.notes.setPlainText(tr("Read the release notes on GitHub before updating."))
        else:
            dialog.status.setText("Install MDS with Setup to use in-app updates. Source and portable copies can open the release page below.")
            dialog.notes.setPlainText(tr("Read the release notes on GitHub before updating."))
        self.reset_controls()

    def reset_controls(self) -> None:
        if self.dialog:
            self.dialog.version_label.setText(self.plan["version"] if self.plan else "")
            self.dialog.version_label.setVisible(self.plan is not None)
            self.dialog.progress.hide()
            self.dialog.action.setEnabled(True)
            self.dialog.recheck.setVisible(self.plan is not None)
            self.dialog.recheck.setEnabled(True)
            text = "Restart and update" if self.ready else "Download update" if self.plan else "Check again"
            self.dialog.action.setText(text)
            self.dialog.close_button.setText("Later")
            translate_tree(self.dialog)

    def activate(self) -> None:
        if self.process:
            return
        if self.ready and self.plan:
            self.restart()
        elif self.plan:
            self.automatic = False
            self.show().status.setText("Downloading update… You can cancel and retry later.")
            self.start("download")
        else:
            self.check()

    def cancel(self, *, reset: bool = True) -> None:
        process, self.process = self.process, None
        if process:
            process.kill()
            process.waitForFinished(1500)
            process.deleteLater()
        if reset and self.dialog is not None and isValid(self.dialog):
            self.reset_controls()

    def restart(self) -> None:
        if self.plan is None:
            return
        from materials_data_studio.ui.tasks import has_active_tasks
        windows: list[Any] = [w for w in QApplication.topLevelWidgets() if hasattr(w, "confirm_update_shutdown")]
        if has_active_tasks() or any(getattr(w, "command_workspace", None) is not None and w.command_workspace.is_running() for w in windows):
            QMessageBox.information(self.dialog, tr("Software updates"), tr("Wait for running analyses or procedures to finish, then retry."))
            return
        if not all(w.confirm_update_shutdown() for w in windows):
            return
        if has_active_tasks() or any(getattr(w, "command_workspace", None) is not None and w.command_workspace.is_running() for w in windows):
            QMessageBox.information(self.dialog, tr("Software updates"), tr("Wait for running analyses or procedures to finish, then retry."))
            return
        try:
            if updates.other_instances_running():
                QMessageBox.information(self.dialog, tr("Software updates"), tr("Close other running MDS instances before updating."))
                return
            updates.arm_restart(self.plan, [])
        except Exception:
            QMessageBox.warning(self.dialog, tr("Software updates"), tr("Could not start the update. Your work remains open. Please try again."))
            return
        for window in windows:
            window._update_shutdown_approved = True
            if hasattr(window, "_discard_approved"):
                window._discard_approved = True
        QApplication.closeAllWindows()
        QApplication.quit()


def controller() -> UpdateController:
    application = QApplication.instance()
    if not isinstance(application, QApplication):
        raise RuntimeError("Qt application is required")
    result = getattr(application, "_mds_update_controller", None)
    if result is None:
        result = UpdateController(application)
        application._mds_update_controller = result  # type: ignore[attr-defined]
    return result


def add_update_actions(menu: QMenu, parent: QWidget) -> None:
    action = QAction("Check for updates…", parent)
    action.triggered.connect(lambda: controller().check(parent))
    menu.addAction(action)
    startup = QAction("Check for updates at startup", parent)
    startup.setCheckable(True)
    startup.setChecked(startup_enabled())
    startup.toggled.connect(set_startup_enabled)
    menu.addAction(startup)
    def synchronize() -> None:
        startup.blockSignals(True)
        startup.setChecked(startup_enabled())
        startup.blockSignals(False)
    menu.aboutToShow.connect(synchronize)
