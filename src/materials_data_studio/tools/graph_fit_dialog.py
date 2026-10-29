"""Asynchronous Quick Fit preview, coefficients/errors and commit-on-accept."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from materials_data_studio.tools.graph_document import EditableGraphDocument, GraphState
from materials_data_studio.tools.graph_fit import MODELS, FitResult, quick_fit, trace_values
from materials_data_studio.tools.graph_trace_dialogs import unique_name
from materials_data_studio.tools.graph_waves import make_wave, put_waves
from materials_data_studio.tools.pxp_model import PxpAnnotation, PxpAnnotationStyle, PxpTrace


class FitWorker(QThread):
    completed = Signal(object, str)

    def __init__(self, parent: Any, inputs: tuple[Any, ...], options: dict[str, Any] | None = None) -> None:
        super().__init__(parent)
        self.inputs = inputs
        self.options = options or {}

    def run(self) -> None:
        try:
            result = quick_fit(*self.inputs, cancel=self.isInterruptionRequested, **self.options)
            self.completed.emit(result, "")
        except Exception as exc:
            self.completed.emit(None, str(exc))


def fit_state(document: EditableGraphDocument, graph_name: str, trace_name: str,
              result: FitResult, text_box: bool = True) -> GraphState:
    graph = document.graph(graph_name)
    source = document.render_source()
    base = unique_name("fit_" + trace_name, [w.name for w in source.waves])
    # Allocate the whole group without collisions, including residual waves.
    names = [base + suffix for suffix in ("_x", "_y", "_residual_x", "_residual")]
    while any(source.wave(name) is not None for name in names):
        base += "_2"
        names = [base + suffix for suffix in ("_x", "_y", "_residual_x", "_residual")]
    x, y = (result.calculated, result.x) if graph.swap_xy else (result.x, result.calculated)
    waves = [make_wave(name, data) for name, data in zip(names, (x, y, result.data_x, result.residual), strict=True)]
    state = put_waves(document, waves)
    original = next(t for t in graph.traces if t.name == trace_name)
    name = unique_name(base, [t.name for t in graph.traces])
    fitted = PxpTrace(name, waves[1].igor_path, waves[0].igor_path, original.axis, x_axis=original.x_axis,
                     mode=0, color_igor=(55000, 10000, 10000), line_width=1.5)
    annotations = graph.annotations
    if text_box:
        annotation_name = unique_name("Quick Fit", [a.name for a in annotations])
        annotations += (PxpAnnotation(annotation_name, result.summary(),
                         PxpAnnotationStyle(anchor="RT", x_percent=-3, y_percent=-3, font_size=10, frame=1)),)
    updated = replace(graph, traces=(*graph.traces, fitted), annotations=annotations)
    state.graphs = tuple(updated if g.name == graph.name else g for g in state.graphs)
    state.layout.setdefault(graph.name, {}).setdefault("fits", {})[name] = {
        "model": result.model, "source_trace": trace_name, "parameters": list(result.parameters),
        "values": list(result.values), "standard_errors": [e if np.isfinite(e) else None for e in result.errors],
        "count": result.count, "rss": result.rss, "x_offset": result.x_offset,
        "range": [float(result.data_x.min()), float(result.data_x.max())], "waves": names,
    }
    return state


class QuickFitDialog(QDialog):
    def __init__(self, parent: Any, document: EditableGraphDocument, graph_name: str,
                 trace_name: str = "", model: str = "line", limits: tuple[float, float] | None = None,
                 weighted: bool = False) -> None:
        super().__init__(parent)
        self.document, self.graph_name = document, graph_name
        self.setWindowTitle("Curve Fitting — Function / Data / Output")
        self.resize(830, 730)
        self.worker: FitWorker | None = None
        self.fit_result: FitResult | None = None
        self.result_state: GraphState | None = None
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.trace = QComboBox()
        graph = document.graph(graph_name)
        for trace in graph.traces:
            self.trace.addItem(f"{trace.name}: {trace.y_wave} vs {trace.x_wave or '_calculated_'}", trace.name)
        self.trace.setCurrentIndex(max(0, self.trace.findData(trace_name)))
        self.model = QComboBox()
        self.model.addItems([*MODELS, *(f"poly:{i}" for i in range(2, 11)), *(f"poly_XOffset:{i}" for i in range(2, 11))])
        self.model.setCurrentText(model)
        self.minimum = QLineEdit(str(min(limits)) if limits else "")
        self.maximum = QLineEdit(str(max(limits)) if limits else "")
        self.weight = QCheckBox("Weight from Y error-bar waves (absolute sigma)")
        try:
            usable_sigma = trace_values(document, graph_name, self.trace.currentData())[2] is not None
        except (ValueError, TypeError):
            usable_sigma = False
        self.weight.setChecked(weighted and usable_sigma)
        self.text_box = QCheckBox("Add coefficient / standard-error annotation")
        self.text_box.setChecked(True)
        self.initial_values = QLineEdit()
        self.initial_values.setPlaceholderText("Blank = automatic guesses; comma-separated model coefficients")
        self.held = QLineEdit()
        self.held.setPlaceholderText("Zero-based indexes, e.g. 0,2")
        self.mask_wave = QComboBox()
        self.mask_wave.addItem("None", "")
        for wave in document.render_source().waves:
            if wave.data.ndim == 1 and wave.data.dtype.kind in "biuf":
                self.mask_wave.addItem(wave.igor_path, wave.igor_path)
        self.full_range = QCheckBox("Extend fitted curve over the full source X range")
        for caption, widget in (("Trace / full path", self.trace), ("Function", self.model), ("X minimum (blank = all)", self.minimum),
                                ("X maximum (blank = all)", self.maximum)):
            form.addRow(caption, widget)
        form.addRow(self.weight)
        form.addRow(self.text_box)
        form.addRow("Initial coefficients", self.initial_values)
        form.addRow("Hold coefficients", self.held)
        form.addRow("Mask wave (nonzero=include)", self.mask_wave)
        form.addRow(self.full_range)
        layout.addLayout(form)
        self.run_button = QPushButton("Run Quick Fit")
        self.run_button.clicked.connect(self.run)
        layout.addWidget(self.run_button)
        self.canvas = FigureCanvasQTAgg(Figure(figsize=(7, 3), dpi=90))
        layout.addWidget(self.canvas, 1)
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setMaximumHeight(160)
        layout.addWidget(self.summary)
        self.status = QLabel("Fit native numeric waves; no Igor procedures are executed.")
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Add Fit to Graph")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self.buttons.accepted.connect(self.apply)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        for widget in (self.trace, self.model, self.mask_wave):
            widget.currentIndexChanged.connect(self.invalidate)
        for widget in (self.minimum, self.maximum, self.initial_values, self.held):
            widget.textChanged.connect(self.invalidate)
        self.weight.toggled.connect(self.invalidate)
        self.full_range.toggled.connect(self.invalidate)

    def invalidate(self, *_args: Any) -> None:
        self.fit_result = None
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)

    def run(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.requestInterruption()
            self.status.setText("Stopping Quick Fit…")
            return
        try:
            x, y, sigma = trace_values(self.document, self.graph_name, self.trace.currentData())
            if self.weight.isChecked() and sigma is None:
                raise ValueError("This trace has no usable Y error-bar wave.")
            lower = float(self.minimum.text()) if self.minimum.text().strip() else None
            upper = float(self.maximum.text()) if self.maximum.text().strip() else None
            if any(v is not None and not np.isfinite(v) for v in (lower, upper)):
                raise ValueError("Fit limits must be finite.")
            self.invalidate()
            selected = self.document.render_source().wave(self.mask_wave.currentData()) if self.mask_wave.currentData() else None
            if selected is not None:
                trace = next(t for t in self.document.graph(self.graph_name).traces if t.name == self.trace.currentData())
                mask = selected.data[slice(trace.point_range[0], trace.point_range[1]+1)] if trace.point_range else selected.data
            else:
                mask = None
            initial = [float(v) for v in self.initial_values.text().split(",")] if self.initial_values.text().strip() else None
            held = tuple(int(v) for v in self.held.text().split(",")) if self.held.text().strip() else ()
            options = {"initial_values": initial, "held": held, "selected": mask,
                       "curve_range": (float(np.nanmin(x)), float(np.nanmax(x))) if self.full_range.isChecked() else None}
            self.worker = FitWorker(self, (x, y, self.model.currentText(), lower, upper, sigma if self.weight.isChecked() else None), options)
            self.worker.completed.connect(self.completed)
            self.worker.finished.connect(self.stopped)
            self.run_button.setText("Stop Quick Fit")
            for widget in (self.trace, self.model, self.minimum, self.maximum, self.weight, self.mask_wave, self.initial_values, self.held, self.full_range):
                widget.setEnabled(False)
            self.status.setText("Fitting…")
            self.worker.start()
        except ValueError as exc:
            QMessageBox.warning(self, "Quick Fit", str(exc))

    def completed(self, result: FitResult | None, error: str) -> None:
        if self.worker is not None and self.worker.isInterruptionRequested():
            result, error = None, "Quick Fit stopped. No waves or graph edits were applied."
        self.fit_result = result
        if error:
            self.status.setText(error)
            return
        assert result is not None
        self.summary.setPlainText(result.summary())
        self.status.setText("Fit complete. Inspect the curve and residual before adding it to the graph.")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)
        figure = self.canvas.figure
        figure.clear()
        ax, residual_ax = figure.subplots(2, 1, sharex=True, gridspec_kw={"height_ratios": [3, 1]})
        x, y, _sigma = trace_values(self.document, self.graph_name, self.trace.currentData())
        ax.plot(x, y, "o", markersize=3, label="Data")
        ax.plot(result.x, result.calculated, color="#cf3535", label="Fit")
        ax.legend(fontsize=9)
        residual_ax.plot(result.data_x, result.residual, "o", markersize=2)
        residual_ax.axhline(0, color="gray", linewidth=0.5)
        residual_ax.set_ylabel("Residual")
        figure.tight_layout()
        self.canvas.draw_idle()

    def stopped(self) -> None:
        self.run_button.setText("Run Quick Fit")
        for widget in (self.trace, self.model, self.minimum, self.maximum, self.weight, self.mask_wave, self.initial_values, self.held, self.full_range):
            widget.setEnabled(True)

    def apply(self) -> None:
        if self.fit_result is not None:
            self.result_state = fit_state(self.document, self.graph_name, self.trace.currentData(), self.fit_result, self.text_box.isChecked())
            self.accept()

    def reject(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.requestInterruption()
            self.status.setText("Stopping… close this dialog again after the fit stops.")
            return
        super().reject()

    def closeEvent(self, event: Any) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.reject()
            event.ignore()
        else:
            event.accept()
