"""Application shell, menus, compact toolbar, drag/drop, and sessions."""

from __future__ import annotations

import copy
import logging
from pathlib import Path
from threading import Event
from typing import Any

from PySide6.QtCore import QByteArray, QSize, Qt, QThreadPool, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence, QUndoStack
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from materials_data_studio.config import (
    APP_NAME,
    LEGACY_SESSION_EXTENSIONS,
    SESSION_EXTENSION,
)
from materials_data_studio.core.models import ModuleType
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.core.recovery import (
    create_diagnostic_bundle,
    create_recovery_path,
    discard_recovery,
    save_recovery_snapshot,
)
from materials_data_studio.core.session import load_session, save_session
from materials_data_studio.core.settings import create_settings
from materials_data_studio.core.workers import Worker
from materials_data_studio.modules.mpms.module import MpmsModule
from materials_data_studio.modules.ppms.module import PpmsModule
from materials_data_studio.modules.registry import ModuleRegistry
from materials_data_studio.modules.xrd.module import XrdModule
from materials_data_studio.plotting.appearance import (
    active_plot_appearance,
    load_plot_appearance,
    reset_plot_appearance,
    save_plot_appearance,
    set_active_plot_appearance,
)
from materials_data_studio.tools import ToolMenuManager
from materials_data_studio.ui.dialogs import show_error
from materials_data_studio.ui.icons import toolbar_icon
from materials_data_studio.ui.manual import UserManualDialog, resolve_user_manual_path
from materials_data_studio.ui.preferences import PreferencesDialog
from materials_data_studio.ui.tasks import blocking_task, cancel_tasks
from materials_data_studio.ui.theme import apply_application_theme
from materials_data_studio.ui.undo_history import (
    WorkspaceFingerprinter,
    WorkspaceSnapshot,
    WorkspaceUndoController,
    restore_snapshot,
)

LOGGER = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Module-independent main application window."""

    def __init__(self, state: ProjectState | None = None) -> None:
        super().__init__()
        from materials_data_studio.ui.input_wheel_guard import install_input_wheel_guard
        install_input_wheel_guard()
        self.state = state or ProjectState()
        self.registry = ModuleRegistry()
        self.registry.register_all(
            [XrdModule(self.state), MpmsModule(self.state), PpmsModule(self.state)]
        )
        self.undo_stack = QUndoStack(self)
        self.module_tabs = QTabWidget()
        self.module_tabs.setDocumentMode(True)
        self.module_tabs.tabBar().setProperty("navigation", "primary")
        self.module_tabs.tabBar().setExpanding(False)
        for index, module in enumerate(self.registry.modules()):
            page = (
                module.create_widget(self.module_tabs)
                if index == 0
                else self._module_placeholder(module.display_name)
            )
            self.module_tabs.addTab(page, module.display_name)
        self.module_tabs.currentChanged.connect(self._active_tab_changed)
        self.setCentralWidget(self.module_tabs)
        self.setAcceptDrops(True)
        from materials_data_studio.tools.graph_file_drop import install_file_drops
        self.file_drop_filter = install_file_drops(self)
        self.setWindowTitle(APP_NAME)
        self.resize(1600, 950)
        # Keep settings scrollable on a 1080p display at 200% scaling.
        self.setMinimumSize(900, 480)
        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setMaximumWidth(220)
        self.statusBar().addPermanentWidget(self.progress)
        self.history_memory = QLabel("Undo: 0 MiB")
        self.history_memory.setToolTip("Retained history storage, including original bytes; excludes current data. Not total process RAM.")
        self.statusBar().addPermanentWidget(self.history_memory)
        self.statusBar().showMessage("Ready")
        settings = create_settings()
        set_active_plot_appearance(load_plot_appearance(settings))
        stored_theme = str(settings.value("appearance/theme", "light"))
        self.apply_theme(stored_theme if stored_theme in {"light", "dark"} else "light")
        from materials_data_studio.ui.localization import set_language
        set_language(str(settings.value("appearance/language", "en")))
        self._undo_fingerprinter = WorkspaceFingerprinter()
        self.undo_history = WorkspaceUndoController(
            self.undo_stack,
            self._capture_undo_snapshot,
            self._fingerprint_undo_snapshot,
            self._restore_undo_snapshot,
            self._set_modified_from_history,
            self,
            change_token=lambda: self.state.change_token,
        )
        self._watch_workspace_controls(self.module_tabs)
        self.undo_history.historyTrimmed.connect(lambda message: self.statusBar().showMessage(message, 15000))
        self.undo_stack.indexChanged.connect(lambda _index: QTimer.singleShot(0, self._update_history_memory))
        self._recovery_path = create_recovery_path()
        self._recovery_worker: Worker | None = None
        self._recovery_discard_when_finished = False
        self._closing_cleanly = False
        self._recovery_timer = QTimer(self)
        self._recovery_timer.setInterval(60_000)
        self._recovery_timer.timeout.connect(self._save_recovery_copy)
        self._recovery_timer.start()
        if self.state.pxp_documents:
            self.tool_manager.sync_pxp_viewers()

    @staticmethod
    def _module_placeholder(display_name: str) -> QWidget:
        """Create a lightweight page while an unvisited module remains unloaded."""
        page = QWidget()
        page.setProperty("lazyModulePlaceholder", True)
        layout = QVBoxLayout(page)
        label = QLabel(f"Open the {display_name} tab to load its workspace.")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
        return page

    def _ensure_module_widget(self, index: int) -> QWidget | None:
        """Instantiate an analysis workspace the first time its tab is used."""
        modules = self.registry.modules()
        if not 0 <= index < len(modules):
            return None
        module = modules[index]
        existing = getattr(module, "widget", None)
        if existing is not None:
            return existing

        self.statusBar().showMessage(f"Loading {module.display_name} workspace…")
        placeholder = self.module_tabs.widget(index)
        widget = module.create_widget(self.module_tabs)
        for child in [widget, *widget.findChildren(QWidget)]:
            child.setAcceptDrops(True)
            child.installEventFilter(self.file_drop_filter)
        module.restore_state(dict(getattr(module, "_state", {})))
        blocked = self.module_tabs.blockSignals(True)
        try:
            self.module_tabs.removeTab(index)
            self.module_tabs.insertTab(index, widget, module.display_name)
            self.module_tabs.setCurrentIndex(index)
        finally:
            self.module_tabs.blockSignals(blocked)
        if placeholder is not None:
            placeholder.deleteLater()
        if hasattr(self, "undo_history"):
            self._watch_workspace_controls(widget)
        apply_theme = getattr(widget, "apply_theme", None)
        if callable(apply_theme):
            apply_theme(getattr(self, "_current_theme", "light"))
        from materials_data_studio.ui.localization import translate_tree
        translate_tree(widget)
        self.statusBar().showMessage(f"{module.display_name} workspace ready", 2500)
        return widget

    def _watch_workspace_controls(self, root: QWidget) -> None:
        def changed(*_args: Any) -> None:
            if not self.undo_history.restoring:
                self.state.modified = True

        for control in root.findChildren(QWidget):
            if control.property("historyObserved"):
                continue
            signal = None
            if isinstance(control, QAbstractSpinBox):
                signal = control.editingFinished
            elif isinstance(control, QComboBox):
                signal = control.currentIndexChanged
            elif isinstance(control, QLineEdit):
                signal = control.textEdited
            elif isinstance(control, QTabWidget):
                signal = control.currentChanged
            elif isinstance(control, QAbstractButton):
                signal = control.clicked
            if signal is not None:
                signal.connect(changed)
                control.setProperty("historyObserved", True)

    def _action(
        self,
        text: str,
        slot: Any,
        shortcut: QKeySequence.StandardKey | str | None = None,
    ) -> QAction:
        action = QAction(text, self)
        # QAction.triggered emits a checked bool. Do not leak that value into
        # slots such as open_session(path=None), where False would otherwise be
        # interpreted as a file-like object by zipfile.
        action.triggered.connect(lambda _checked=False: slot())
        if shortcut is not None:
            action.setShortcut(shortcut)
        return action

    def _build_actions(self) -> None:
        self.new_action = self._action(
            "New Session", self.new_session, QKeySequence.StandardKey.New
        )
        self.open_action = self._action(
            "Open Session", self.open_session, QKeySequence.StandardKey.Open
        )
        self.save_action = self._action(
            "Save Session", self.save_session, QKeySequence.StandardKey.Save
        )
        self.save_as_action = self._action("Save Session As", self.save_session_as, "Ctrl+Shift+S")
        self.import_action = self._action("Import Data", self.import_data, "Ctrl+I")
        self.open_pxp_action = self._action(
            "Open Igor Experiment (.pxp)", self.open_igor_experiment
        )
        self.open_graph_action = self._action(
            "Open Graph Document (.mdsg)", self.open_graph_document
        )
        self.save_graph_action = self._action(
            "Save Graph Document", lambda: self.tool_manager.save_graph()
        )
        self.save_graph_as_action = self._action(
            "Save Graph Document As", lambda: self.tool_manager.save_graph(save_as=True)
        )
        self.export_data_action = self._action("Export Data", self.export_data, "Ctrl+E")
        self.export_figure_action = self._action(
            "Export Figure", self.export_figure, "Ctrl+Shift+E"
        )
        self.send_to_igor_action = self._action("Send to Igor Pro", self.send_to_igor)
        self.edit_graph_action = self._action("Edit current plot in Graph Editor", self.edit_current_plot)
        self.undo_action = self.undo_stack.createUndoAction(self, "Undo")
        self.undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self.redo_action = self.undo_stack.createRedoAction(self, "Redo")
        self.redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        self.preferences_action = self._action("Preferences", self.show_preferences)
        self.manual_action = self._action("User Manual (English)", self.show_user_manual, "F1")
        self.japanese_manual_action = self._action(
            "ユーザーマニュアル (日本語)", self.show_japanese_user_manual
        )
        self.diagnostics_action = self._action("Create Diagnostic Bundle", self.create_diagnostics)
        self.about_action = self._action("About", self.show_about)
        self.remove_action = self._action("Remove Dataset", self.remove_selected, "Delete")
        self.addAction(self.remove_action)
        application = QApplication.instance()
        if isinstance(application, QApplication):
            application.focusChanged.connect(self._dataset_delete_focus)
        self.remove_action.setEnabled(False)

        self._quick_actions = (
            (self.new_action, "new"),
            (self.open_action, "open"),
            (self.save_action, "save"),
            (self.import_action, "import"),
            (self.export_data_action, "export_data"),
            (self.export_figure_action, "export_figure"),
            (self.send_to_igor_action, "send_to_igor"),
            (self.edit_graph_action, "edit_graph"),
            (self.undo_action, "undo"),
            (self.redo_action, "redo"),
        )
        for action, _icon_name in self._quick_actions:
            shortcut = action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
            action.setToolTip(f"{action.text()} ({shortcut})" if shortcut else action.text())
            if action in {self.undo_action, self.redo_action}:
                action.setToolTip(action.toolTip() + " — main analysis session; Graph Editor has separate history")

    def _build_menus(self) -> None:
        """Build the permanent File / Tools / View / Help application menu order."""
        menu_bar = self.menuBar()
        self.file_menu = menu_bar.addMenu("File")
        for action in (self.new_action, self.open_action):
            self.file_menu.addAction(action)
        self.file_menu.addSeparator()
        for action in (self.save_action, self.save_as_action):
            self.file_menu.addAction(action)
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.import_action)
        self.file_menu.addAction(self.open_pxp_action)
        self.file_menu.addAction(self.open_graph_action)
        self.file_menu.addAction(self.save_graph_action)
        self.file_menu.addAction(self.save_graph_as_action)
        self.file_menu.addSeparator()
        for action in (
            self.export_data_action,
            self.export_figure_action,
            self.send_to_igor_action,
        ):
            self.file_menu.addAction(action)
        self.file_menu.addSeparator()
        for action in (self.undo_action, self.redo_action):
            self.file_menu.addAction(action)
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.edit_graph_action)
        self.file_menu.addAction(self._action("Analysis Workbench…", self.show_workbench, "Ctrl+Shift+A"))
        self.recent_menu = self.file_menu.addMenu("Recent files / resume workspace")
        self.refresh_recent_files()
        self.recent_menu.aboutToShow.connect(self.refresh_recent_files)

        self.tools_menu = menu_bar.addMenu("Tools")
        self.tool_manager = ToolMenuManager(self.tools_menu, self)

        self.view_menu = menu_bar.addMenu("View")
        self.view_menu.aboutToShow.connect(self.refresh_view_menu)
        self.refresh_view_menu()
        self.help_menu = menu_bar.addMenu("Help")
        self.help_menu.addAction(self.preferences_action)
        self.help_menu.addSeparator()
        self.help_menu.addAction(self.manual_action)
        self.help_menu.addAction(self.japanese_manual_action)
        from materials_data_studio.ui.licenses import show_licenses
        self.help_menu.addAction(self._action("Licenses and Source (English)", lambda: show_licenses(self, "en")))
        self.help_menu.addAction(self._action("ライセンス・対応ソース (日本語)", lambda: show_licenses(self, "ja")))
        self.help_menu.addAction(self.diagnostics_action)
        self.help_menu.addAction(self.about_action)
        self.help_menu.addAction(self._action("Context help", self.context_help, "Shift+F1"))
        self.help_menu.addAction(self._action("Recovery Center…", self.show_recovery_center))

    def refresh_view_menu(self) -> None:
        from materials_data_studio.ui.workspace_layout import WorkspaceSplitter, populate_view_menu
        widget = self.active_widget()
        workspace = getattr(widget, "active_workspace", widget)
        splitter = getattr(workspace, "splitter", None)
        if isinstance(splitter, WorkspaceSplitter):
            populate_view_menu(self.view_menu, splitter)

    def _build_toolbar(self) -> None:
        """Expose frequent operations as small icon-only shortcuts."""
        self.main_toolbar = self.addToolBar("Quick Actions")
        self.main_toolbar.setObjectName("QuickActionsToolbar")
        self.main_toolbar.setAccessibleName("Quick actions")
        self.main_toolbar.setMovable(False)
        self.main_toolbar.setFloatable(False)
        self.main_toolbar.setIconSize(QSize(15, 15))
        self.main_toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        for group in (
            (self.new_action, self.open_action, self.save_action),
            (
                self.import_action,
                self.export_data_action,
                self.export_figure_action,
                self.send_to_igor_action,
                self.edit_graph_action,
            ),
            (self.undo_action, self.redo_action),
        ):
            if self.main_toolbar.actions():
                self.main_toolbar.addSeparator()
            for action in group:
                self.main_toolbar.addAction(action)

    def active_module(self):  # type: ignore[no-untyped-def]
        """Return the module associated with the visible top-level tab."""
        index = self.module_tabs.currentIndex()
        modules = self.registry.modules()
        return modules[index] if 0 <= index < len(modules) else None

    def active_widget(self):  # type: ignore[no-untyped-def]
        """Return the current module's primary widget."""
        return self._ensure_module_widget(self.module_tabs.currentIndex())

    def _active_tab_changed(self, index: int) -> None:
        modules = self.registry.modules()
        if not 0 <= index < len(modules):
            return
        self._ensure_module_widget(index)
        module_id = modules[index].module_id
        self.state.active_module = ModuleType(module_id)
        self.state.modified = True

    def _collect_ui_state(self) -> None:
        self.state.module_states = {
            module.module_id: module.serialize_state() for module in self.registry.modules()
        }
        active_module = self.active_module()
        if active_module is not None:
            self.state.active_module = ModuleType(active_module.module_id)
        geometry_bytes = bytes(self.saveGeometry().toBase64().data())
        self.state.window_state = {
            "geometry": geometry_bytes.decode("ascii"),
        }

    def _capture_undo_snapshot(self) -> WorkspaceSnapshot:
        module_states = {
            module.module_id: module.serialize_state() for module in self.registry.modules()
        }
        digest = self._undo_fingerprinter.digest(self.state, module_states)
        return WorkspaceSnapshot.capture(self.state, module_states, digest=digest)

    def _fingerprint_undo_snapshot(self) -> str:
        module_states = {
            module.module_id: module.serialize_state() for module in self.registry.modules()
        }
        return self._undo_fingerprinter.digest(self.state, module_states)

    def _restore_undo_snapshot(self, snapshot: WorkspaceSnapshot, direction: str) -> None:
        self.state.invalidate_jobs()
        restore_snapshot(self.state, snapshot)
        cancel_tasks(self.state)
        self.tool_manager.sync_pxp_viewers()
        for module in self.registry.modules():
            module.restore_state(snapshot.module_states.get(module.module_id, {}))
        module_index = next(
            (
                index
                for index, module in enumerate(self.registry.modules())
                if module.module_id == snapshot.active_module.value
            ),
            0,
        )
        self.module_tabs.setCurrentIndex(module_index)
        self.statusBar().showMessage(f"{direction} completed", 3000)

    def _set_modified_from_history(self, modified: bool) -> None:
        self.state.modified = modified

    def new_session(self) -> None:
        """Clear state after a standard unsaved-change confirmation."""
        if not self._confirm_leave_current_session("create a new session"):
            return
        self._discard_recovery_copy()
        self.state.invalidate_jobs()
        self.state.datasets.clear()
        cancel_tasks(self.state)
        self.tool_manager.close_pxp_viewers()
        self.state.pxp_documents.clear()
        self.state.pxp_view_states.clear()
        self.state.module_states.clear()
        self.state.path = None
        self.state.modified = False
        for module in self.registry.modules():
            module.restore_state({})
            widget = getattr(module, "widget", None)
            if widget is not None and hasattr(widget, "refresh"):
                widget.refresh()
        self.undo_history.reset(clean=True)
        self.statusBar().showMessage("New session", 3000)

    def open_igor_experiment(self) -> None:
        """Open an immutable PXP source in the independent Tools viewer."""
        filename, _ = QFileDialog.getOpenFileName(
            self, "Open Igor Experiment", "", "Igor Packed Experiment (*.pxp)"
        )
        if filename:
            self.tool_manager.open_pxp(Path(filename))

    def open_graph_document(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "Open Graph Document", "", "MDS Graph Document (*.mdsg)"
        )
        if filename:
            self.tool_manager.open_graph(Path(filename))

    def open_session(self, path: Path | None = None) -> None:
        """Open, validate, and restore a self-contained session."""
        if path is None:
            name, _ = QFileDialog.getOpenFileName(
                self,
                "Open Session",
                "",
                f"Materials Data Studio (*{SESSION_EXTENSION});;"
                "Legacy Materials Data Studio (*.mdsession)",
            )
            if not name:
                return
            path = Path(name)
        if not self._confirm_leave_current_session("open another session"):
            return
        previous = self._capture_undo_snapshot()
        previous_path, previous_modified = self.state.path, self.state.modified
        try:
            loaded = blocking_task(self, "Open session", lambda: load_session(path))
            self.state.invalidate_jobs()
            cancel_tasks(self.state)
            self.state.datasets = loaded.datasets
            self.state.pxp_documents = loaded.pxp_documents
            self.state.pxp_view_states = loaded.pxp_view_states
            self.state.active_module = loaded.active_module
            self.state.module_states = loaded.module_states
            self.state.axis_settings = loaded.axis_settings
            self.state.window_state = loaded.window_state
            self.state.path = path
            self.state.modified = False
            for module in self.registry.modules():
                module.restore_state(loaded.module_states.get(module.module_id, {}))
            module_index = next(
                (
                    index
                    for index, module in enumerate(self.registry.modules())
                    if module.module_id == loaded.active_module.value
                ),
                0,
            )
            self.module_tabs.setCurrentIndex(module_index)
            geometry = loaded.window_state.get("geometry")
            if isinstance(geometry, str):
                self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
            self.undo_history.reset(clean=True)
            self.tool_manager.close_pxp_viewers()
            self.tool_manager.sync_pxp_viewers()
            self._discard_recovery_copy()
            self.statusBar().showMessage(f"Opened {path.name}", 5000)
            self.remember_file(path)
        except Exception as exc:
            restore_snapshot(self.state, previous)
            for module in self.registry.modules():
                module.restore_state(previous.module_states.get(module.module_id, {}))
            self.module_tabs.setCurrentIndex(
                next((index for index, module in enumerate(self.registry.modules())
                      if module.module_id == previous.active_module.value), 0)
            )
            geometry = self.state.window_state.get("geometry")
            if isinstance(geometry, str):
                self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
            self.state.path, self.state.modified = previous_path, previous_modified
            LOGGER.exception("Session open failed: %s", path)
            show_error(self, "Open Session Error", str(exc))

    def save_session(self) -> bool:
        """Save to the current path, or prompt for one."""
        if self.state.path is None:
            return self.save_session_as()
        return self._save_to(self.state.path)

    def save_session_as(self) -> bool:
        """Prompt for a session filename and save."""
        name, _ = QFileDialog.getSaveFileName(
            self,
            "Save Session As",
            f"session{SESSION_EXTENSION}",
            f"Materials Data Studio (*{SESSION_EXTENSION})",
        )
        return bool(name) and self._save_to(Path(name))

    def _save_to(self, path: Path) -> bool:
        try:
            self.undo_history.checkpoint()
            self._collect_ui_state()
            snapshot = self._capture_undo_snapshot()
            saved = ProjectState(
                datasets=list(snapshot.datasets),
                active_module=snapshot.active_module,
                module_states=snapshot.module_states,
                axis_settings=snapshot.axis_settings,
                window_state=copy.deepcopy(self.state.window_state),
                pxp_documents=snapshot.pxp_documents,
                pxp_view_states=snapshot.pxp_view_states,
            )
            cancelled = Event()
            blocking_task(
                self,
                "Save session",
                lambda: save_session(saved, path, cancel_event=cancelled),
                cancelled=cancelled,
            )
            self.state.path = saved.path
            self.remember_file(path)
            self.undo_history.mark_clean()
            self._discard_recovery_copy()
            saved_path = self.state.path
            self.statusBar().showMessage(
                f"Saved {saved_path.name if saved_path is not None else path.name}", 5000
            )
            return True
        except Exception as exc:
            LOGGER.exception("Session save failed: %s", path)
            show_error(self, "Save Session Error", str(exc))
            return False

    def _confirm_leave_current_session(self, action: str) -> bool:
        """Offer Save/Discard/Cancel before replacing a modified workspace."""
        self.undo_history.checkpoint()
        if not self.state.modified:
            return True
        response = QMessageBox.question(
            self,
            "Unsaved Changes",
            f"Save the current session before you {action}?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if response == QMessageBox.StandardButton.Save:
            return self.save_session()
        return response == QMessageBox.StandardButton.Discard

    def _save_recovery_copy(self) -> None:
        """Snapshot a dirty workspace in the background without marking it clean."""
        if (
            self._closing_cleanly
            or not self.state.modified
            or (not self.state.datasets and not self.state.pxp_documents)
            or self._recovery_worker is not None
        ):
            return
        self._collect_ui_state()
        snapshot = self._capture_undo_snapshot()
        recovery_state = ProjectState(
            datasets=list(snapshot.datasets),
            active_module=snapshot.active_module,
            module_states=copy.deepcopy(snapshot.module_states),
            axis_settings=copy.deepcopy(snapshot.axis_settings),
            window_state=copy.deepcopy(self.state.window_state),
            pxp_documents=dict(snapshot.pxp_documents),
            pxp_view_states=copy.deepcopy(snapshot.pxp_view_states),
            path=self.state.path,
            modified=True,
        )
        worker = Worker(save_recovery_snapshot, recovery_state, self._recovery_path)
        worker.signals.error.connect(self._recovery_error)
        worker.signals.finished.connect(lambda active=worker: self._recovery_finished(active))
        self._recovery_worker = worker
        QThreadPool.globalInstance().start(worker)

    def _recovery_error(self, message: str, traceback_text: str) -> None:
        LOGGER.error("Automatic recovery save failed: %s\n%s", message, traceback_text)
        self.statusBar().showMessage(f"Recovery save failed; save your session explicitly: {message}", 15000)

    def _recovery_finished(self, worker: Worker) -> None:
        if self._recovery_worker is worker:
            self._recovery_worker = None
        if self._closing_cleanly or self._recovery_discard_when_finished:
            discard_recovery(self._recovery_path)
        self._recovery_discard_when_finished = False

    def _discard_recovery_copy(self) -> None:
        """Remove both an existing snapshot and one currently being written."""
        discard_recovery(self._recovery_path)
        if self.state.recovery_source is not None:
            discard_recovery(self.state.recovery_source)
            self.state.recovery_source = None
        if self._recovery_worker is not None:
            self._recovery_discard_when_finished = True

    def _finish_clean_shutdown(self) -> None:
        self.file_drop_filter.cancel()
        self.state.invalidate_jobs()
        cancel_tasks(self.state)
        self.tool_manager.close_graph_documents()
        self._closing_cleanly = True
        self._recovery_timer.stop()
        self._discard_recovery_copy()

    def create_diagnostics(self) -> None:
        """Export logs and platform metadata without including sessions or measurements."""
        from materials_data_studio.ui.diagnostic_review import review_diagnostics
        reviewed = review_diagnostics(self)
        if reviewed is None:
            return
        name, _ = QFileDialog.getSaveFileName(
            self,
            "Create Diagnostic Bundle",
            "materials-data-studio-diagnostics.zip",
            "ZIP archive (*.zip)",
        )
        if not name:
            return
        try:
            target = create_diagnostic_bundle(Path(name), reviewed_logs=reviewed)
        except OSError as exc:
            LOGGER.exception("Could not create diagnostic bundle")
            show_error(self, "Diagnostic Bundle Error", str(exc))
            return
        QMessageBox.information(
            self,
            "Diagnostic Bundle",
            "Diagnostic metadata and application logs were saved. Logs can contain "
            f"local file paths, so review the archive before sharing it.\n\n{target}",
        )

    def import_data(self) -> None:
        """Delegate file browsing to the active registered module."""
        widget: Any = self.active_widget()
        if hasattr(widget, "choose_import_files"):
            widget.choose_import_files()
        else:
            self.statusBar().showMessage("This module does not import files yet.", 4000)

    def export_data(self) -> None:
        """Open the same unified export dialog used by interactive plots."""
        from materials_data_studio.plotting.pyqtgraph_backend import (
            export_interactive_plot,
        )

        _widget, _workspace, plot = self._active_interactive_plot()
        if plot is not None and getattr(plot, "_materials_plot_spec", None) is not None:
            export_interactive_plot(plot)
            return
        self.statusBar().showMessage("There is no interactive plot to export.", 4000)

    def show_workbench(self) -> None:
        from materials_data_studio.ui.analysis_workbench import AnalysisWorkbench
        window = AnalysisWorkbench(self)
        window.show()

    def _update_history_memory(self) -> None:
        from shiboken6 import isValid
        if isValid(self.history_memory):
            self.history_memory.setText(f"Undo: {getattr(self.undo_history, 'retained_memory_bytes', 0) / 1024**2:.1f} MiB")

    def refresh_analysis_modules(self) -> None:
        for module in self.registry.modules():
            widget = getattr(module, "widget", None)
            if widget is not None and hasattr(widget, "refresh"):
                widget.refresh()

    def accept_recipe(self, dataset: Any, result: Any) -> None:
        if dataset.module_type == ModuleType.MPMS or dataset.metadata.get("ppms_kind") == "heat_capacity":
            from materials_data_studio.core.measurement_branches import SAMPLE_KEYS, share_sample
            dataset.metadata.update({key: result.metadata[key] for key in SAMPLE_KEYS if key in result.metadata})
            share_sample(self.state, dataset)
        dataset.metadata, dataset.processed_data, dataset.analysis_results = result.metadata, result.processed_data, result.analysis_results
        self.state.modified = True
        self.refresh_analysis_modules()
        self.undo_history.checkpoint(force=True)

    def open_native_graph(self, document: Any) -> Any:
        from materials_data_studio.tools.graph_window import GraphEditorWindow
        window = self.tool_manager.open_tool("pxp_viewer")
        assert isinstance(window, GraphEditorWindow)
        window.bind_graph_document(document)
        return window

    def edit_current_plot(self) -> None:
        from materials_data_studio.tools.graph_workflows import from_plot_spec
        _widget, _workspace, plot = self._active_interactive_plot()
        spec = getattr(plot, "_materials_plot_spec", None)
        if spec is not None and spec.curves:
            self.open_native_graph(from_plot_spec(spec))
        else:
            self.statusBar().showMessage("There is no plotted data to edit.", 4000)

    def remember_file(self, path: Path) -> None:
        settings = create_settings()
        stored = settings.value("workspace/recent", [])
        recent = [str(path.absolute()), *(p for p in stored if p != str(path.absolute()))] if isinstance(stored, list) else [str(path.absolute())]
        settings.setValue("workspace/recent", recent[:15])
        self.refresh_recent_files()

    def refresh_recent_files(self) -> None:
        self.recent_menu.clear()
        recent = create_settings().value("workspace/recent", [])
        for name in recent if isinstance(recent, list) else []:
            path = Path(name)
            action = self.recent_menu.addAction(path.name)
            action.setToolTip(str(path))
            # Probe unavailable network/cloud paths only when explicitly opened.
            action.setEnabled(True)
            action.triggered.connect(lambda _checked=False, target=path: self.open_session(target) if target.suffix.lower() in {".mdss", ".mdsession"} else self.tool_manager.open_graph(target))
        self.recent_menu.addSeparator()
        self.recent_menu.addAction("Clear recent list").triggered.connect(lambda: (create_settings().remove("workspace/recent"), self.refresh_recent_files()))

    def context_help(self) -> None:
        from PySide6.QtCore import QUrl
        section = {"xrd": "3", "mpms": "4", "ppms": "5"}.get(str(getattr(self.active_module(), "module_id", "")), "2")
        manual = UserManualDialog(resolve_user_manual_path("en"), self)
        manual.show()
        manual._open_manual_link(QUrl("manual:section-" + section))

    def show_recovery_center(self) -> None:
        from materials_data_studio.ui.recovery_center import RecoveryCenter
        dialog = RecoveryCenter(self)
        dialog.exec()
        dialog.deleteLater()

    def _active_interactive_plot(self) -> tuple[Any, Any, Any]:
        """Resolve the visible module/workspace/plot through existing abstractions."""
        widget: Any = self.active_widget()
        workspace = getattr(widget, "active_workspace", widget)
        plot_tabs = getattr(workspace, "plot_tabs", None)
        plot = (
            plot_tabs.currentWidget()
            if plot_tabs is not None
            else getattr(workspace, "plot_widget", None)
        )
        if plot is None:
            tabs = getattr(widget, "tabs", None)
            plot = tabs.currentWidget() if tabs is not None else None
        if plot is not None:
            module = self.active_module()
            module_name = str(getattr(module, "module_id", "Graph")).upper()
            workspace_name = type(workspace).__name__.lower()
            if module_name == "PPMS":
                if "heatcapacity" in workspace_name or "heat_capacity" in workspace_name:
                    module_name = "HeatCapacity"
                elif "resistivity" in workspace_name:
                    module_name = "Resistivity"
            plot.setProperty("materialsIgorModule", module_name)
        return widget, workspace, plot

    def send_to_igor(self) -> None:
        """Generate a unique H5XP for the current graph and launch Igor Pro."""
        from materials_data_studio.integrations.igor import (
            IGOR_EXECUTABLE_SETTING,
            IgorLaunchError,
            IgorTransferService,
        )
        from materials_data_studio.plotting.igor_export import (
            graph_export_state_from_plot_spec,
        )
        from materials_data_studio.plotting.igor_h5xp import IgorExportError

        widget, workspace, plot = self._active_interactive_plot()
        spec = getattr(plot, "_materials_plot_spec", None) if plot is not None else None
        if spec is None:
            self.statusBar().showMessage("There is no interactive plot to send.", 4000)
            return
        module = self.active_module()
        module_name = str(getattr(module, "module_id", "MDS")).upper()
        workspace_name = type(workspace).__name__.lower()
        if module_name == "PPMS":
            if "heatcapacity" in workspace_name or "heat_capacity" in workspace_name:
                module_name = "HeatCapacity"
            elif "resistivity" in workspace_name:
                module_name = "Resistivity"
        graph_state = graph_export_state_from_plot_spec(spec, module_name)
        reflection_provider = getattr(widget, "igor_reflection_traces", None)
        if callable(reflection_provider):
            reflections = reflection_provider()
            if isinstance(reflections, list):
                graph_state.traces.extend(reflections)
        settings = create_settings()
        configured_executable = str(settings.value(IGOR_EXECUTABLE_SETTING, ""))
        try:
            result = IgorTransferService().send(graph_state, configured_executable)
        except IgorLaunchError as exc:
            LOGGER.exception("Igor Pro launch failed")
            show_error(
                self,
                "Send to Igor Pro",
                f"{exc}\n\nThe generated experiment was retained at:\n{exc.experiment_path}",
            )
            return
        except (IgorExportError, OSError, ValueError) as exc:
            LOGGER.exception("Igor packed-experiment export failed")
            show_error(self, "Send to Igor Pro", str(exc))
            return
        warning_suffix = f" ({len(result.warnings)} style warning(s))" if result.warnings else ""
        self.statusBar().showMessage(f"Sent to Igor Pro{warning_suffix}", 5000)

    def export_figure(self) -> None:
        from materials_data_studio.plotting.pyqtgraph_backend import export_interactive_plot

        _widget, _workspace, plot = self._active_interactive_plot()
        if plot is not None:
            export_interactive_plot(plot, default_kind="vector")

    def remove_selected(self) -> None:
        widget: Any = self.active_widget()
        dataset_list = getattr(widget, "dataset_list", None)
        if dataset_list is not None:
            dataset_list._remove()

    def _dataset_delete_focus(self, _old: Any, focused: Any) -> None:
        from materials_data_studio.ui.dataset_list import DatasetList
        target = focused
        while target is not None and not isinstance(target, DatasetList):
            target = target.parentWidget()
        self.remove_action.setEnabled(target is not None and focused is target.list_widget)

    def show_preferences(self) -> None:
        """Edit and immediately apply persistent application/plot appearance."""
        settings = create_settings()
        current = str(settings.value("appearance/theme", "light"))
        from materials_data_studio.integrations.igor import IGOR_EXECUTABLE_SETTING

        dialog = PreferencesDialog(
            current,
            active_plot_appearance(),
            self,
            igor_executable=str(settings.value(IGOR_EXECUTABLE_SETTING, "")),
        )

        def reset_defaults() -> None:
            from materials_data_studio.ui.localization import set_language
            set_language("en")
            settings.remove("appearance/theme")
            settings.remove(IGOR_EXECUTABLE_SETTING)
            reset_plot_appearance(settings)
            self.apply_theme("light")

        def apply_preferences() -> None:
            from materials_data_studio.ui.localization import set_language
            set_language(str(dialog.language.currentData()))
            save_plot_appearance(dialog.plot_appearance(), settings)
            executable = dialog.igor_executable_path()
            if executable:
                settings.setValue(IGOR_EXECUTABLE_SETTING, executable)
            else:
                settings.remove(IGOR_EXECUTABLE_SETTING)
            self.apply_theme(dialog.selected_theme())

        dialog.defaultsReset.connect(reset_defaults)
        dialog.applyRequested.connect(apply_preferences)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            apply_preferences()
        dialog.deleteLater()

    def apply_theme(self, theme: str) -> None:
        """Apply a light/dark theme to Qt and embedded plotting scenes."""
        normalized = theme if theme in {"light", "dark"} else "light"
        self._current_theme = normalized
        application = QApplication.instance()
        if isinstance(application, QApplication):
            apply_application_theme(application, normalized)
        for action, icon_name in self._quick_actions:
            action.setIcon(toolbar_icon(icon_name, normalized))
        settings = create_settings()
        settings.setValue("appearance/theme", normalized)
        settings.sync()
        for module in self.registry.modules():
            widget = getattr(module, "widget", None)
            if widget is not None and hasattr(widget, "apply_theme"):
                widget.apply_theme(normalized)

    def show_about(self) -> None:
        from materials_data_studio.version import __version__

        QMessageBox.about(
            self,
            f"About {APP_NAME}",
            f"{APP_NAME} {__version__}\n"
            "Scientific plotting and analysis for XRD, MPMS, and PPMS workflows.",
        )

    def show_user_manual(self) -> None:
        """Open the default English manual inside the application."""
        try:
            dialog = UserManualDialog(resolve_user_manual_path(), self)
        except (OSError, UnicodeError) as exc:
            show_error(self, "User Manual Error", str(exc))
            return
        dialog.exec()
        dialog.deleteLater()

    def show_japanese_user_manual(self) -> None:
        """Open the bundled Japanese manual inside the application."""
        try:
            dialog = UserManualDialog(resolve_user_manual_path("ja"), self)
        except (OSError, UnicodeError) as exc:
            show_error(self, "User Manual Error", str(exc))
            return
        dialog.exec()
        dialog.deleteLater()

    def dragEnterEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        """Accept one or more local files from Windows Explorer."""
        urls = event.mimeData().urls()
        if urls and all(url.isLocalFile() for url in urls):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        """Leave the native drop before any import or conversion dialog opens."""
        self.dragEnterEvent(event)
        if not event.isAccepted():
            return
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls()]
        event.acceptProposedAction()
        self.file_drop_filter.enqueue(paths)

    def _import_dropped_paths(self, paths: list[Path]) -> None:
        """Keep each experiment independent; IPF drops are source-only previews."""
        graph_paths = [path for path in paths if path.suffix.lower() in {".pxp", ".mdsg", ".ipf", ".h5xp"}]
        for path in graph_paths:
            if self.file_drop_filter.closed:
                return
            window = self.tool_manager.open_tool("pxp_viewer")
            window.file_drop_filter.enqueue([path])  # type: ignore[attr-defined]
        paths = [path for path in paths if path not in graph_paths]
        if not paths:
            return
        if len(paths) == 1 and paths[0].suffix.lower() in {
            SESSION_EXTENSION,
            *LEGACY_SESSION_EXTENSIONS,
        }:
            self.open_session(paths[0])
            return
        widget: Any = self.active_widget()
        if hasattr(widget, "import_dropped_files"):
            widget.import_dropped_files(paths)
        elif hasattr(widget, "import_files"):
            widget.import_files(paths)
        else:
            self.statusBar().showMessage("The active module does not accept files.", 4000)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Protect unsaved session changes on application close."""
        if not self.tool_manager.confirm_graph_shutdown():
            event.ignore()
            return
        self.undo_history.checkpoint()
        if not self.state.modified:
            self._finish_clean_shutdown()
            event.accept()
            return
        response = QMessageBox.question(
            self,
            "Unsaved Changes",
            "Save the current session before closing?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if response == QMessageBox.StandardButton.Save:
            if self.save_session():
                self._finish_clean_shutdown()
                event.accept()
            else:
                event.ignore()
        elif response == QMessageBox.StandardButton.Discard:
            self._finish_clean_shutdown()
            event.accept()
        else:
            event.ignore()
