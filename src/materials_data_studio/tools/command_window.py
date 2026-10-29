"""Dockable command completion/history and explicitly run Python procedures."""
from __future__ import annotations

import html
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from PySide6.QtCore import QProcess, QProcessEnvironment, QStringListModel, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QKeyEvent,
    QPainter,
    QSyntaxHighlighter,
    QTextBlock,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QCompleter,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from materials_data_studio.tools.command_engine import COMMAND_HELP, CommandEngine
from materials_data_studio.tools.command_services import DocumentServices
from materials_data_studio.tools.graph_document import (
    EditableGraphDocument,
    load_graph_document,
    save_graph_document,
)
from materials_data_studio.tools.pxp_model import PxpDocument
from materials_data_studio.ui.tasks import blocking_task


class CommandInput(QLineEdit):
    def __init__(self, execute: Any, parent: QWidget) -> None:
        super().__init__(parent)
        self.execute = execute
        self.history: list[str] = []
        self.position = 0
        self.setPlaceholderText('Make/D/N=100 signal; signal=sin(p/10); Display signal')

    def keyPressEvent(self, event: QKeyEvent) -> None:
        completer = self.completer()
        popup = completer.popup() if completer is not None else None
        if event.key() in {Qt.Key.Key_Up, Qt.Key.Key_Down} and not (popup and popup.isVisible()):
            self.position = max(0, min(len(self.history), self.position + (-1 if event.key() == Qt.Key.Key_Up else 1)))
            self.setText(self.history[self.position] if self.position < len(self.history) else "")
            return
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter} and not (popup and popup.isVisible()):
            text = self.text().strip()
            if text:
                self.history.append(text)
                self.position = len(self.history)
                self.clear()
                self.execute(text)
            return
        super().keyPressEvent(event)


class TokenCompleter(QCompleter):
    """Complete one token without destroying preceding statements/arguments."""
    def splitPath(self, path: str) -> list[str]:
        import re
        return [re.split(r"[;,\s]", path)[-1]]

    def pathFromIndex(self, index: Any) -> str:
        import re
        widget = self.widget()
        candidate = super().pathFromIndex(index)
        if not isinstance(widget, QLineEdit):
            return candidate
        text, position = widget.text(), widget.cursorPosition()
        match = re.search(r"[^;,\s]*$", text[:position])
        return text[:match.start() if match else position] + candidate + text[position:]


class SyntaxColors(QSyntaxHighlighter):
    def highlightBlock(self, text: str) -> None:
        import re
        for pattern, color in ((r'\b(?:from|import|def|for|in|if|else|with|as|return|print|class|try|except)\b', "#8b5cf6"),
                               (r'"[^"\n]*"|\x27[^\x27\n]*\x27', "#16a085"), (r"#.*$", "#6b7280")):
            style = QTextCharFormat()
            style.setForeground(QColor(color))
            for match in re.finditer(pattern, text):
                self.setFormat(match.start(), len(match[0]), style)


class LineNumbers(QWidget):
    def __init__(self, editor: CodeEditor) -> None:
        super().__init__(editor)
        self.editor = editor

    def paintEvent(self, _event: Any) -> None:
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        block = self.editor.firstVisibleBlock()
        while block.isValid():
            top = int(self.editor.blockBoundingGeometry(block).translated(self.editor.contentOffset()).top())
            if top > self.height():
                break
            if block.isVisible():
                painter.drawText(0, top, self.width() - 7, self.editor.fontMetrics().height(), Qt.AlignmentFlag.AlignRight, str(block.blockNumber() + 1))
            block = block.next()


class CodeEditor(QPlainTextEdit):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        from PySide6.QtGui import QFontDatabase
        self.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.gutter = LineNumbers(self)
        self.blockCountChanged.connect(self.update_gutter)
        self.updateRequest.connect(lambda *_: self.gutter.update())
        self.highlighter = SyntaxColors(self.document())
        self.update_gutter()

    def update_gutter(self, *_args: Any) -> None:
        width = 16 + self.fontMetrics().horizontalAdvance("9") * len(str(self.blockCount()))
        self.setViewportMargins(width, 0, 0, 0)
        self.gutter.setGeometry(0, 0, width, self.height())

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self.update_gutter()


def cleanup_work(work: tempfile.TemporaryDirectory, attempt: int = 0) -> None:
    """Windows may retain a killed process's log handle until finished returns."""
    from materials_data_studio.tools.procedure_sandbox import cleanup_profile
    try:
        cleanup_profile(Path(work.name))
    except Exception:
        if attempt < 6:
            QTimer.singleShot(250, lambda: cleanup_work(work, attempt + 1))
            return
        import logging
        logging.getLogger(__name__).warning("Sandbox profile cleanup failed", exc_info=True)
    work.cleanup()
    if Path(work.name).exists():
        if attempt < 6:
            QTimer.singleShot(250, lambda: cleanup_work(work, attempt + 1))
        else:
            import logging
            logging.getLogger(__name__).warning("Procedure temporary directory could not be fully cleaned: %s", work.name)


class CommandWorkspace:
    def __init__(self, window: Any) -> None:
        self.window = window
        self.engine: CommandEngine | None = None
        self.suppress_recording = False
        self.loading = False
        self.process: QProcess | None = None
        self.work: tempfile.TemporaryDirectory | None = None
        self.cancelled = False
        self.run_editor: Any = None
        self.run_revision = -1
        self.run_state: Any = None
        self.log_position = 0
        self.trusted = False
        self.dock_areas: dict[QDockWidget, Qt.DockWidgetArea] = {}
        self.dock_transitions: dict[QDockWidget, int] = {}
        self.procedure_log_block: QTextBlock | None = None
        self.procedure_log_count = 0
        self.parameter_dialog: Any = None
        self.interaction_controller: Any = None
        # Animated dock geometry can outlive a native screen/DPI transition.
        window.setDockOptions(window.dockOptions() & ~window.DockOption.AnimatedDocks)
        self.console_dock = QDockWidget("Command Window", window)
        self.console_dock.setObjectName("CommandWindow")
        self.install_dock_controls(self.console_dock)
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 6, 8, 6)
        self.output = QTextBrowser()
        self.output.setOpenExternalLinks(False)
        self.output.setMinimumHeight(70)
        layout.addWidget(self.output)
        actions = QHBoxLayout()
        copy = QPushButton("Copy output")
        copy.clicked.connect(self.copy_output)
        clear = QPushButton("Clear output")
        clear.clicked.connect(self.output.clear)
        actions.addWidget(copy)
        actions.addWidget(clear)
        convert = QPushButton("Selection to Python")
        convert.clicked.connect(self.selection_to_python)
        actions.addWidget(convert)
        actions.addStretch()
        layout.addLayout(actions)
        self.input = CommandInput(self.execute, panel)
        self.input.setObjectName("CommandInput")
        self.completer = TokenCompleter(sorted(COMMAND_HELP), self.input)
        self.completer.setMaxVisibleItems(8)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.input.setCompleter(self.completer)
        self.input.textChanged.connect(self.update_hint)
        self.completer.highlighted.connect(lambda value: self.update_hint(value if value in COMMAND_HELP else self.input.text()))
        layout.addWidget(self.input)
        self.hint = QLabel("Type a command for syntax help · ↑/↓ history · Undo/Redo are local to this editor")
        self.hint.setWordWrap(True)
        self.hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.hint)
        self.console_dock.setWidget(panel)
        window.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.console_dock)
        self.procedure_dock = QDockWidget("Procedure Window", window)
        self.procedure_dock.setObjectName("ProcedureWindow")
        self.install_dock_controls(self.procedure_dock)
        self.procedure_dock.setMinimumWidth(400)
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self.tabs = QTabWidget()
        self.code = CodeEditor(panel)
        self.code.setMinimumHeight(240)
        self.tabs.addTab(self.code, "Python · procedure.py")
        layout.addWidget(self.tabs)
        note = QLabel("Isolated scientific Python · network and Python file I/O disabled · NumPy, selected SciPy, math, statistics and mds only. Windows AppContainer required; no unrestricted fallback. Imported Igor source is read-only.")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QHBoxLayout()
        self.run_all = QPushButton("Run All")
        self.run_selection = QPushButton("Run Selection")
        self.stop_button = QPushButton("Stop")
        self.stop_button.setEnabled(False)
        self.run_all.clicked.connect(lambda: self.start(self.code.toPlainText()))
        self.run_selection.clicked.connect(self.start_selection)
        self.stop_button.clicked.connect(self.stop)
        for button in (self.run_all, self.run_selection, self.stop_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        buttons = QHBoxLayout()
        for name, callback in (("Open .py…", self.open_python), ("Save .py…", self.save_python)):
            button = QPushButton(name)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.convert_button = QPushButton("Convert Igor / IPF…")
        self.convert_button.setToolTip("Review imported Igor procedures or an IPF file as MDS Python; conversion never executes code")
        self.convert_button.clicked.connect(self.convert_igor)
        convert_row = QHBoxLayout()
        convert_row.addWidget(self.convert_button)
        self.parameters_button = QPushButton("Macro parameters…")
        self.parameters_button.clicked.connect(self.show_macro_parameters)
        convert_row.addWidget(self.parameters_button)
        layout.addLayout(convert_row)
        self.procedure_dock.setWidget(panel)
        window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.procedure_dock)
        self.procedure_dock.hide()
        self.view_menu = menu = window.menuBar().addMenu("View")
        menu.addAction(self.console_dock.toggleViewAction())
        menu.addAction(self.procedure_dock.toggleViewAction())
        for dock in (self.console_dock, self.procedure_dock):
            self.dock_areas[dock] = window.dockWidgetArea(dock)
            if hasattr(dock, "dockLocationChanged"):
                dock.dockLocationChanged.connect(lambda area, target=dock: self.remember_dock_area(target, area))
            action = menu.addAction("Open " + dock.windowTitle() + " in separate window")
            action.triggered.connect(lambda _checked=False, target=dock: self.float_dock(target))
            action = menu.addAction("Dock back " + dock.windowTitle())
            action.triggered.connect(lambda _checked=False, target=dock: self.dock_back(target))
        self.procedure_help: Any = None
        self.save_timer = QTimer(window)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(500)
        self.save_timer.timeout.connect(self.flush)
        self.code.textChanged.connect(lambda: self.save_timer.start() if not self.loading else None)
        self.tabs.currentChanged.connect(self.update_buttons)

    def float_dock(self, dock: QDockWidget) -> None:
        self.dock_transitions[dock] = self.dock_transitions.get(dock, 0) + 1
        self.remember_dock_area(dock, self.window.dockWidgetArea(dock))
        dock.setWindowState(Qt.WindowState.WindowNoState)
        dock.setFloating(True)
        dock.showNormal()
        dock.resize(880, 520 if dock is self.console_dock else 700)
        dock.raise_()
        dock.activateWindow()

    def remember_dock_area(self, dock: QDockWidget, area: Qt.DockWidgetArea) -> None:
        if area != Qt.DockWidgetArea.NoDockWidgetArea:
            self.dock_areas[dock] = area

    def dock_back(self, dock: QDockWidget) -> None:
        """Return through Qt's dock layout, not by changing native window flags.

        Clear minimized/maximized state before docking. Reparenting the native
        widget directly bypasses QDockWidget's private layout state and can leave
        a logically visible panel hidden. Keep panel/editor/process identity.
        """
        fallback = Qt.DockWidgetArea.BottomDockWidgetArea if dock is self.console_dock else Qt.DockWidgetArea.RightDockWidgetArea
        area = self.dock_areas.get(dock, fallback)
        generation = self.dock_transitions.get(dock, 0) + 1
        self.dock_transitions[dock] = generation
        dock.setWindowState(Qt.WindowState.WindowNoState)
        dock.hide()
        dock.setFloating(False)
        # Remove tab/placeholder membership only AFTER the standard undock
        # transition has finished. addDockWidget owns the child reparenting.
        self.window.removeDockWidget(dock)
        self.window.addDockWidget(area, dock)
        dock.show()
        dock.raise_()  # Also selects a tabified dock instead of leaving it hidden.
        dock.updateGeometry()
        horizontal = area in {Qt.DockWidgetArea.LeftDockWidgetArea, Qt.DockWidgetArea.RightDockWidgetArea}
        extent = self.window.width() if horizontal else self.window.height()
        self.window.resizeDocks([dock], [max(240, round(extent * .32))], Qt.Orientation.Horizontal if horizontal else Qt.Orientation.Vertical)
        if self.window.isMinimized():
            self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()
        # Finish layout after the originating button/menu event. Never revive
        # a panel hidden, floated, or re-docked by a subsequent user operation.
        def settle() -> None:
            from shiboken6 import isValid
            if (not isValid(dock) or not isValid(self.window)
                    or self.dock_transitions.get(dock) != generation
                    or dock.isFloating() or dock.isHidden() or not self.window.isVisible()):
                return
            self.window.layout().activate()
            self.window.resizeDocks([dock], [max(240, round(extent * .32))], Qt.Orientation.Horizontal if horizontal else Qt.Orientation.Vertical)
            dock.updateGeometry()
        QTimer.singleShot(0, settle)

    def show_procedure_help(self) -> None:
        from materials_data_studio.tools.procedure_help import ProcedureHelpDialog
        if self.procedure_help is None:
            self.procedure_help = ProcedureHelpDialog(self.window)
        self.procedure_help.show()
        self.procedure_help.raise_()
        self.procedure_help.activateWindow()

    def install_dock_controls(self, dock: QDockWidget) -> None:
        title = QWidget(dock)
        row = QHBoxLayout(title)
        row.setContentsMargins(8, 3, 4, 3)
        label = QLabel(dock.windowTitle())
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(label)
        row.addStretch()
        toggle = QToolButton(title)
        toggle.setProperty("mdsDockToggle", True)
        toggle.setText("↗ Separate window")
        toggle.setToolTip("Open as a movable, resizable window. Use Dock back to return; you can also drag this title bar.")
        toggle.clicked.connect(lambda: self.dock_back(dock) if dock.isFloating() else self.float_dock(dock))
        dock.topLevelChanged.connect(lambda floating: toggle.setText("↙ Dock back" if floating else "↗ Separate window"))
        row.addWidget(toggle)
        close = QToolButton(title)
        close.setText("×")
        close.setToolTip("Hide window (reopen from View)")
        close.clicked.connect(dock.hide)
        row.addWidget(close)
        dock.setTitleBarWidget(title)

    def update_hint(self, text: str) -> None:
        text = text.rsplit(";", 1)[-1].lstrip()
        name = text.split("/", 1)[0].split(" ", 1)[0].lower()
        entries = [value for key, value in COMMAND_HELP.items() if key.lower().startswith(name)]
        self.hint.setText(entries[0] if entries and name else "Type a command for syntax help · ↑/↓ history")

    def copy_output(self) -> None:
        from PySide6.QtWidgets import QApplication
        selected = self.output.textCursor().selectedText()
        QApplication.clipboard().setText(selected.replace("\u2029", "\n") or self.output.toPlainText())

    def selection_to_python(self) -> None:
        if self.engine is None:
            return
        from materials_data_studio.tools.command_python import to_python
        text = self.output.textCursor().selectedText().replace("\u2029", "\n")
        commands = "\n".join(line[2:] if line.startswith("> ") else line for line in text.splitlines())
        if not commands.strip():
            self.write("Select command lines in the output first.", "error")
            return
        try:
            source = to_python(commands, self.engine.services)
            self.tabs.setCurrentIndex(0)
            self.code.appendPlainText("\n# Converted selection (review before Run)\n" + source)
            self.procedure_dock.show()
        except Exception as exc:
            self.write(f"Python conversion: {exc}", "error")

    def write(self, text: str, kind: str = "result") -> None:
        self.procedure_log_block = None
        self.procedure_log_count = 0
        color = {"error": "#dc6b6b", "command": "#568ed2", "import": "#888888", "gui": "#399b88"}.get(kind, "")
        self.output.append(f'<pre style="margin:2px;color:{color}">{html.escape(text)}</pre>')

    def bind(self) -> None:
        if self.is_running():
            self.stop()
        self.igor_input_resources: list[dict[str, str]] = []
        from shiboken6 import isValid
        if self.parameter_dialog is not None and isValid(self.parameter_dialog):
            self.parameter_dialog.close()
        self.parameter_dialog = None
        editor = self.window.editor
        if editor is None:
            return
        self.engine = CommandEngine(DocumentServices(editor.document))
        self.trusted = False
        editor.observers.append(self.record)
        self.output.clear()
        self.procedure_log_block = None
        self.procedure_log_count = 0
        self.input.history.clear()
        self.input.position = 0
        if editor.document.source.command_history:
            self.write("Imported Igor history (not executed)", "import")
            for line in editor.document.source.command_history:
                self.write(line, "import")
        self.loading = True
        self.code.setPlainText(editor.document.current.environment.get("python_source", ""))
        while self.tabs.count() > 1:
            page = self.tabs.widget(1)
            self.tabs.removeTab(1)
            if page is not None:
                page.deleteLater()
        for i, source in enumerate(editor.document.source.procedures):
            imported = CodeEditor(self.tabs)
            imported.setPlainText(source)
            imported.setReadOnly(True)
            self.tabs.addTab(imported, f"Igor source {i + 1} · read-only")
        self.loading = False
        self.update_buttons()
        self.update_candidates()

    def update_candidates(self) -> None:
        if self.window.editor is None:
            return
        source = self.window.editor.document.render_source()
        candidates = set(COMMAND_HELP)
        candidates.update(w.igor_path for w in source.waves)
        candidates.update(w.name for w in source.waves if w.name.isidentifier())
        candidates.update(g.name for g in source.graphs)
        model = self.completer.model()
        if isinstance(model, QStringListModel):
            model.setStringList(sorted(candidates))

    def ensure_document(self) -> None:
        if self.window.editor is None:
            source = PxpDocument("new", "Untitled.mdsg", "", 0, "", "MDS", "", status="FULL")
            self.window.bind_graph_document(EditableGraphDocument.from_pxp(source))

    def record(self, events: list[str]) -> None:
        if not self.suppress_recording:
            if events and events[0] == "// GUI Edit Python procedure":
                block = self.procedure_log_block
                if (block is not None and block.isValid()
                        and block == self.output.document().lastBlock()
                        and block.text().startswith(events[0])):
                    self.procedure_log_count += 1
                    cursor = QTextCursor(block)
                    cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
                    cursor.insertText(f"{events[0]} ({self.procedure_log_count} consecutive updates)")
                else:
                    self.write(events[0], "gui")
                    self.procedure_log_block = self.output.document().lastBlock()
                    self.procedure_log_count = 1
                return
            self.write("\n".join(events), "gui")

    def context(self) -> None:
        if self.engine is None:
            return
        kind, name = self.window._selection
        if kind in {"graph", "table"}:
            self.engine.services.context[kind] = name
            self.engine.services.context["selected_kind"] = kind

    def select_result(self) -> None:
        if self.engine is None:
            return
        context = self.engine.services.context
        kind = context.get("selected_kind", "graph" if context.get("graph") else "table")
        name = context.get(kind)
        source = self.window.editor.document.render_source()
        if kind == "graph" and not any(g.name == name for g in source.graphs):
            return
        if kind == "table" and not any(t.name == name for t in source.table_definitions):
            return
        if name:
            selection = (kind, name)
            flags = Qt.MatchFlag.MatchContains | Qt.MatchFlag.MatchRecursive
            item = next((item for item in self.window.tree.findItems(name, flags)
                         if item.data(0, Qt.ItemDataRole.UserRole) == selection), None)
            if item is None:
                return
            if self.window.tree.currentItem() is item:
                self.window._selection = selection
                self.window._show_selection()
            else:
                # Use the same path as an explicit browser click. Drawing a
                # worker's last graph alone leaves the browser on the first
                # graph selected when the new document structure was bound.
                self.window.tree.setCurrentItem(item)
            actual = self.window._selection
            self.window.view_state["selected"] = actual
            if actual != selection:
                # A pending property form can decline the selection change.
                # Keep the browser and persisted view state on that same graph.
                previous = next((item for item in self.window.tree.findItems(actual[1], flags)
                                 if item.data(0, Qt.ItemDataRole.UserRole) == actual), None)
                if previous is not None:
                    blocked = self.window.tree.blockSignals(True)
                    self.window.tree.setCurrentItem(previous)
                    self.window.tree.blockSignals(blocked)

    def execute(self, source: str) -> None:
        self.ensure_document()
        self.flush()
        editor = self.window.editor
        assert self.engine is not None and editor is not None
        self.context()
        self.write("> " + source, "command")
        if source.strip().lower() in {"undo", "redo"}:
            self.suppress_recording = True
            try:
                getattr(editor.history, source.strip().lower())()
            finally:
                self.suppress_recording = False
            self.sync_source()
            return
        before = editor.document.current
        result = self.engine.execute(source)
        after = editor.document.current
        editor.document.current = before
        if result.success:
            if before != after:
                after = after.clone()
                commands = list(before.environment.get("command_recipe", []))
                commands.append({"source": source, "replayable": True, "interpreter": "MDS command subset"})
                after.environment["command_recipe"] = commands
            self.suppress_recording = True
            try:
                editor.commit(before, after, "Command: " + source[:60])
            finally:
                self.suppress_recording = False
            self.select_result()
        self.write(result.message or ("OK" if result.success else "Error"), "result" if result.success else "error")
        for warning in result.warnings:
            self.write("Warning: " + warning, "error")

    def flush(self) -> None:
        self.save_timer.stop()
        if self.loading or self.window.editor is None:
            return
        editor = self.window.editor
        source = self.code.toPlainText()
        if source == editor.document.current.environment.get("python_source", ""):
            return
        before = editor.document.current
        after = before.clone()
        after.environment["python_source"] = source
        editor.commit(before, after, "Edit Python procedure")

    def sync_source(self) -> None:
        self.update_candidates()
        self.update_buttons()
        if self.window.editor is not None and not self.save_timer.isActive():
            source = self.window.editor.document.current.environment.get("python_source", "")
            if source != self.code.toPlainText():
                self.loading = True
                self.code.setPlainText(source)
                self.loading = False

    def update_buttons(self, *_args: Any) -> None:
        enabled = self.tabs.currentIndex() == 0 and not self.is_running()
        self.run_all.setEnabled(enabled)
        self.run_selection.setEnabled(enabled)
        self.stop_button.setEnabled(self.is_running())
        self.convert_button.setEnabled(not self.is_running())
        editor = self.window.editor
        self.parameters_button.setEnabled(bool(editor is not None and editor.document.current.environment.get("igor_parameter_panels")))
        from shiboken6 import isValid
        if self.parameter_dialog is not None and isValid(self.parameter_dialog):
            self.parameter_dialog.refresh()

    def show_macro_parameters(self) -> None:
        from shiboken6 import isValid

        from materials_data_studio.tools.igor_parameter_panel import IgorParameterDialog
        if self.window.editor is None:
            return
        if self.parameter_dialog is None or not isValid(self.parameter_dialog):
            self.parameter_dialog = IgorParameterDialog(self)
        self.parameter_dialog.refresh()
        self.parameter_dialog.show()
        self.parameter_dialog.raise_()
        self.parameter_dialog.activateWindow()

    def convert_igor(self) -> None:
        sources = [(self.tabs.tabText(i), page.toPlainText()) for i in range(1, self.tabs.count())
                   if isinstance(page := self.tabs.widget(i), QPlainTextEdit)]
        self._show_igor_conversion(sources, selected=max(0, self.tabs.currentIndex() - 1))

    def convert_ipf_paths(self, paths: list[Path]) -> None:
        """Preview explicitly dropped source files; Analyze and Run stay explicit."""
        if paths:
            self._show_igor_conversion([], paths=paths)

    def _show_igor_conversion(self, sources: list[tuple[str, str]], *,
                              selected: int = 0, paths: list[Path] | None = None) -> None:
        if self.is_running():
            self.write("Stop the current Procedure before replacing its source.", "error")
            return
        from PySide6.QtWidgets import QDialog
        from shiboken6 import isValid

        from materials_data_studio.tools.igor_conversion_dialog import IgorConversionDialog
        self.flush()
        dialog = IgorConversionDialog(
            self.window, sources, selected=selected,
            replacing_python=bool(self.code.toPlainText().strip()),
            input_resources=getattr(self, "igor_input_resources", []),
        )
        try:
            if paths:
                dialog.queue_ipf_paths(paths)
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
            if not isValid(self.window) or not isValid(dialog):
                return
            if accepted:
                self.ensure_document()
                self.igor_input_resources = [dict(item) for item in dialog.input_resources]
                self.code.setPlainText(dialog.generated)
                self.flush()
                self.tabs.setCurrentIndex(0)
                self.procedure_dock.show()
                self.procedure_dock.raise_()
                self.write("Converted Igor source inserted as Python; not executed. Review the code and diagnostics, then use Run All explicitly.")
        finally:
            if isValid(dialog):
                dialog.deleteLater()

    def start_selection(self) -> None:
        cursor = self.code.textCursor()
        if not cursor.hasSelection():
            self.write("Select Python code to run.", "error")
            return
        start = self.code.document().findBlock(cursor.selectionStart()).blockNumber()
        self.start("\n" * start + cursor.selectedText().replace("\u2029", "\n"))

    def is_running(self) -> bool:
        return getattr(self, "preparing", False) or (self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning)

    def start(self, source: str) -> None:
        if self.is_running() or self.tabs.currentIndex() != 0:
            return
        from materials_data_studio.tools.procedure_policy import validate
        try:
            validate(source)
        except (ValueError, SyntaxError) as exc:
            self.write(f"Isolated Python: {exc}", "error")
            return
        self.ensure_document()
        self.flush()
        self.context()
        self.work = tempfile.TemporaryDirectory(prefix="mds-procedure-", ignore_cleanup_errors=True)
        root = Path(self.work.name)
        (root / "input").mkdir()
        (root / "output").mkdir()
        assert self.engine is not None
        self.run_editor = self.window.editor
        self.run_revision = self.run_editor.history.index()
        self.run_state = self.run_editor.document.current
        document = replace(self.run_editor.document, current=self.run_editor.document.current.clone())
        # Prepared file bodies never enter the MDSG snapshot/history. The fixed
        # sidecar is read only by trusted worker bootstrap, before user guards.
        document.current.environment.pop("igor_input_resources", None)
        prepared_inputs = [dict(item) for item in getattr(self, "igor_input_resources", [])]
        def prepare_snapshot() -> None:
            save_graph_document(root / "input/input.mdsg", document)
            if prepared_inputs:
                from materials_data_studio.tools.igor_input_resources import write_input_resources
                write_input_resources(root / "input/igor_inputs.json", prepared_inputs)
        self.preparing = True
        self.update_buttons()
        self.write("Preparing an immutable procedure snapshot… First use also prepares a private isolated runtime; this can take longer.")
        try:
            blocking_task(self.window, "Prepare isolated Python snapshot", prepare_snapshot, allow_cancel=False)
        except Exception as exc:
            self.write(f"Cannot prepare isolated Python: {exc}", "error")
            self.preparing = False
            self.cleanup()
            return
        finally:
            self.preparing = False
        from materials_data_studio.tools.igor_dialogs import IgorInteractionController
        from materials_data_studio.tools.procedure_interaction import ProcedureInteractionSession
        try:
            session = ProcedureInteractionSession(root)
            self.interaction_controller = IgorInteractionController(self, session, prepared_inputs)
        except Exception as exc:
            self.write(f"Cannot prepare isolated macro interactions: {exc}", "error")
            self.cleanup()
            return
        (root / "input/procedure.py").write_text(source, encoding="utf-8")
        run_context = {**self.engine.services.context, "igor_interactive": True}
        (root / "input/procedure.json").write_text(json.dumps(run_context), encoding="utf-8")
        self.cancelled = False
        self.process = QProcess(self.window)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONIOENCODING", "utf-8")
        environment.insert("PYTHONUNBUFFERED", "1")
        self.process.setProcessEnvironment(environment)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(self.process_error)
        self.log_position = 0
        self.log_timer = QTimer(self.process)
        self.log_timer.setInterval(150)
        self.log_timer.timeout.connect(self.read_log)
        self.log_timer.start()
        args = [] if getattr(sys, "frozen", False) else ["-m", "materials_data_studio"]
        args += ["--procedure-broker", str(root)]
        self.process.start(sys.executable, args)
        self.update_buttons()
        self.write("Starting isolated Python; no unrestricted fallback. Successful changes commit as one Undo step.")

    def read_output(self) -> None:
        if self.process is not None:
            self.write(bytes(self.process.readAllStandardOutput().data()).decode("utf-8", errors="replace"))

    def read_log(self) -> None:
        if self.interaction_controller is not None:
            self.interaction_controller.poll()
        if self.work is not None:
            path = Path(self.work.name) / "output/procedure.log"
            if path.exists():
                with path.open(encoding="utf-8", errors="replace") as stream:
                    stream.seek(self.log_position)
                    content = stream.read()
                    self.log_position = stream.tell()
                if content:
                    self.write(content)

    def stop(self) -> None:
        if self.is_running() and self.process is not None:
            self.cancelled = True
            if self.interaction_controller is not None:
                self.interaction_controller.close()
                self.interaction_controller = None
            self.process.kill()

    def process_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self.write("Python worker could not start.", "error")
            self.cleanup()

    def finished(self, code: int, _status: Any) -> None:
        self.read_output()
        self.read_log()
        show_panels = False
        try:
            if self.cancelled:
                self.write("Stopped; document changes discarded.")
            elif code != 0:
                self.write("Procedure failed; document changes discarded.", "error")
            elif self.window.editor is not self.run_editor or self.run_editor.history.index() != self.run_revision or self.run_editor.document.current is not self.run_state:
                self.write("Document changed during execution; stale worker results discarded.", "error")
            elif self.work is not None:
                root = Path(self.work.name)
                result = blocking_task(self.window, "Read isolated Python result", lambda: load_graph_document(root / "output/output.mdsg"), allow_cancel=False)
                if self.window.editor is not self.run_editor or self.run_editor.history.index() != self.run_revision or self.run_editor.document.current is not self.run_state:
                    self.write("Document changed while reading results; stale worker results discarded.", "error")
                    return
                # Reuse unchanged immutable arrays rather than doubling Undo memory.
                originals = {w.id: w for w in self.run_editor.document.render_source().waves}
                if result.current.waves is not None:
                    import numpy as np
                    result.current.waves = tuple(originals[w.id] if w.id in originals and replace(w, data=originals[w.id].data) == originals[w.id] and np.array_equal(w.data, originals[w.id].data, equal_nan=w.data.dtype.kind not in "US") else w for w in result.current.waves)
                self.run_editor.commit(self.run_state, result.current, "Run Python procedure")
                show_panels = bool(result.current.environment.get("igor_parameter_panels"))
                if self.engine is not None:
                    self.engine.services.context = json.loads((root / "output/output.json").read_text(encoding="utf-8"))
                    self.engine.services.context.pop("igor_interactive", None)
                self.select_result()
                self.write("Python procedure completed.")
        except Exception as exc:
            self.write(f"Cannot apply worker result: {exc}", "error")
        finally:
            self.cleanup()
            if show_panels:
                self.show_macro_parameters()

    def cleanup(self) -> None:
        if self.interaction_controller is not None:
            self.interaction_controller.close()
        self.interaction_controller = None
        if self.process is not None:
            self.process.deleteLater()
        self.process = None
        if self.work is not None:
            cleanup_work(self.work)
        self.work = None
        self.update_buttons()

    def open_python(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self.window, "Open Python source (not executed)", "", "Python (*.py)")
        if path:
            try:
                self.ensure_document()
                self.code.setPlainText(Path(path).read_text(encoding="utf-8-sig"))
                self.trusted = False
                self.tabs.setCurrentIndex(0)
            except Exception as exc:
                self.write(str(exc), "error")

    def save_python(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self.window, "Save Python source", "procedure.py", "Python (*.py)")
        if path:
            try:
                Path(path).write_text(self.code.toPlainText(), encoding="utf-8")
            except Exception as exc:
                self.write(str(exc), "error")
