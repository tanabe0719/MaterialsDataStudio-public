"""New/append/reorder/replace graph operations, committed as semantic edits."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QVBoxLayout,
)

from materials_data_studio.tools.graph_document import EditableGraphDocument
from materials_data_studio.tools.graph_waves import (
    numeric_waves,
    reorder_traces,
    validate_references,
)
from materials_data_studio.tools.pxp_model import (
    PxpAxis,
    PxpGraph,
    PxpGraphStyle,
    PxpImage,
)


def unique_name(base: str, names: list[str]) -> str:
    candidate, count = base, 1
    while candidate in names:
        count += 1
        candidate = f"{base}_{count}"
    return candidate


class NewGraphDialog(QDialog):
    def __init__(self, parent: Any, document: EditableGraphDocument, graph: PxpGraph | None = None) -> None:
        super().__init__(parent)
        self.document, self.graph = document, graph
        self.result_graph: PxpGraph | None = None
        self.setWindowTitle("Append Traces" if graph else "New Graph")
        self.resize(830, 600)
        layout = QVBoxLayout(self)
        self.type = QComboBox()
        self.type.addItems(["XY plot", "Image plot", "Box plot", "Violin plot"])
        if graph:
            self.type.setCurrentIndex(1 if graph.images else 0)
        layout.addWidget(self.type)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter waves by name / folder")
        layout.addWidget(self.filter)
        lists = QHBoxLayout()
        self.y_list, self.x_list = QListWidget(), QListWidget()
        self.y_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        for title, widget in (("Y waves / image waves (multiple selection)", self.y_list), ("X wave (or wave scaling)", self.x_list)):
            column = QVBoxLayout()
            column.addWidget(QLabel(title))
            column.addWidget(widget)
            lists.addLayout(column)
        layout.addLayout(lists, 1)
        form = QFormLayout()
        self.axis = QComboBox()
        self.axis.setEditable(True)
        self.axis.addItems(list(dict.fromkeys(["left", "right", *(a.name for a in graph.axes if (a.side or a.name) not in {"bottom", "top"})] if graph else ["left", "right"])))
        self.x_axis = QComboBox()
        self.x_axis.setEditable(True)
        self.x_axis.addItems(list(dict.fromkeys(["bottom", "top", *(a.name for a in graph.axes if a.side in {"bottom", "top"})] if graph else ["bottom", "top"])))
        self.swap = QCheckBox("Swap trace X and Y axes")
        self.swap.setChecked(graph.swap_xy if graph else False)
        self.swap.setEnabled(graph is None)
        self.title = QLineEdit(graph.name if graph else unique_name("Graph", [g.name for g in document.current.graphs]))
        self.title.setEnabled(graph is None)
        form.addRow("Graph name", self.title)
        form.addRow("Y axis (type a name to create a new axis)", self.axis)
        form.addRow("X axis (type a name to create a new axis)", self.x_axis)
        form.addRow(self.swap)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Append" if graph else "Create Graph")
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.type.currentIndexChanged.connect(self.populate)
        self.filter.textChanged.connect(self.filter_items)
        self.populate()

    def populate(self, *_args: Any) -> None:
        self.y_list.clear()
        self.x_list.clear()
        self.x_list.addItem("_calculated_ (wave dimension scaling)")
        self.x_list.item(0).setData(Qt.ItemDataRole.UserRole, None)
        self.x_list.setCurrentRow(0)
        image = self.type.currentIndex() == 1
        self.x_list.setEnabled(not image)
        self.axis.setEnabled(not image)
        self.x_axis.setEnabled(not image)
        box = self.type.currentIndex() in {2, 3}
        choices = [w for w in self.document.render_source().waves if w.data.ndim in {1, 2} and w.data.dtype.kind in "biuf"] if box else [w for w in self.document.render_source().waves if w.data.dtype.kind in "biuf" and (w.data.ndim == 2 or (w.data.ndim == 3 and w.shape[2] in {3,4}))] if image else numeric_waves(self.document, 1)
        for wave in choices:
            self.y_list.addItem(f"{wave.igor_path}  {wave.shape}")
            self.y_list.item(self.y_list.count() - 1).setData(Qt.ItemDataRole.UserRole, wave.igor_path)
            if not image:
                self.x_list.addItem(f"{wave.igor_path}  {wave.shape}")
                self.x_list.item(self.x_list.count() - 1).setData(Qt.ItemDataRole.UserRole, wave.igor_path)
        self.filter_items(self.filter.text())

    def filter_items(self, text: str) -> None:
        for widget in (self.y_list, self.x_list):
            for i in range(widget.count()):
                item = widget.item(i)
                item.setHidden(bool(item.data(Qt.ItemDataRole.UserRole)) and text.casefold() not in item.text().casefold())

    def apply(self) -> None:
        try:
            selected = [i.data(Qt.ItemDataRole.UserRole) for i in self.y_list.selectedItems() if not i.isHidden()]
            name = self.title.text().strip()
            if not selected or not name:
                raise ValueError("Select at least one Y/image wave and provide a graph name.")
            if not self.graph and any(g.name == name for g in self.document.current.graphs):
                raise ValueError("A graph with this name already exists.")
            graph = self.graph or PxpGraph(name, axes=(PxpAxis("bottom"), PxpAxis("left")),
                style=PxpGraphStyle(width_points=432, height_points=288))
            if self.type.currentIndex() == 1:
                if any(i.wave in selected for i in graph.images):
                    raise ValueError("An image wave can only be added once to a graph.")
                graph = replace(graph, images=(*graph.images, *(PxpImage(p, color_table="viridis", colorbar_visible=True) for p in selected)))
            elif self.type.currentIndex() in {2, 3}:
                from materials_data_studio.tools.igor_box import append_box
                from materials_data_studio.tools.igor_violin import append_violin
                xitem = self.x_list.currentItem()
                x = xitem.data(Qt.ItemDataRole.UserRole) if xitem and not xitem.isHidden() else None
                graph = (append_box if self.type.currentIndex() == 2 else append_violin)(graph, tuple(selected), x, axis=self.axis.currentText(), x_axis=self.x_axis.currentText())
            else:
                axis = self.axis.currentText().strip()
                if not axis or axis in {"bottom", "top"}:
                    raise ValueError("Choose a Y-axis name other than bottom/top.")
                xitem = self.x_list.currentItem()
                x = xitem.data(Qt.ItemDataRole.UserRole) if xitem and not xitem.isHidden() else None
                from materials_data_studio.tools.command_services import append_trace_model
                for path in selected:
                    graph = append_trace_model(graph, path, x, axis, x_axis=self.x_axis.currentText().strip())
                graph = replace(graph, swap_xy=self.swap.isChecked())
            state = self.document.current.clone()
            state.graphs = (graph,)
            validate_references(self.document, state)
            self.result_graph = graph
            self.accept()
        except ValueError as exc:
            QMessageBox.warning(self, "Graph Waves", str(exc))


class ReorderTracesDialog(QDialog):
    def __init__(self, parent: Any, graph: PxpGraph) -> None:
        super().__init__(parent)
        self.graph = graph
        self.result_graph: PxpGraph | None = None
        self.setWindowTitle("Reorder Traces")
        self.resize(640, 440)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("BACK (top) → FRONT (bottom) • Drag rows to reorder traces"))
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        for trace in graph.traces:
            self.list.addItem(f"{trace.name}:  {trace.y_wave} vs {trace.x_wave or '_calculated_'}")
            self.list.item(self.list.count() - 1).setData(Qt.ItemDataRole.UserRole, trace.name)
        layout.addWidget(self.list)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def apply(self) -> None:
        self.result_graph = reorder_traces(self.graph, [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())])
        self.accept()


class ReplaceWaveDialog(QDialog):
    def __init__(self, parent: Any, document: EditableGraphDocument, graph: PxpGraph, trace_name: str = "") -> None:
        super().__init__(parent)
        self.document, self.graph = document, graph
        self.result_graph: PxpGraph | None = None
        self.setWindowTitle("Replace Wave")
        self.resize(710, 490)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Select traces to replace. X/Y/error lengths must match; styles are retained."))
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        for trace in graph.traces:
            self.list.addItem(f"{trace.name}: {trace.y_wave} vs {trace.x_wave or '_calculated_'}")
            item = self.list.item(self.list.count() - 1)
            item.setData(Qt.ItemDataRole.UserRole, trace.name)
            item.setSelected(trace.name == trace_name or not trace_name)
        layout.addWidget(self.list)
        form = QFormLayout()
        self.y_enabled, self.x_enabled = QCheckBox("Replace Y"), QCheckBox("Replace X")
        self.y_enabled.setChecked(True)
        self.y_wave, self.x_wave = QComboBox(), QComboBox()
        self.x_wave.addItem("_calculated_ (wave scaling)", None)
        for wave in numeric_waves(document):
            self.y_wave.addItem(wave.igor_path, wave.igor_path)
            self.x_wave.addItem(wave.igor_path, wave.igor_path)
        form.addRow(self.y_enabled, self.y_wave)
        form.addRow(self.x_enabled, self.x_wave)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def apply(self) -> None:
        try:
            selected = {i.data(Qt.ItemDataRole.UserRole) for i in self.list.selectedItems()}
            if not selected or not (self.y_enabled.isChecked() or self.x_enabled.isChecked()):
                raise ValueError("Select traces and at least one replacement axis.")
            traces = tuple(replace(t, y_wave=self.y_wave.currentData() if self.y_enabled.isChecked() else t.y_wave,
                                   x_wave=self.x_wave.currentData() if self.x_enabled.isChecked() else t.x_wave)
                           if t.name in selected else t for t in self.graph.traces)
            graph = replace(self.graph, traces=traces)
            state = self.document.current.clone()
            state.graphs = (graph,)
            validate_references(self.document, state)
            self.result_graph = graph
            self.accept()
        except ValueError as exc:
            QMessageBox.warning(self, "Replace Wave", str(exc))
