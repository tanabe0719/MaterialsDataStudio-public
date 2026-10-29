"""Scientific wave operation forms sharing the transactional Command service."""
from __future__ import annotations

import json
import re
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
)

from materials_data_studio.tools.command_language import reference


def object_name(value: str) -> str:
    """Quote individual path segments after rejecting command delimiters."""
    path = reference(value).path
    return ":".join(part if re.fullmatch(r"[A-Za-z_]\w*", part) else "'" + part + "'" for part in path.split(":"))


class WaveOperationDialog(QDialog):
    """Preview a bounded operation and apply it as one undoable transaction."""

    def __init__(self, parent: Any, document: Any, operation: str) -> None:
        super().__init__(parent)
        self.operation = operation
        self.command = ""
        self.setWindowTitle(operation + " Waves")
        self.resize(700, 600)
        layout = QVBoxLayout(self)
        self.waves = QListWidget()
        self.waves.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        for wave in document.render_source().waves:
            self.waves.addItem(f"{wave.igor_path}  {wave.shape}")
            self.waves.item(self.waves.count() - 1).setData(Qt.ItemDataRole.UserRole, wave.igor_path)
        layout.addWidget(QLabel("Select source waves (list order is output order). Original imported files are preserved."))
        layout.addWidget(self.waves)
        form = QFormLayout()
        layout.addLayout(form)
        self.destination = QLineEdit("combined" if operation == "Concatenate" else "column")
        self.overwrite = QCheckBox("Overwrite working destinations")
        self.axis = QSpinBox()
        self.axis.setRange(0, 3)
        self.promote = QCheckBox("Promote equal dimensions (Igor default)")
        self.promote.setChecked(True)
        self.copy_labels = QCheckBox("Copy dimension labels / use source names")
        self.copy_scale = QCheckBox("Copy scaling and dimension labels")
        self.copy_note = QCheckBox("Copy wave note")
        self.copy_note.setChecked(True)
        self.copy_scale.setChecked(True)
        self.up, self.down, self.samples, self.output_dim = QSpinBox(), QSpinBox(), QSpinBox(), QSpinBox()
        for spin in (self.up, self.down):
            spin.setRange(1, 10000)
            spin.setValue(1)
        self.samples.setRange(3, 10001)
        self.samples.setSingleStep(2)
        self.samples.setValue(21)
        self.output_dim.setRange(1, 3)
        self.window_kind = QComboBox()
        self.window_kind.addItems(["Hanning", "Hamming", "None", "Bartlett", "Blackman"])
        self.end = QComboBox()
        self.end.addItems(["Bounce", "Wrap", "Zero", "Repeat"])
        self.end.setCurrentIndex(2)
        self.note = QPlainTextEdit()
        self.append_note = QCheckBox("Append note (CR separator); unchecked replaces the note")
        self.rate = QLineEdit()
        self.same = QLineEdit()
        self.coefficients = QLineEdit()
        self.label_index = QSpinBox()
        self.label_index.setRange(-1, 9999999)
        self.label = QLineEdit()
        if operation == "Concatenate":
            form.addRow("Destination", self.destination)
            form.addRow(self.promote)
            form.addRow("Concatenation dimension (promotion off)", self.axis)
            form.addRow(self.copy_labels)
            form.addRow(self.overwrite)
        elif operation == "SplitWave":
            form.addRow("Output base name", self.destination)
            form.addRow("Output dimensions", self.output_dim)
            form.addRow(self.copy_scale)
            form.addRow(self.copy_note)
            form.addRow(self.overwrite)
        elif operation == "Resample":
            form.addRow("Interpolation factor", self.up)
            form.addRow("Decimation factor", self.down)
            form.addRow("Dimension", self.axis)
            form.addRow("Target sample rate (optional)", self.rate)
            form.addRow("Match wave sample rate (optional)", self.same)
            form.addRow("Reconstruction input samples (odd)", self.samples)
            form.addRow("Coefficient window", self.window_kind)
            form.addRow("End handling", self.end)
            form.addRow("External coefficient wave (optional)", self.coefficients)
            layout.addWidget(QLabel("Changes working waves in place. Linear interpolation: N=3, window=None, down=1."))
        elif operation == "Note":
            form.addRow("Note text", self.note)
            form.addRow(self.append_note)
        else:
            form.addRow("Dimension", self.axis)
            form.addRow("Element index (-1 names dimension)", self.label_index)
            form.addRow("Label", self.label)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMaximumHeight(80)
        layout.addWidget(self.preview)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        preview = buttons.addButton("Preview result", QDialogButtonBox.ButtonRole.ActionRole)
        preview.clicked.connect(self.preview_result)
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.waves.itemSelectionChanged.connect(self.selection_changed)
        self.document = document
        self.append_note.toggled.connect(lambda checked: self.note.clear() if checked else self.selection_changed())

    def selection_changed(self) -> None:
        items = self.waves.selectedItems()
        if len(items) == 1 and self.operation == "Note" and not self.append_note.isChecked():
            self.note.setPlainText(self.document.render_source().wave(items[0].data(Qt.ItemDataRole.UserRole)).wave_note)

    def build_command(self) -> str:
        """Return the same command accepted by the interactive engine."""
        names = [object_name(i.data(Qt.ItemDataRole.UserRole)) for i in self.waves.selectedItems()]
        if not names:
            raise ValueError("Select at least one source wave")
        op = self.operation
        if op == "Concatenate":
            flags = ("/O" if self.overwrite.isChecked() else "") + ("/DL" if self.copy_labels.isChecked() else "")
            if not self.promote.isChecked():
                flags += f"/NP={self.axis.value()}"
            return f"Concatenate{flags} {{{','.join(names)}}},{object_name(self.destination.text())}"
        if op == "SplitWave":
            if len(names) != 1:
                raise ValueError("Select exactly one multidimensional source")
            flags = ("/O" if self.overwrite.isChecked() else "") + ("/NOTE" if self.copy_note.isChecked() else "")
            return f"SplitWave/N={object_name(self.destination.text())}/SDIM={self.output_dim.value()}/SCL={int(self.copy_scale.isChecked())}{flags} {names[0]}"
        if op == "Resample":
            if self.rate.text().strip() and self.same.text().strip():
                raise ValueError("Choose target sample rate or matching wave")
            rates = "/RATE=" + str(float(self.rate.text())) if self.rate.text().strip() else "/SAME=" + object_name(self.same.text()) if self.same.text().strip() else f"/UP={self.up.value()}/DOWN={self.down.value()}"
            coef = "/COEF=" + object_name(self.coefficients.text()) if self.coefficients.text().strip() else ""
            return f"Resample{rates}/DIM={self.axis.value()}/N={self.samples.value()}/WINF={self.window_kind.currentText()}/E={self.end.currentIndex()}{coef} {','.join(names)}"
        if op == "Note":
            flag = "" if self.append_note.isChecked() else "/K"
            return ";".join(f"Note{flag} {name},{json.dumps(self.note.toPlainText(), ensure_ascii=False)}" for name in names)
        label = self.label.text()
        return f"SetDimLabel {self.axis.value()},{self.label_index.value()},$" + json.dumps(label, ensure_ascii=False) + "," + ",".join(names)

    def apply(self) -> None:
        try:
            self.command = self.build_command()
            self.preview.setPlainText(self.command)
            self.accept()
        except ValueError as exc:
            QMessageBox.warning(self, self.operation, str(exc))

    def preview_result(self) -> None:
        """Evaluate on a cloned state and restore the exact document identity."""
        from materials_data_studio.tools.command_engine import CommandEngine
        from materials_data_studio.tools.command_services import DocumentServices
        before = self.document.current
        try:
            code = self.build_command()
            result = CommandEngine(DocumentServices(self.document)).execute(code)
            if not result.success:
                raise ValueError(result.message)
            after = self.document.render_source()
            details = [f"{w.igor_path}: {w.shape}, {w.dtype}" for w in after.waves if w.igor_path in result.affected_objects]
            self.preview.setPlainText(code + "\n" + result.message + "\n" + "\n".join(details))
        except ValueError as exc:
            self.preview.setPlainText(str(exc))
        finally:
            self.document.current = before


def scientific_wave_action(window: Any, operation: str) -> None:
    """Run a dialog's command once and commit the exact state with Undo/Redo."""
    from materials_data_studio.tools.command_engine import CommandEngine
    from materials_data_studio.tools.command_services import DocumentServices
    from materials_data_studio.tools.graph_actions import ensure_document
    ensure_document(window)
    document = window.editor.document
    dialog = WaveOperationDialog(window, document, operation)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return
    before = document.current
    result = CommandEngine(DocumentServices(document)).execute(dialog.command)
    if not result.success:
        QMessageBox.warning(window, operation, result.message)
        return
    after = document.current
    document.current = before
    window.editor.commit(before, after, operation + " waves")
