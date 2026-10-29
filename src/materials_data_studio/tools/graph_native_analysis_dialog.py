"""Forms for the already shared Igor-compatible analysis command subset."""
from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QSpinBox,
)

from materials_data_studio.tools.graph_scientific_dialogs import WaveOperationDialog, object_name


class NativeAnalysisDialog(WaveOperationDialog):
    def __init__(self, parent: Any, document: Any, operation: str) -> None:
        QDialog.__init__(self, parent)
        self.document, self.operation, self.command = document, operation, ""
        self.setWindowTitle(operation)
        self.resize(700, 490)
        form = QFormLayout(self)
        self.source = QComboBox()
        self.second = QComboBox()
        self.second.addItem("None", "")
        for wave in document.render_source().waves:
            if wave.data.ndim == 1 and wave.data.dtype.kind in "biufc":
                self.source.addItem(wave.igor_path, wave.igor_path)
                self.second.addItem(wave.igor_path, wave.igor_path)
        form.addRow("Source", self.source)
        self.destination = QLineEdit("W_"+operation)
        self.count = QSpinBox()
        self.count.setRange(1, 32767)
        self.count.setValue(5)
        self.method = QComboBox()
        self.method.addItems(["Binomial", "Boxcar", "Savitzky-Golay order 2", "Savitzky-Golay order 4"] if operation == "Smooth" else ["Complex", "Real part", "Magnitude", "Magnitude squared", "Phase"] if operation == "FFT" else ["Either crossing", "Rising", "Falling"])
        self.endpoint = QComboBox()
        self.endpoint.addItems(["Bounce", "Wrap", "Zero", "Repeat"])
        self.value = QLineEdit("0")
        self.bin_width = QLineEdit("1")
        self.bins = QSpinBox()
        self.bins.setRange(1, 1000000)
        self.bins.setValue(50)
        self.cumulative = QCheckBox("Cumulative")
        self.probability = QCheckBox("Probability density")
        if operation not in {"Smooth", "Sort", "FindLevel"}:
            form.addRow("Output wave", self.destination)
        if operation == "Smooth":
            form.addRow("Method", self.method)
            form.addRow("Passes / window width", self.count)
            form.addRow("End handling", self.endpoint)
        elif operation == "FFT":
            form.addRow("Output", self.method)
        elif operation == "Histogram":
            form.addRow("First bin edge", self.value)
            form.addRow("Bin width", self.bin_width)
            form.addRow("Bin count", self.bins)
            form.addRow(self.cumulative)
            form.addRow(self.probability)
        elif operation == "FindLevel":
            form.addRow("Level", self.value)
            form.addRow("Crossing", self.method)
        elif operation == "Sort":
            form.addRow("Associated wave (optional)", self.second)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        form.addRow(self.preview)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Do It")
        buttons.addButton("Preview result", QDialogButtonBox.ButtonRole.ActionRole).clicked.connect(self.preview_result)
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def build_command(self) -> str:
        if not self.source.currentData():
            raise ValueError("Select a numeric 1D wave")
        source = object_name(self.source.currentData())
        dest = object_name(self.destination.text())
        if self.operation in {"Differentiate", "Integrate"}:
            if self.source.currentData() == self.destination.text():
                raise ValueError("Choose a different output wave")
            return f"{self.operation}/D={dest} {source}"
        if self.operation == "Smooth":
            method = self.method.currentIndex()
            option = "" if method == 0 else "/B" if method == 1 else "/S="+str(2 if method == 2 else 4)
            return f"Smooth{option}/E={self.endpoint.currentIndex()} {self.count.value()},{source}"
        if self.operation == "FFT":
            return f"FFT/DEST={dest}/OUT={self.method.currentIndex()+1} {source}"
        if self.operation == "Histogram":
            start, width = float(self.value.text()), float(self.bin_width.text())
            return f"Histogram/B={{{start:.17g},{width:.17g},{self.bins.value()}}}/DEST={dest}"+("/CUM" if self.cumulative.isChecked() else "")+("/P" if self.probability.isChecked() else "")+" "+source
        if self.operation == "Sort":
            return f"Sort {source},{source}"+(","+object_name(self.second.currentData()) if self.second.currentData() and self.second.currentData() != self.source.currentData() else "")
        return f"FindLevel/Q/EDGE={self.method.currentIndex()} {source},{float(self.value.text()):.17g}"


def native_analysis(window: Any, operation: str) -> None:
    from materials_data_studio.tools.command_engine import CommandEngine
    from materials_data_studio.tools.command_services import DocumentServices
    from materials_data_studio.tools.graph_actions import ensure_document
    ensure_document(window)
    document = window.editor.document
    dialog = NativeAnalysisDialog(window, document, operation)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return
    before = document.current
    result = CommandEngine(DocumentServices(document)).execute(dialog.command)
    if not result.success:
        QMessageBox.warning(window, operation, result.message)
        return
    after, document.current = document.current, before
    recipe = list(before.environment.get("command_recipe", []))
    recipe.append({"source": dialog.command, "replayable": True, "interpreter": "MDS command subset"})
    after.environment["command_recipe"] = recipe
    window.editor.commit(before, after, operation)
    window.command_workspace.output.insertPlainText(dialog.command+"\n"+result.message+"\n")
