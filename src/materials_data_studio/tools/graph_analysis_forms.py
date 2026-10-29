"""Analysis/Statistics dialogs, with preview and one transactional application."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from materials_data_studio.tools.graph_scientific_dialogs import WaveOperationDialog, object_name
from materials_data_studio.tools.graph_signal_analysis import PROCESSING
from materials_data_studio.tools.graph_statistics import STATISTICS


class AnalysisDialog(WaveOperationDialog):
    def __init__(self, parent: Any, document: Any, kind: str, *, statistical: bool = True) -> None:
        QDialog.__init__(self, parent)
        self.document, self.kind, self.statistical = document, kind, statistical
        self.operation = "MDSStatistics" if statistical else "MDSProcess"
        self.command = ""
        title, methods = (STATISTICS if statistical else PROCESSING)[kind]
        self.setWindowTitle(title)
        self.resize(750, 680)
        layout = QVBoxLayout(self)
        self.instructions = QLabel({
            "chi": "Wave 1: observed counts. Wave 2: expected counts (equal totals).",
            "anova2": "One matrix: rows=A, columns=B; balanced replication adds a third replicate dimension.",
            "contingency": "One matrix of nonnegative integer counts.",
            "average": "Select equal-shaped waves. Creates pointwise mean, sample SD and SEM waves.",
            "circular": "Angles must be in radians. Rayleigh tests circular uniformity (asymptotic P).",
            "resample": "Bootstrap mean with percentile confidence limits; reproducible random seed.",
            "ks": "One-sample mode compares with N(0,1); normalize using independently specified parameters.",
            "convolve": "Wave 1: kernel. Wave 2: signal. Output is a new wave.",
            "correlate": "Wave 1: reference. Wave 2: signal. For autocorrelation choose the same wave in both roles below.",
            "interpolate": "Wave 1: Y values. Optional wave 2: strictly monotonic X values.",
            "filter": "Wave 1: signal. Wave 2: numerator coefficients. IIR wave 3: denominator coefficients. Zero initial conditions.",
        }.get(kind, "Choose input waves. Pairwise tests preserve missing-value alignment."))
        self.instructions.setWordWrap(True)
        layout.addWidget(self.instructions)
        self.waves = QListWidget()
        self.waves.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        for wave in document.render_source().waves:
            if wave.data.dtype.kind in ("biuf" if statistical else "biufc"):
                self.waves.addItem(f"{wave.igor_path}  {wave.shape}")
                self.waves.item(self.waves.count()-1).setData(Qt.ItemDataRole.UserRole, wave.igor_path)
        layout.addWidget(self.waves)
        order = QHBoxLayout()
        for label, step in (("Move input up", -1), ("Move input down", 1)):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, delta=step: self.move_input(delta))
            order.addWidget(button)
        layout.addLayout(order)
        form = QFormLayout()
        layout.addLayout(form)
        self.method = QComboBox()
        self.method.addItems(methods)
        form.addRow("Method", self.method)
        self.destination = QLineEdit("W_"+kind)
        form.addRow("Output wave (working copy)", self.destination)
        self.alpha = QDoubleSpinBox()
        self.alpha.setDecimals(6)
        self.alpha.setRange(.000001, .999999)
        self.alpha.setValue(.05)
        self.tail = QComboBox()
        for label, value in [("Two-sided", 4), ("Greater", 1), ("Less", 2)]:
            self.tail.addItem(label, value)
        if kind == "ks":
            self.tail.setItemText(1, "F1 > F2 (CDF)")
            self.tail.setItemText(2, "F1 < F2 (CDF)")
        self.count = QSpinBox()
        self.count.setRange(2, 1000000)
        self.count.setValue(2000 if statistical else 201)
        self.seed = QSpinBox()
        self.seed.setRange(0, 2147483647)
        self.table = QCheckBox("Show labelled result table")
        self.table.setChecked(True)
        self.remove_dc = QCheckBox("Remove mean (DC component)")
        self.autocorrelation = QCheckBox("Use first wave for both correlation inputs")
        if statistical:
            form.addRow("Significance level / 1-confidence", self.alpha)
            if kind in {"ftest", "correlation", "ks", "rank", "contingency"}:
                form.addRow("Alternative hypothesis", self.tail)
            if kind == "resample":
                form.addRow("Resamples", self.count)
                form.addRow("Random seed", self.seed)
            form.addRow(self.table)
        else:
            if kind in {"interpolate", "lomb", "cwt", "stft"}:
                self.count.setValue(32 if kind in {"cwt", "stft"} else 201)
                form.addRow("Maximum scale" if kind == "cwt" else "Window length" if kind == "stft" else "Output points / frequencies", self.count)
            if kind == "correlate":
                form.addRow(self.remove_dc)
                form.addRow(self.autocorrelation)
        layout.addWidget(QLabel("Inputs follow list order (Wave 1, Wave 2, …). Preview does not change the document."))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMaximumHeight(170)
        layout.addWidget(self.preview)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Do It")
        buttons.addButton("Preview result", QDialogButtonBox.ButtonRole.ActionRole).clicked.connect(self.preview_result)
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.method.currentIndexChanged.connect(self.update_method_controls)
        self.update_method_controls()

    def update_method_controls(self) -> None:
        enabled = self.kind != "contingency" or self.method.currentIndex() == 2
        enabled = enabled and (self.kind != "rank" or self.method.currentIndex() != 2)
        self.tail.setEnabled(enabled)
        if not enabled:
            self.tail.setCurrentIndex(0)

    def move_input(self, step: int) -> None:
        selected = {i.data(Qt.ItemDataRole.UserRole) for i in self.waves.selectedItems()}
        row = self.waves.currentRow()
        target = row+step
        if row < 0 or not 0 <= target < self.waves.count():
            return
        item = self.waves.takeItem(row)
        self.waves.insertItem(target, item)
        self.waves.setCurrentItem(item)
        for index in range(self.waves.count()):
            current = self.waves.item(index)
            current.setSelected(current.data(Qt.ItemDataRole.UserRole) in selected)

    def build_command(self) -> str:
        names = [object_name(i.data(Qt.ItemDataRole.UserRole)) for i in self.waves.selectedItems()]
        if not names:
            raise ValueError("Select input waves")
        if self.kind == "correlate" and self.autocorrelation.isChecked():
            if len(names) != 1:
                raise ValueError("Select only one wave for autocorrelation")
            names *= 2
        options = f"/KIND={self.kind}/METH={self.method.currentIndex()}/DEST={object_name(self.destination.text())}"
        if self.statistical:
            options += f"/ALPH={self.alpha.value():.17g}/TAIL={self.tail.currentData()}/N={self.count.value()}/SEED={self.seed.value()}"
            if self.table.isChecked():
                options += "/T"
        else:
            options += f"/N={self.count.value()}" + ("/NODC" if self.remove_dc.isChecked() else "")
        return self.operation+options+" "+",".join(names)

    def preview_result(self) -> None:
        from materials_data_studio.tools.command_engine import CommandEngine
        from materials_data_studio.tools.command_services import DocumentServices
        from materials_data_studio.ui.tasks import blocking_task
        try:
            command = self.build_command()
            working = replace(self.document, current=self.document.current.clone())
            result = blocking_task(self, "Preview "+self.windowTitle(), lambda: CommandEngine(DocumentServices(working)).execute(command), allow_cancel=False)
            self.preview.setPlainText(command+"\n"+result.message)
        except ValueError as exc:
            self.preview.setPlainText(str(exc))


def show_analysis(window: Any, kind: str, *, statistical: bool = True) -> None:
    from PySide6.QtWidgets import QMessageBox

    from materials_data_studio.tools.command_engine import CommandEngine
    from materials_data_studio.tools.command_services import DocumentServices
    from materials_data_studio.tools.graph_actions import ensure_document
    ensure_document(window)
    document = window.editor.document
    dialog = AnalysisDialog(window, document, kind, statistical=statistical)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return
    before = document.current
    from materials_data_studio.ui.tasks import blocking_task
    working = replace(document, current=before.clone())
    result = blocking_task(window, dialog.windowTitle(), lambda: CommandEngine(DocumentServices(working)).execute(dialog.command), allow_cancel=False)
    if not result.success:
        QMessageBox.warning(window, dialog.windowTitle(), result.message)
        return
    after = working.current
    recipe = list(before.environment.get("command_recipe", []))
    recipe.append({"source": dialog.command, "replayable": True, "interpreter": "MDS command subset"})
    after.environment["command_recipe"] = recipe
    window.editor.commit(before, after, dialog.windowTitle())
    window.command_workspace.output.insertPlainText(dialog.command+"\n"+result.message+"\n")
