"""Explicit, source-only Igor conversion preview; execution stays in Procedure Run."""
from __future__ import annotations

import json
from pathlib import Path
from threading import Event
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from materials_data_studio.tools.igor_input_resources import read_input_files
from materials_data_studio.tools.igor_procedure_conversion import (
    ConversionReport,
    analyze_procedures,
    build_python,
)
from materials_data_studio.ui.tasks import blocking_task

MAX_IPF_BYTES = 4 * 1024**2


def read_ipf(path: Path) -> str:
    """Read only the explicitly selected file, never #include dependencies."""
    if path.stat().st_size > MAX_IPF_BYTES:
        raise ValueError("Igor source exceeds the 4 MiB import limit")
    with path.open("rb") as stream:
        raw = stream.read(MAX_IPF_BYTES + 1)
    if len(raw) > MAX_IPF_BYTES:
        raise ValueError("Igor source exceeds the 4 MiB import limit")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    for encoding in ("utf-8-sig", "cp932", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("Igor source encoding could not be decoded")


def _reject_constant(value: str) -> Any:
    raise ValueError(f"Non-finite JSON argument is not supported: {value}")


class IgorConversionDialog(QDialog):
    """Original sources are copies; accepting inserts Python, never executes it."""

    def __init__(self, parent: QWidget, sources: list[tuple[str, str]], *,
                 selected: int = 0, replacing_python: bool = False,
                 input_resources: list[dict[str, str]] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Convert Igor / IPF to MDS Python — review before Run")
        self.resize(1080, 820)
        self.report: ConversionReport | None = None
        self.generated = ""
        self.replacing_python = replacing_python
        self.input_resources = [dict(item) for item in input_resources or []]
        self._reading_inputs = False
        self._pending_ipfs: list[Path] = []
        self.ipf_timer = QTimer(self)
        self.ipf_timer.setSingleShot(True)
        self.ipf_timer.timeout.connect(self.open_pending_ipfs)
        layout = QVBoxLayout(self)
        note = QLabel("Convert a copy of imported Igor source or .ipf files. Review diagnostics and Python, then insert into Procedure. Nothing is executed here. External file/network operations, includes and unsupported instructions are not run.")
        note.setWordWrap(True)
        layout.addWidget(note)
        row = QHBoxLayout()
        self.sources = QComboBox()
        self._empty_source_placeholder = not sources
        self.sources.setAccessibleName("Igor source to convert")
        for title, source in sources:
            self.sources.addItem(title, source)
        if not sources:
            self.sources.addItem("Paste Igor source below", "")
        self.sources.setCurrentIndex(max(0, min(selected, self.sources.count() - 1)))
        row.addWidget(self.sources, 1)
        self.open_button = QPushButton("Open .ipf…")
        self.open_button.clicked.connect(lambda _checked=False: self.open_ipf())
        row.addWidget(self.open_button)
        self.analyze_button = QPushButton("Analyze / Convert")
        self.analyze_button.clicked.connect(self.analyze)
        row.addWidget(self.analyze_button)
        layout.addLayout(row)
        input_row = QHBoxLayout()
        self.input_button = QPushButton("Input files…")
        self.input_button.setToolTip("Select the required input files once to prepare the virtual GlobalRoot in memory for isolated Run. No filesystem or network access is granted to the procedure.")
        self.input_button.clicked.connect(lambda _checked=False: self.open_inputs())
        input_row.addWidget(self.input_button)
        self.clear_inputs_button = QPushButton("Clear inputs")
        self.clear_inputs_button.clicked.connect(self.clear_inputs)
        input_row.addWidget(self.clear_inputs_button)
        self.input_summary = QLabel()
        self.input_summary.setWordWrap(True)
        self.input_summary.setTextFormat(Qt.TextFormat.PlainText)
        self.input_summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        input_row.addWidget(self.input_summary, 1)
        layout.addLayout(input_row)
        self.update_input_summary()
        form = QFormLayout()
        self.routine = QComboBox()
        self.routine.setAccessibleName("Entry routine")
        form.addRow("Entry routine", self.routine)
        self.arguments = QLineEdit("[]")
        self.arguments.setMaxLength(16384)
        self.arguments.setPlaceholderText('JSON argument list, e.g. [2.0, "root:signal"]')
        form.addRow("Arguments (JSON list)", self.arguments)
        self.parameter_hint = QLabel("Select a source and click Analyze / Convert.")
        self.parameter_hint.setWordWrap(True)
        self.parameter_hint.setTextFormat(Qt.TextFormat.PlainText)
        self.parameter_hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow(self.parameter_hint)
        layout.addLayout(form)
        self.pages = QTabWidget()
        self.source = QPlainTextEdit()
        self.source.setAccessibleName("Igor source copy")
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName("Converted MDS Python preview")
        self.diagnostics = QPlainTextEdit()
        self.diagnostics.setReadOnly(True)
        for edit in (self.source, self.preview, self.diagnostics):
            edit.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.pages.addTab(self.source, "Igor source (copy)")
        self.pages.addTab(self.preview, "MDS Python preview")
        self.pages.addTab(self.diagnostics, "Diagnostics / limits")
        layout.addWidget(self.pages, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.insert_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.insert_button.setText("Insert into Python (not run)")
        self.copy_button = self.buttons.addButton("Copy Python", QDialogButtonBox.ButtonRole.ActionRole)
        self.copy_button.clicked.connect(self.copy_python)
        self.buttons.accepted.connect(self.insert_python)
        self.buttons.rejected.connect(self.reject)
        for button in [*self.buttons.buttons(), self.open_button, self.analyze_button,
                       self.input_button, self.clear_inputs_button]:
            button.setAutoDefault(False)
            button.setDefault(False)
        layout.addWidget(self.buttons)
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(150)
        self.preview_timer.timeout.connect(self.refresh_preview)
        self.sources.currentIndexChanged.connect(self.select_source)
        self.source.textChanged.connect(self.invalidate)
        self.routine.currentIndexChanged.connect(self.select_routine)
        self.arguments.textChanged.connect(lambda: self.preview_timer.start())
        self.select_source()

    def update_input_summary(self) -> None:
        count = len(self.input_resources)
        self.clear_inputs_button.setEnabled(bool(count) and not self._reading_inputs)
        names = ", ".join(item["name"][:80] for item in self.input_resources[:5])
        if count > 5:
            names += f" … ({count - 5} more)"
        self.input_summary.setText(
            f"{count} prepared input(s) in virtual GlobalRoot: " + names
            if count else "Virtual GlobalRoot: select the required input files once here. File-reading macros can use only these prepared files."
        )

    def clear_inputs(self) -> None:
        if not self._reading_inputs:
            self.input_resources = []
            self.update_input_summary()

    def open_inputs(self, paths: list[Path] | None = None) -> None:
        if self._reading_inputs:
            return
        if paths is None:
            filenames, _ = QFileDialog.getOpenFileNames(
                self, "Prepare input text files (not executed)", "",
                "Text data (*.dat *.txt *.prf *.csv *.tsv *.xy);;All files (*)",
            )
            if not filenames:
                return
            paths = [Path(filename) for filename in filenames]
        if not paths:
            return
        self._reading_inputs = True
        self.input_button.setEnabled(False)
        self.update_input_summary()
        stop = Event()
        existing = [dict(item) for item in self.input_resources]
        try:
            prepared = blocking_task(
                self, "Prepare read-only Igor input snapshots",
                lambda: read_input_files(paths, existing, cancelled=stop), cancelled=stop,
            )
            if isValid(self):
                self.input_resources = prepared
                self.status.setText("Inputs prepared in memory only. Insert Python, then explicitly Run All to use them. No code has run.")
        except InterruptedError:
            if isValid(self):
                self.status.setText("Input preparation stopped; previously prepared inputs are unchanged.")
        except Exception as exc:
            if isValid(self):
                self.status.setText(f"Cannot prepare input files: {exc}")
        finally:
            if isValid(self):
                self._reading_inputs = False
                self.input_button.setEnabled(True)
                self.update_input_summary()

    def queue_ipf_paths(self, paths: list[Path]) -> None:
        self._pending_ipfs.extend(paths)
        self.ipf_timer.start(0)

    def open_pending_ipfs(self) -> None:
        """The modal source dialog must be visible before progress work starts."""
        while self._pending_ipfs:
            path = self._pending_ipfs.pop(0)
            self.open_ipf(path)
            if not isValid(self):
                return

    def invalidate(self) -> None:
        self.preview_timer.stop()
        self.report = None
        self.generated = ""
        self.preview.clear()
        self.diagnostics.clear()
        self.routine.clear()
        self.insert_button.setEnabled(False)
        self.copy_button.setEnabled(False)
        self.status.setText("Source changed. Click Analyze / Convert; no code has run.")

    def select_source(self, *_args: Any) -> None:
        self.source.setPlainText(str(self.sources.currentData() or ""))
        self.invalidate()
        self.pages.setCurrentIndex(0)

    def open_ipf(self, path: Path | None = None) -> None:
        if path is None:
            filename, _ = QFileDialog.getOpenFileName(self, "Open Igor procedure (not executed)", "", "Igor procedure (*.ipf);;Text (*.txt)")
            if not filename:
                return
            path = Path(filename)
        try:
            source = blocking_task(self, "Read Igor procedure source", lambda: read_ipf(path))
            if not isValid(self):
                return
            if self._empty_source_placeholder and not self.source.toPlainText():
                self.sources.setItemText(0, path.name)
                self.sources.setItemData(0, source)
                self.select_source()
            else:
                self.sources.addItem(path.name, source)
                self.sources.setCurrentIndex(self.sources.count() - 1)
            self._empty_source_placeholder = False
        except InterruptedError:
            if isValid(self):
                self.status.setText("Igor source loading stopped; no source was inserted or executed.")
        except Exception as exc:
            if isValid(self):
                self.status.setText(f"Cannot read Igor source: {exc}")

    def analyze(self) -> None:
        self.preview_timer.stop()
        source = self.source.toPlainText()
        self.invalidate()
        self.analyze_button.setEnabled(False)
        try:
            report = blocking_task(self, "Analyze Igor procedure (no execution)", lambda: analyze_procedures(source))
            if not isValid(self):
                return
            if source != self.source.toPlainText():
                self.status.setText("Source changed during analysis; analyze the new copy again.")
                return
            self.report = report
            self.routine.blockSignals(True)
            for routine in report.routines:
                state = "convertible subset" if routine.supported else "unsupported — see diagnostics"
                self.routine.addItem(f"{routine.name}({', '.join(routine.parameters)}) — {state}", routine.name)
            self.routine.blockSignals(False)
            index = next((i for i, routine in enumerate(report.routines) if routine.supported), 0)
            self.routine.setCurrentIndex(index)
            self.select_routine()
            self.pages.setCurrentIndex(1 if self.generated else 2)
        except InterruptedError:
            if isValid(self):
                self.status.setText("Analysis stopped; no code was inserted or executed.")
        except Exception as exc:
            if isValid(self):
                self.status.setText(f"Cannot convert Igor source: {exc}")
        finally:
            if isValid(self):
                self.analyze_button.setEnabled(True)

    def select_routine(self, *_args: Any) -> None:
        if self.report is None or self.routine.currentIndex() < 0:
            return
        routine = self.report.routines[self.routine.currentIndex()]
        self.parameter_hint.setText("Parameters: " + (", ".join(routine.parameters) or "none") + ". Fill required values in the JSON list; conversion does not validate your scientific model.")
        self.arguments.blockSignals(True)
        self.arguments.setText(json.dumps([routine.defaults.get(name) for name in routine.parameters], ensure_ascii=False))
        self.arguments.blockSignals(False)
        self.refresh_preview()

    def refresh_preview(self) -> bool:
        self.preview_timer.stop()
        self.generated = ""
        self.preview.clear()
        self.insert_button.setEnabled(False)
        self.copy_button.setEnabled(False)
        diagnostics = list(self.report.diagnostics) if self.report is not None else []
        if self.report is not None and self.routine.currentIndex() >= 0:
            diagnostics.extend(self.report.routines[self.routine.currentIndex()].diagnostics)
        lines = [f"{item.severity.upper()} · line {item.line}: {item.message}" for item in diagnostics]
        self.diagnostics.setPlainText("\n".join(dict.fromkeys(lines)) or "No static unsupported-syntax diagnostics for this routine. Review Python before Run. Runtime names, data shapes and scientific units still need verification.")
        if self.report is None or self.routine.currentIndex() < 0:
            self.status.setText("No convertible routine. Review the source and diagnostics.")
            return False
        routine = self.report.routines[self.routine.currentIndex()]
        try:
            arguments = json.loads(self.arguments.text(), parse_constant=_reject_constant)
            if not isinstance(arguments, list):
                raise ValueError("Arguments must be a JSON list")
            self.generated = build_python(self.report, routine.name, arguments)
        except (ValueError, TypeError, SyntaxError, NotImplementedError) as exc:
            self.status.setText(str(exc))
            return False
        self.preview.setPlainText(self.generated)
        self.insert_button.setEnabled(True)
        self.copy_button.setEnabled(True)
        self.status.setText("Converted subset only. Review all diagnostics, insert into Python, then explicitly press Run All. Original Igor source remains unchanged.")
        return True

    def copy_python(self) -> None:
        if self.refresh_preview():
            QApplication.clipboard().setText(self.generated)
            self.status.setText("Copied reviewed Python. Nothing was executed.")

    def insert_python(self) -> None:
        if not self.refresh_preview():
            return
        if self.replacing_python and QMessageBox.question(self, "Replace working Python source?", "Replace the current Python source with this converted copy? The original Igor source remains unchanged. This does not run the code.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.accept()
