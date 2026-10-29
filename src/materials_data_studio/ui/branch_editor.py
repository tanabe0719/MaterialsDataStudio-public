"""Transactional branch editing against a virtual raw-data table and graph."""

from __future__ import annotations

import copy
import uuid
from typing import Any, cast

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QAbstractTableModel, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from materials_data_studio.core.measurement_branches import (
    apply_branches,
    automatic_branches,
    measurement_row_count,
)
from materials_data_studio.core.models import Dataset, PlotStyle
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.plotting.plot_spec import CurveSpec, PlotSpec
from materials_data_studio.plotting.pyqtgraph_backend import create_plot_widget, render_plot
from materials_data_studio.ui.action_layout import ActionBar, button_role


class RawMeasurementModel(QAbstractTableModel):
    def __init__(self, dataset: Dataset, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.arrays = {
            key: value
            for key, value in dataset.raw_data.items()
            if value.ndim == 1 and len(value) == measurement_row_count(dataset)
        }
        self.names = list(self.arrays)

    def rowCount(self, parent: Any = None) -> int:
        return (
            0 if parent is not None and parent.isValid() else len(next(iter(self.arrays.values())))
        )

    def columnCount(self, parent: Any = None) -> int:
        return 0 if parent is not None and parent.isValid() else len(self.names)

    def data(self, index: Any, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if index.isValid() and role == Qt.ItemDataRole.DisplayRole:
            return str(self.arrays[self.names[index.column()]][index.row()])
        return None

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> Any:
        if role == Qt.ItemDataRole.DisplayRole:
            return self.names[section] if orientation == Qt.Orientation.Horizontal else str(section)
        return None


class BranchEditor(QDialog):
    def __init__(self, state: ProjectState, root: Dataset, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state, self.root = state, root
        self._definitions = copy.deepcopy(root.metadata.get("branch_definitions", []))
        self.setWindowTitle("Manual branches — " + root.display_name)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.resize(1150, 850)
        layout = QVBoxLayout(self)
        help_label = QLabel(
            "Rows are zero-based, inclusive, and refer to original measurement order. Select raw table rows or drag the graph interval, then use Add / Use selected rows. Raw measurements are retained. Apply commits all changes; Cancel leaves branches unchanged."
        )
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        self.model = RawMeasurementModel(root, self)
        self.raw_table = QTableView()
        self.raw_table.setModel(self.model)
        self.raw_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.raw_table.setSelectionMode(QAbstractItemView.SelectionMode.ContiguousSelection)
        self.signal = QComboBox()
        self.signal.addItems([key for key, values in self.model.arrays.items() if values.dtype.kind in "biuf"])
        for key, values in self.model.arrays.items():
            if values.dtype.kind in "biuf":
                self.signal.setCurrentText(key)
                break
        for key in ("moment_emu", "cp_raw_uj_k", "bridge_1_resistance"):
            if key in self.model.names:
                self.signal.setCurrentText(key)
                break
        layout.addWidget(self.signal)
        self.plot = create_plot_widget()
        self.region = pg.LinearRegionItem((0, max(0, self.model.rowCount() - 1)))
        self.region.setBounds((0, max(0, self.model.rowCount() - 1)))
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.raw_table)
        split.addWidget(self.plot)
        layout.addWidget(split, 2)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            [
                "Branch name",
                "First raw row",
                "Last raw row",
                "Bridge",
                "Mode",
                "Average adjacent pairs",
            ]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self.table, 1)
        edit_actions = []
        delete_actions = []
        reset_actions = []
        for title, callback in (
            ("Add branch", self.add_branch),
            ("Delete branch", self.delete_branch),
            ("Use selected rows", self.use_selection),
            ("Reset automatic branches", self.reset_automatic),
        ):
            button = QPushButton(title)
            button.clicked.connect(callback)
            if title == "Delete branch":
                button_role(button, "destructive")
                delete_actions.append(button)
            elif title.startswith("Reset"):
                reset_actions.append(button)
            else:
                edit_actions.append(button)
        layout.addWidget(ActionBar([edit_actions, delete_actions, reset_actions]))
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.commit)
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).setText("Apply and close")
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.signal.currentTextChanged.connect(self.preview)
        self.table.itemSelectionChanged.connect(self.select_branch)
        self.table.itemChanged.connect(self.preview)
        self._populate()
        self.preview()

    def _populate(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(self._definitions))
        heat = self.root.metadata.get("ppms_kind") == "heat_capacity"
        transport = self.root.metadata.get("ppms_kind") == "resistivity"
        self.table.setColumnHidden(3, not transport)
        self.table.setColumnHidden(4, heat or self.root.metadata.get("ppms_kind") == "generic")
        self.table.setColumnHidden(5, not heat)
        for row, definition in enumerate(self._definitions):
            for column, key in enumerate(("name", "start", "stop", "bridge", "mode")):
                self.table.setItem(row, column, QTableWidgetItem(str(definition.get(key, ""))))
            item = QTableWidgetItem()
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if definition.get("average_pairs", True)
                else Qt.CheckState.Unchecked
            )
            self.table.setItem(row, 5, item)
        self.table.resizeColumnsToContents()
        self.table.blockSignals(False)
        if self._definitions:
            self.table.selectRow(0)

    def _cell(self, row: int, column: int) -> QTableWidgetItem:
        item = self.table.item(row, column)
        assert item is not None
        return item

    def definitions(self) -> list[dict[str, Any]]:
        definitions = copy.deepcopy(self._definitions)
        for row, definition in enumerate(definitions):
            definition.update(
                name=self._cell(row, 0).text().strip(),
                start=int(self._cell(row, 1).text()),
                stop=int(self._cell(row, 2).text()),
            )
            if self.root.metadata.get("ppms_kind") == "resistivity":
                definition["bridge"] = int(self._cell(row, 3).text())
            if self.root.metadata.get("ppms_kind") == "heat_capacity":
                definition["average_pairs"] = (
                    self._cell(row, 5).checkState() == Qt.CheckState.Checked
                )
            elif self.root.metadata.get("ppms_kind") != "generic":
                mode = self._cell(row, 4).text().strip()
                if mode not in {"mt", "mh", "rho_t", "rho_h"}:
                    raise ValueError("Mode must be mt, mh, rho_t or rho_h.")
                definition["mode"] = mode
        return definitions

    def _selection(self) -> tuple[int, int]:
        rows = [index.row() for index in self.raw_table.selectionModel().selectedRows()]
        if rows:
            return min(rows), max(rows)
        low, high = cast(tuple[float, float], self.region.getRegion())
        return max(0, round(float(low))), min(self.model.rowCount() - 1, round(float(high)))

    def _capture(self) -> bool:
        try:
            self._definitions = self.definitions()
        except ValueError as exc:
            QMessageBox.warning(self, "Manual branches", str(exc))
            return False
        return True

    def add_branch(self) -> None:
        if not self._capture():
            return
        start, stop = self._selection()
        bridge = next(
            (
                int(key)
                for key, value in self.root.metadata.get("bridges", {}).items()
                if value.get("available")
            ),
            1,
        )
        self._definitions.append(
            {
                "id": str(uuid.uuid4()),
                "name": "New branch",
                "start": start,
                "stop": stop,
                "bridge": bridge,
                "mode": "generic" if self.root.metadata.get("ppms_kind") == "generic" else "rho_t" if self.root.metadata.get("ppms_kind") == "resistivity" else "mt",
                "average_pairs": True,
            }
        )
        if self.root.metadata.get("ppms_kind") != "resistivity":
            self._definitions[-1].pop("bridge")
        self._populate()
        self.table.selectRow(len(self._definitions) - 1)

    def delete_branch(self) -> None:
        if self._capture() and self.table.currentRow() >= 0:
            self._definitions.pop(self.table.currentRow())
            self._populate()

    def use_selection(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            start, stop = self._selection()
            self._cell(row, 1).setText(str(start))
            self._cell(row, 2).setText(str(stop))

    def reset_automatic(self) -> None:
        self._definitions = automatic_branches(self.root)
        self._populate()

    def select_branch(self) -> None:
        try:
            definition = self.definitions()[self.table.currentRow()]
            self.region.setRegion((definition["start"], definition["stop"]))
        except (IndexError, ValueError):
            pass
        self.preview()

    def preview(self, *_args: Any) -> None:
        key = self.signal.currentText()
        if not key:
            return
        values = np.asarray(self.root.raw_data[key], dtype=float)
        curves = [
            CurveSpec(
                np.arange(len(values), dtype=float),
                values,
                "Raw: " + key,
                PlotStyle(color="#64748B", line_width=1),
            )
        ]
        if self.root.metadata.get("ppms_kind") == "heat_capacity" and self.table.currentRow() >= 0:
            try:
                definition = self.definitions()[self.table.currentRow()]
                if definition.get("average_pairs"):
                    from materials_data_studio.modules.ppms.heat_capacity import (
                        average_adjacent_heat_capacity,
                    )

                    start, stop = definition["start"], definition["stop"] + 1
                    if 0 <= start < stop <= len(values):
                        selection = slice(start, stop)
                        averaged = average_adjacent_heat_capacity(
                            self.root.raw_data["temperature_k"][selection],
                            {
                                "signal": values[selection],
                                "field_oe": self.root.raw_data["field_oe"][selection],
                                "source_row_index": np.arange(start, stop, dtype=float),
                            },
                        )
                        curves.append(
                            CurveSpec(
                                (averaged["source_row_start"] + averaged["source_row_stop"]) / 2,
                                averaged["signal"],
                                "Averaged preview",
                                PlotStyle(
                                    color="#DC2626", marker="o", line_style="none", marker_size=5
                                ),
                            )
                        )
            except (ValueError, IndexError):
                pass
        render_plot(self.plot, PlotSpec("Original measurement index", key, curves))
        plot_item = self.plot.getPlotItem()
        assert plot_item is not None
        if self.region not in plot_item.items:
            self.plot.addItem(self.region)

    def commit(self) -> None:
        try:
            apply_branches(self.state, self.root, self.definitions())
        except ValueError as exc:
            QMessageBox.warning(self, "Manual branches", str(exc))
            return
        self.accept()
