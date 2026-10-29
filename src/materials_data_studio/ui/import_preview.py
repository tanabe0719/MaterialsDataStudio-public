"""Instrument-independent X/Y/sigma preview with persistent named templates."""
from __future__ import annotations

import json
from pathlib import Path
from threading import Event

import numpy as np
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from materials_data_studio.core.settings import create_settings
from materials_data_studio.tools.graph_waves import column_names, detect_start, text_rows
from materials_data_studio.ui.tasks import run_task


class ImportPreview(QDialog):
    def __init__(self, path: Path, payload: bytes, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import preview — columns, units and uncertainty")
        self.resize(950, 720)
        self.text = payload.decode("utf-8-sig", errors="replace")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(path.name + " — verify column roles and physical units before importing"))
        form = QFormLayout()
        self.delimiter = QComboBox()
        self.delimiter.addItems(["Auto", "Comma", "Tab", "Whitespace", "Semicolon"])
        form.addRow("Delimiter", self.delimiter)
        self.start = QSpinBox()
        self.start.setRange(1, max(1, self.text.count("\n") + 1))
        form.addRow("First data row", self.start)
        self.x_column, self.y_column, self.sigma = QComboBox(), QComboBox(), QComboBox()
        for name, combo in (("X column", self.x_column), ("Y / intensity column", self.y_column), ("Sigma / standard uncertainty", self.sigma)):
            form.addRow(name, combo)
        self.axis = QComboBox()
        self.axis.addItem("2θ (degrees)", "two_theta")
        self.axis.addItem("2θ (radians → degrees)", "radians")
        self.axis.addItem("TOF (µs)", "tof")
        form.addRow("X axis / source unit", self.axis)
        self.y_unit = QLineEdit("counts")
        form.addRow("Y unit", self.y_unit)
        layout.addLayout(form)
        self.read_status = QLabel()
        self.read_status.setWordWrap(True)
        layout.addWidget(self.read_status)
        self.preview = QTableWidget()
        self.preview.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.preview, 1)
        row = QHBoxLayout()
        self.templates = QComboBox()
        self.templates.addItems(sorted(self.template_values()))
        row.addWidget(self.templates, 1)
        save, load = QPushButton("Save template…"), QPushButton("Load template")
        save.clicked.connect(self.save_template)
        load.clicked.connect(self.load_template)
        row.addWidget(save)
        row.addWidget(load)
        layout.addLayout(row)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.delimiter.currentTextChanged.connect(self.refresh)
        self.start.valueChanged.connect(self.update_preview)
        self.refresh()

    def refresh(self):
        delimiter = self.delimiter.currentText()
        previous = getattr(self, "_preview_stop", None)
        if previous is not None:
            previous.set()
        self._preview_stop = stop = Event()
        text = self.text
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self.preview.setRowCount(0)
        self.preview.setToolTip("Reading text columns…")
        self.read_status.setText("Reading text columns…")
        # Import completion already runs inside a queued task callback. Starting
        # another modal event loop while constructing its hidden dialog can trap
        # window activation/finished events. Populate the visible dialog asynchronously.
        run_task(self, "Read import columns", lambda: text_rows(text, delimiter, cancelled=stop),
                 self._populate, cancelled=stop, on_error=self._preview_error)

    def _preview_error(self, message):
        self.preview.setToolTip(message)
        self.read_status.setText(message)

    def _populate(self, rows):
        self.rows = rows
        self.preview.setToolTip("")
        self.read_status.setText("")
        start = detect_start(self.rows)
        names = column_names(self.rows, start)
        width = max(len(names), max((len(row) for row in self.rows[start:start + 100]), default=2))
        for combo in (self.x_column, self.y_column, self.sigma):
            combo.clear()
            if combo is self.sigma:
                combo.addItem("None (unweighted)", None)
            for index in range(width):
                combo.addItem(f"{index + 1}: {names[index] if index < len(names) else 'column'}", index)
        self.y_column.setCurrentIndex(min(1, width - 1))
        self.start.setValue(start + 1)
        self.update_preview()
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(self.rows))
        pending = getattr(self, "_pending_template", None)
        if pending is not None:
            self._pending_template = None
            if pending["delimiter"] == self.delimiter.currentText():
                self._apply_template(pending)

    def update_preview(self):
        if not hasattr(self, "rows"):
            return
        start = self.start.value() - 1
        rows = self.rows[max(0, start - 2):start + 80]
        width = max((len(row) for row in rows), default=0)
        self.preview.setColumnCount(width)
        self.preview.setRowCount(len(rows))
        self.preview.setVerticalHeaderLabels([str(i + max(0, start - 2) + 1) for i in range(len(rows))])
        for i, row in enumerate(rows):
            for j, text in enumerate(row):
                self.preview.setItem(i, j, QTableWidgetItem(text))

    def options(self):
        return {"delimiter": self.delimiter.currentText(), "start": self.start.value(), "x": self.x_column.currentData(), "y": self.y_column.currentData(), "sigma": self.sigma.currentData(), "axis": self.axis.currentData(), "y_unit": self.y_unit.text()}

    def arrays(self):
        from materials_data_studio.modules.xrd.parser import _parse_xrd_lines
        selected = self.options()
        if selected["sigma"] in {selected["x"], selected["y"]}:
            raise ValueError("Sigma must be a separate column or None")
        lines = ["\t".join(row) for row in self.rows[self.start.value() - 1:]]
        result = _parse_xrd_lines(lines, delimiter="\t", x_column=selected["x"], y_column=selected["y"], sigma_column=selected["sigma"], force_two_columns=True)
        if selected["axis"] == "radians":
            result.x = np.rad2deg(result.x)
        return result

    @staticmethod
    def template_values():
        try:
            return json.loads(str(create_settings().value("import/templates", "{}")))
        except (ValueError, TypeError):
            return {}

    def save_template(self):
        name, accepted = QInputDialog.getText(self, "Save import template", "Template name")
        if accepted and name.strip():
            templates = self.template_values()
            templates[name.strip()] = self.options()
            create_settings().setValue("import/templates", json.dumps(templates))
            self.templates.clear()
            self.templates.addItems(sorted(templates))

    def load_template(self):
        value = self.template_values().get(self.templates.currentText())
        if value:
            if self.delimiter.currentText() != value["delimiter"]:
                self._pending_template = value
                self.delimiter.setCurrentText(value["delimiter"])
            else:
                self._apply_template(value)

    def _apply_template(self, value):
        self.start.setValue(value["start"])
        for field in ("x", "y", "sigma", "axis"):
            combo = getattr(self, field + "_column" if field in {"x", "y"} else field)
            combo.setCurrentIndex(max(0, combo.findData(value[field])))
        self.y_unit.setText(value.get("y_unit", ""))
