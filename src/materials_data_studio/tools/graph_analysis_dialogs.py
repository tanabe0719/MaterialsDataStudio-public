"""Explicit image-mask and statistical analysis forms using the command service."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QVBoxLayout,
)

from materials_data_studio.tools.graph_scientific_dialogs import WaveOperationDialog, object_name


class ScientificAnalysisDialog(WaveOperationDialog):
    def __init__(self, parent: Any, document: Any, operation: str) -> None:
        QDialog.__init__(self, parent)
        self.document, self.operation, self.command = document, operation, ""
        self.setWindowTitle("Image Threshold / ROI Mask" if operation == "ImageThreshold" else "T Test")
        self.resize(720, 660)
        layout = QVBoxLayout(self)
        self.waves = QListWidget()
        self.waves.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        image = operation == "ImageThreshold"
        for wave in document.render_source().waves:
            if wave.data.dtype.kind in "biuf" and (wave.data.ndim in {2, 3} if image else True):
                self.waves.addItem(f"{wave.igor_path}  {wave.shape}")
                self.waves.item(self.waves.count()-1).setData(Qt.ItemDataRole.UserRole, wave.igor_path)
        layout.addWidget(self.waves)
        form = QFormLayout()
        layout.addLayout(form)
        self.destination = QLineEdit("M_ImageThresh" if image else "W_StatsTTest")
        form.addRow("Output wave", self.destination)
        self.method = QComboBox()
        self.method.addItems(["Manual threshold", "Iterative", "Otsu"] if image else ["One sample", "Two independent samples (Welch)", "Paired samples"])
        form.addRow("Method", self.method)
        self.value = QDoubleSpinBox()
        self.value.setRange(-1e15, 1e15)
        self.value.setDecimals(8)
        form.addRow("Threshold" if image else "Hypothesized mean", self.value)
        self.invert = QCheckBox("Invert mask")
        self.background = QCheckBox("Encode background as 64")
        self.roi = QComboBox()
        self.roi.addItem("Whole image", "")
        for wave in document.render_source().waves:
            if wave.data.ndim == 2 and str(wave.data.dtype) == "uint8":
                self.roi.addItem(wave.igor_path, wave.igor_path)
        self.outside = QComboBox()
        self.outside.addItems(["Outside ROI: zero", "Outside ROI: preserve input", "Outside ROI: 64"])
        self.outside.setCurrentIndex(1)
        self.tail = QComboBox()
        for label, value in [("Two-sided", 4), ("Greater than", 1), ("Less than", 2)]:
            self.tail.addItem(label, value)
        self.alpha = QDoubleSpinBox()
        self.alpha.setRange(.000001, .999999)
        self.alpha.setDecimals(6)
        self.alpha.setValue(.05)
        self.confidence = QCheckBox("Compute confidence interval (one-sample)")
        self.df_method = QComboBox()
        self.df_method.addItems(["Equivalent (floating point)", "Equivalent (integer)", "n1+n2-2 (equal variances)"])
        self.table = QCheckBox("Show labelled result table")
        self.table.setChecked(True)
        if image:
            form.addRow(self.invert)
            form.addRow(self.background)
            form.addRow("ROI mask (zero=inside)", self.roi)
            form.addRow("Outside ROI", self.outside)
        else:
            form.addRow("Alternative hypothesis", self.tail)
            form.addRow("Significance level", self.alpha)
            form.addRow("Degrees of freedom", self.df_method)
            form.addRow(self.confidence)
            form.addRow(self.table)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMaximumHeight(120)
        layout.addWidget(self.preview)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.addButton("Preview result", QDialogButtonBox.ButtonRole.ActionRole).clicked.connect(self.preview_result)
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if not image:
            self.method.currentIndexChanged.connect(self.update_test_controls)
            self.update_test_controls()

    def update_test_controls(self) -> None:
        mode = self.method.currentIndex()
        self.value.setEnabled(mode == 0)
        self.df_method.setEnabled(mode == 1)
        self.confidence.setEnabled(mode == 0)
        if mode != 0:
            self.confidence.setChecked(False)

    def build_command(self) -> str:
        sources = [object_name(item.data(Qt.ItemDataRole.UserRole)) for item in self.waves.selectedItems()]
        dest = object_name(self.destination.text())
        if self.operation == "ImageThreshold":
            if len(sources) != 1:
                raise ValueError("Select exactly one image")
            mode = [0, 1, 7][self.method.currentIndex()]
            flags = f"/T={self.value.value():.17g}" if mode == 0 else f"/M={mode}"
            flags += ("/I" if self.invert.isChecked() else "") + ("/N" if self.background.isChecked() else "")
            if self.roi.currentData():
                flags += f"/R={{{object_name(self.roi.currentData())},{self.outside.currentIndex()}}}"
            return f"ImageThreshold/Q{flags}/DEST={dest} {sources[0]}"
        count = 1 if self.method.currentIndex() == 0 else 2
        if len(sources) != count:
            raise ValueError(f"Select {count} wave(s), in list order")
        flags = f"/MEAN={self.value.value():.17g}" if count == 1 else "/PAIR" if self.method.currentIndex() == 2 else ""
        if self.method.currentIndex() == 1:
            flags += f"/DFM={self.df_method.currentIndex()}"
        flags += ("/CI" if self.confidence.isChecked() else "") + ("/T" if self.table.isChecked() else "")
        return f"StatsTTest/Q{flags}/TAIL={self.tail.currentData()}/ALPH={self.alpha.value():.17g}/DEST={dest} " + ",".join(sources)


def analysis_action(window: Any, operation: str) -> None:
    from PySide6.QtWidgets import QMessageBox

    from materials_data_studio.tools.command_engine import CommandEngine
    from materials_data_studio.tools.command_services import DocumentServices
    from materials_data_studio.tools.graph_actions import ensure_document
    ensure_document(window)
    document = window.editor.document
    dialog = ScientificAnalysisDialog(window, document, operation)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return
    before = document.current
    result = CommandEngine(DocumentServices(document)).execute(dialog.command)
    if not result.success:
        QMessageBox.warning(window, operation, result.message)
        return
    after, document.current = document.current, before
    window.editor.commit(before, after, operation)
