"""Preview-based text import and virtual, full-precision working-wave editing."""

from __future__ import annotations

import csv
from pathlib import Path
from threading import Event
from typing import Any

import numpy as np
from PySide6.QtCore import QAbstractTableModel, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QSpinBox,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)
from shiboken6 import isValid

from materials_data_studio.tools.graph_waves import (
    MAX_TEXT_COLUMNS,
    column_names,
    detect_start,
    import_columns,
    make_wave,
    read_text,
    text_rows,
)
from materials_data_studio.tools.pxp_model import PxpWave
from materials_data_studio.ui.tasks import blocking_task


class TextImportDialog(QDialog):
    def __init__(self, parent: Any, path: Path) -> None:
        super().__init__(parent)
        self.setWindowTitle("Load General Text — working waves")
        self.resize(1000, 690)
        self._busy = False
        stop = Event()
        try:
            self.text = blocking_task(parent or self, "Read general text", lambda: read_text(path, cancelled=stop), cancelled=stop)
        except Exception:
            if isValid(self):
                self.deleteLater()
            raise
        self.path = path
        self.waves: list[PxpWave] = []
        self.rows: list[list[str]] = []
        self._data_widths: list[int] = []
        self._valid_columns = False
        self._parsed_delimiter: str | None = None
        layout = QVBoxLayout(self)
        label = QLabel(path.name)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(label)
        controls = QHBoxLayout()
        self.delimiter = QComboBox()
        self.delimiter.addItems(["Auto", "Comma", "Tab", "Whitespace", "Semicolon"])
        self.start = QSpinBox()
        self.start.setPrefix("First data row: ")
        self.start.setMinimum(1)
        self.header = QCheckBox("Use previous row as column names")
        self.header.setChecked(True)
        controls.addWidget(self.delimiter)
        controls.addWidget(self.start)
        controls.addWidget(self.header)
        layout.addLayout(controls)
        self.preview = QTableWidget()
        self.preview.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.preview, 2)
        self.preview_note = QLabel("Preview is limited to 100 rows and 32 columns; the column list below retains every imported column.")
        self.preview_note.setWordWrap(True)
        layout.addWidget(self.preview_note)
        layout.addWidget(QLabel("Provide wave names / column format (double precision). Blank numeric cells become NaN."))
        self.columns = QTableWidget(0, 3)
        self.columns.setHorizontalHeaderLabels(["Column", "Wave name", "Format"])
        self.columns.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.columns, 1)
        self.overwrite = QCheckBox("Overwrite existing working waves (requires confirmation)")
        layout.addWidget(self.overwrite)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Load Waves")
        self.buttons.accepted.connect(self.load)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.delimiter.currentTextChanged.connect(self.refresh)
        self.start.valueChanged.connect(self.populate_columns)
        self.header.toggled.connect(self.populate_columns)
        try:
            self.refresh()
        except Exception:
            if isValid(self):
                self.deleteLater()
            raise

    def refresh(self, *_args: Any) -> None:
        if not isValid(self) or self._busy:
            return
        self._busy = True
        self._enable_controls(False)
        try:
            while True:
                delimiter = self.delimiter.currentText()
                stop = Event()
                text = self.text

                def parse(source: str = text, separator: str = delimiter,
                          cancelled: Event = stop) -> tuple[list[list[str]], int, list[int]]:
                    rows = text_rows(source, separator, cancelled=cancelled)
                    start = detect_start(rows, cancelled=cancelled)
                    # One worker-side suffix scan covers late columns too. A
                    # preview sample must never determine scientific data width.
                    widths = [0] * len(rows)
                    maximum = 0
                    for index in range(len(rows) - 1, -1, -1):
                        if index % 1024 == 0 and cancelled.is_set():
                            raise InterruptedError("Text parsing stopped")
                        maximum = max(maximum, len(rows[index]))
                        widths[index] = maximum
                    width = max(len(column_names(rows, start)), widths[start] if widths else 0)
                    if width > MAX_TEXT_COLUMNS:
                        raise ValueError(f"General-text import supports at most {MAX_TEXT_COLUMNS} data columns. Choose the correct delimiter or split the file.")
                    return rows, start, widths

                parent = self.parentWidget()
                owner = parent if not self.isVisible() and parent is not None else self
                try:
                    rows, start, widths = blocking_task(owner, "Parse text columns", parse, cancelled=stop)
                except (InterruptedError, RuntimeError) as exc:
                    if not isValid(self):
                        if self._parsed_delimiter is None:
                            raise InterruptedError("Text import owner closed") from exc
                        return
                    if self._parsed_delimiter is None:
                        raise
                    self.delimiter.blockSignals(True)
                    self.delimiter.setCurrentText(self._parsed_delimiter)
                    self.delimiter.blockSignals(False)
                    if not isinstance(exc, InterruptedError):
                        QMessageBox.warning(self, "Text Import", str(exc))
                    return
                if delimiter != self.delimiter.currentText():
                    # A programmatic change may arrive in the nested event loop.
                    # Serialize/coalesce it and never apply stale rows/settings.
                    continue
                self.rows = rows
                self._data_widths = widths
                self._parsed_delimiter = delimiter
                self.start.blockSignals(True)
                self.start.setMaximum(max(1, len(self.rows)))
                self.start.setValue(start + 1)
                self.start.blockSignals(False)
                self.header.blockSignals(True)
                self.header.setChecked(bool(column_names(self.rows, self.start.value() - 1)))
                self.header.blockSignals(False)
                self.populate_columns()
                break
        finally:
            self._busy = False
            if isValid(self):
                self._enable_controls(True)

    def _enable_controls(self, enabled: bool) -> None:
        for widget in (self.delimiter, self.start, self.header, self.columns, self.buttons):
            widget.setEnabled(enabled)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(enabled and self._valid_columns)

    def populate_columns(self, *_args: Any) -> None:
        start = self.start.value() - 1
        names = column_names(self.rows, start) if self.header.isChecked() else []
        width = max(len(names), self._data_widths[start] if start < len(self._data_widths) else 0)
        self._valid_columns = 0 < width <= MAX_TEXT_COLUMNS
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(self._valid_columns)
        self.preview_note.setText(
            "Preview is limited to 100 rows and 32 columns; the column list below retains every imported column."
            if self._valid_columns else f"This data block has {width} columns; choose a start row with 1–{MAX_TEXT_COLUMNS} columns. No data will be loaded.")
        # Show context surrounding the chosen row, not only the first 100 lines
        # (instrument files often have large headers).
        first = max(0, start - 3)
        records = self.rows[first:first + 100]
        self.preview.clearContents()
        self.preview.setRowCount(len(records))
        preview_width = min(32, width)
        self.preview.setColumnCount(preview_width)
        self.preview.setVerticalHeaderLabels([str(i + first + 1) for i in range(len(records))])
        for i, row in enumerate(records):
            for col, cell in enumerate(row[:preview_width]):
                item = QTableWidgetItem(cell)
                if i + first == start:
                    item.setBackground(self.palette().highlight())
                    item.setForeground(self.palette().highlightedText())
                self.preview.setItem(i, col, item)
        self.columns.setRowCount(width if self._valid_columns else 0)
        for col in range(width if self._valid_columns else 0):
            self.columns.setItem(col, 0, QTableWidgetItem(str(col + 1)))
            name = names[col] if col < len(names) and names[col] else f"wave{col}"
            self.columns.setItem(col, 1, QTableWidgetItem(name))
            kind = QComboBox()
            kind.addItems(["Number", "Text", "Skip"])
            if name.casefold() in {"comment", "comments", "remark", "remarks"}:
                kind.setCurrentText("Text")
            try:
                for row in self.rows[start:start + 30]:
                    if col < len(row) and row[col]:
                        float(row[col].replace("D", "E").replace("d", "e"))
            except ValueError:
                kind.setCurrentText("Text")
            self.columns.setCellWidget(col, 2, kind)

    def load(self) -> None:
        if self._busy or not self._valid_columns:
            return
        self._busy = True
        self._enable_controls(False)
        try:
            names = [item.text() if (item := self.columns.item(i, 1)) else "" for i in range(self.columns.rowCount())]
            kinds = [self.columns.cellWidget(i, 2).currentText() for i in range(self.columns.rowCount())]  # type: ignore[attr-defined]
            start = self.start.value() - 1
            stop = Event()
            rows = self.rows
            self.waves = blocking_task(self, "Load working waves", lambda: import_columns(rows, start, names, kinds, cancelled=stop), cancelled=stop)
            self.accept()
        except InterruptedError:
            return
        except (ValueError, TypeError, RuntimeError) as exc:
            if isValid(self):
                QMessageBox.warning(self, "Text Import", str(exc))
        finally:
            self._busy = False
            if isValid(self):
                self._enable_controls(True)


class WaveTableModel(QAbstractTableModel):
    def __init__(self, wave: PxpWave) -> None:
        super().__init__()
        self.wave = wave
        self.edits: dict[tuple[int, ...], Any] = {}
        self.plane = [0] * max(0, wave.data.ndim-2)

    def point(self, row: int, col: int) -> tuple[int, ...]:
        return (row,) if self.wave.data.ndim == 1 else (row, col, *self.plane)

    def set_plane(self, dimension: int, value: int) -> None:
        if not 0 <= dimension < len(self.plane) or not 0 <= value < self.wave.shape[dimension+2]:
            raise ValueError("Invalid wave plane")
        self.beginResetModel()
        self.plane[dimension] = value
        self.endResetModel()

    def rowCount(self, parent: Any = None) -> int:
        return 0 if parent is not None and parent.isValid() else self.wave.shape[0]

    def columnCount(self, parent: Any = None) -> int:
        return 0 if parent is not None and parent.isValid() else (self.wave.shape[1] if self.wave.data.ndim >= 2 else 1)

    def value(self, row: int, col: int) -> Any:
        point = self.point(row, col)
        return self.edits.get(point, self.wave.data[point])

    def data(self, index: Any, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if index.isValid() and role in {Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole}:
            value = self.value(index.row(), index.column())
            return str(value) if role == Qt.ItemDataRole.EditRole or self.wave.data.dtype.kind in "SU" else (
                format(float(value), ".8g") if self.wave.data.dtype.kind in "f" else str(value))
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        return f"Point {section}" if orientation == Qt.Orientation.Vertical else (
            self.wave.name if self.wave.data.ndim == 1 else f"Column {section}")

    def flags(self, index: Any) -> Qt.ItemFlag:
        return super().flags(index) | Qt.ItemFlag.ItemIsEditable

    def parse(self, value: str) -> Any:
        kind = self.wave.data.dtype.kind
        if kind in "SU":
            return value
        if kind == "c":
            return complex(value)
        if kind in "biu":
            number = int(value)
            if kind != "b" and not np.iinfo(self.wave.data.dtype.str).min <= number <= np.iinfo(self.wave.data.dtype.str).max:
                raise ValueError("Integer outside wave dtype range.")
            if kind == "b" and number not in {0, 1}:
                raise ValueError("Boolean values must be 0 or 1.")
            return number
        return float(value) if value.strip() else np.nan

    def setData(self, index: Any, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if not index.isValid() or role != Qt.ItemDataRole.EditRole:
            return False
        try:
            self.edits[self.point(index.row(), index.column())] = self.parse(str(value))
        except ValueError:
            return False
        self.dataChanged.emit(index, index, [role, Qt.ItemDataRole.DisplayRole])
        return True

    def result(self) -> PxpWave:
        dtype: Any = self.wave.data.dtype
        if dtype.kind in "SU":
            maximum = max([dtype.itemsize // (4 if dtype.kind == "U" else 1),
                           *(len(str(v)) for v in self.edits.values())])
            dtype = f"U{maximum}"
        if self.wave.data.size * np.dtype(dtype).itemsize > 512 * 1024**2:
            raise ValueError("Edited wave exceeds the 512 MiB memory safety limit")
        data = np.array(self.wave.data, dtype=dtype, copy=True)
        for point, value in self.edits.items():
            data[point] = value
        return make_wave(self.wave.igor_path, data, self.wave)


class WaveEditorDialog(QDialog):
    def __init__(self, parent: Any, wave: PxpWave) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Edit Working Wave — {wave.igor_path}")
        self.resize(750, 570)
        self.model = WaveTableModel(wave)
        self.result_wave: PxpWave | None = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Full precision on edit • Ctrl+C/Ctrl+V: copy/paste cells • Original PXP is unchanged"))
        if wave.data.ndim > 2:
            planes = QFormLayout()
            for dimension, label in enumerate(("Layer (r)", "Chunk (s)")[:wave.data.ndim-2]):
                selector = QSpinBox()
                selector.setRange(0, wave.shape[dimension+2]-1)
                selector.valueChanged.connect(lambda value, d=dimension: self.model.set_plane(d, value))
                planes.addRow(label, selector)
            layout.addLayout(planes)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)
        self.current_value = QLabel()
        self.current_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.current_value)
        self.table.selectionModel().currentChanged.connect(lambda index, _old: self.current_value.setText(
            f"Point {index.row()}, column {index.column()}: {self.model.data(index, Qt.ItemDataRole.EditRole)}"))
        for key, callback in ((QKeySequence.StandardKey.Copy, self.copy), (QKeySequence.StandardKey.Paste, self.paste)):
            shortcut = QShortcut(QKeySequence(key), self.table)
            shortcut.activated.connect(callback)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Apply to Working Wave")
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def copy(self) -> None:
        indexes = self.table.selectedIndexes()
        if not indexes:
            return
        rows = range(min(i.row() for i in indexes), max(i.row() for i in indexes) + 1)
        cols = range(min(i.column() for i in indexes), max(i.column() for i in indexes) + 1)
        QApplication.clipboard().setText("\n".join("\t".join(str(self.model.value(r, c)) for c in cols) for r in rows))

    def paste(self) -> None:
        index = self.table.currentIndex()
        if not index.isValid():
            return
        rows = list(csv.reader(QApplication.clipboard().text().splitlines(), delimiter="\t"))
        pending: dict[tuple[int, ...], Any] = {}
        try:
            for r, row in enumerate(rows):
                for c, value in enumerate(row):
                    point = index.row() + r, index.column() + c
                    if point[0] >= self.model.rowCount() or point[1] >= self.model.columnCount():
                        raise ValueError("Paste exceeds wave dimensions. Insert points/redimension first.")
                    pending[self.model.point(*point)] = self.model.parse(value)
            self.model.edits.update(pending)
            self.model.dataChanged.emit(self.model.index(0, 0), self.model.index(self.model.rowCount() - 1, self.model.columnCount() - 1))
        except ValueError as exc:
            QMessageBox.warning(self, "Paste Values", str(exc))

    def apply(self) -> None:
        try:
            self.result_wave = self.model.result()
            self.accept()
        except ValueError as exc:
            QMessageBox.warning(self, "Wave Edit", str(exc))


class WaveBrowserDialog(QDialog):
    """Select a wave for Data menu operations without leaving the current graph."""

    def __init__(self, parent: Any, title: str, waves: tuple[PxpWave, ...]) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(580, 470)
        layout = QVBoxLayout(self)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter by wave name or folder")
        layout.addWidget(self.filter)
        self.list = QListWidget()
        for wave in waves:
            self.list.addItem(f"{wave.igor_path}   {wave.shape}  {wave.dtype}")
            self.list.item(self.list.count() - 1).setData(Qt.ItemDataRole.UserRole, wave.igor_path)
        layout.addWidget(self.list)
        self.filter.textChanged.connect(self.filter_items)
        if self.list.count():
            self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(lambda _item: self.accept())
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def filter_items(self, text: str) -> None:
        for i in range(self.list.count()):
            item = self.list.item(i)
            item.setHidden(text.casefold() not in item.text().casefold())

    def selected_path(self) -> str:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item and not item.isHidden() else ""


class ScalingDialog(QDialog):
    def __init__(self, parent: Any, wave: PxpWave) -> None:
        super().__init__(parent)
        self.setWindowTitle("Change Wave Scaling")
        self.wave = wave
        self.result_wave: PxpWave | None = None
        form = QFormLayout(self)
        self.dimensions = []
        for index, ((offset, delta), unit) in enumerate(zip(wave.dimension_scaling, wave.dimension_units, strict=True)):
            controls = (QLineEdit(str(offset)), QLineEdit(str(delta)), QLineEdit(unit))
            for caption, widget in zip(("Offset", "Delta", "Unit"), controls, strict=True):
                form.addRow(f"Dimension {index}: {caption}", widget)
            self.dimensions.append(controls)
        self.unit = QLineEdit(wave.data_units)
        form.addRow("Data unit", self.unit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def apply(self) -> None:
        from dataclasses import replace
        try:
            scaling = tuple((float(o.text()), float(d.text())) for o, d, _u in self.dimensions)
            if not all(np.isfinite(v) for pair in scaling for v in pair) or any(d == 0 for _o, d in scaling):
                raise ValueError("Scaling must be finite; Delta must not be zero.")
            self.result_wave = replace(self.wave, dimension_scaling=scaling,
                                       dimension_units=tuple(u.text() for _o, _d, u in self.dimensions), data_units=self.unit.text())
            self.accept()
        except ValueError as exc:
            QMessageBox.warning(self, "Wave Scaling", str(exc))


def choose_text(parent: Any) -> Path | None:
    filename, _ = QFileDialog.getOpenFileName(parent, "Load General Text", "", "General text (*.dat *.csv *.tsv *.txt *.xy);;All files (*)")
    return Path(filename) if filename else None
