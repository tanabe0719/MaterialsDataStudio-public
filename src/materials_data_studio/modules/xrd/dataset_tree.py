"""XRD datasets with independently styled, parent-linked phase annotations."""
from __future__ import annotations

from typing import cast

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QMenu, QTreeWidget, QTreeWidgetItem

from materials_data_studio.ui.dataset_list import DatasetList
from materials_data_studio.ui.measurement_tree import MeasurementTree


class _OrderedTree(QTreeWidget):
    dropped = Signal()

    def dropEvent(self, event) -> None:
        super().dropEvent(event)
        self.dropped.emit()


class XrdDatasetTree(MeasurementTree):
    tree_widget_class = _OrderedTree

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.list_widget.setDragDropMode(QTreeWidget.DragDropMode.InternalMove)
        self.list_widget.setDefaultDropAction(Qt.DropAction.MoveAction)
        cast(_OrderedTree, self.list_widget).dropped.connect(self._rows_moved)

    def _dataset(self, item: QTreeWidgetItem | None):
        identifier = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        return getattr(self, "_by_id", {}).get(identifier)

    def _rows_moved(self) -> None:
        ordered = []
        def visit(item):
            dataset = self._dataset(item)
            if dataset is None:
                raise ValueError("Unknown dataset")
            parent = self._dataset(item.parent())
            if dataset.metadata.get("phase_parent") != (parent.dataset_id if parent else None):
                raise ValueError("Annotations must remain under their measured parent")
            ordered.append(dataset.dataset_id)
            for index in range(item.childCount()):
                visit(item.child(index))
        try:
            for index in range(self.list_widget.topLevelItemCount()):
                visit(self.list_widget.topLevelItem(index))
        except ValueError:
            self.refresh()
            return
        slots = iter(ordered)
        self.state.reorder([next(slots) if dataset.module_type == self.module_filter else dataset.dataset_id for dataset in self.state.datasets])
        self.refresh()
        self.datasets_changed.emit()

    def _filter(self) -> None:
        text = self.search.text().casefold()
        def visit(item, inherited=False):
            dataset = self._dataset(item)
            tags = " ".join(dataset.metadata.get("tags", [])) if dataset else ""
            matches = inherited or text in (item.text(0) + " " + tags).casefold()
            children = [visit(item.child(index), matches) for index in range(item.childCount())]
            shown = matches or any(children)
            item.setHidden(not shown)
            return shown
        for index in range(self.list_widget.topLevelItemCount()):
            visit(self.list_widget.topLevelItem(index))
    def _sync_styles(self) -> None:
        for dataset in self.datasets():
            if dataset.metadata.get("phase_annotation"):
                dataset.plot_style.marker_edge_color = dataset.plot_style.color

    def _set_selected_visibility(self, visible: bool) -> None:
        for dataset in self.selected_datasets():
            dataset.visible = visible
        self.state.modified = True
        self.refresh()
        self.datasets_changed.emit()

    def refresh(self) -> None:
        selected = self.current_dataset()
        selected_id = selected.dataset_id if selected else None
        expanded = {key: item.isExpanded() for key, item in self._items.items()}
        self._updating = True
        self.list_widget.clear()
        self._items.clear()
        datasets = self.datasets()
        self._by_id = {dataset.dataset_id: dataset for dataset in datasets}
        for dataset in datasets:
            item = QTreeWidgetItem([dataset.display_name])
            item.setData(0, Qt.ItemDataRole.UserRole, dataset.dataset_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEditable)
            item.setCheckState(0, Qt.CheckState.Checked if dataset.visible else Qt.CheckState.Unchecked)
            item.setIcon(0, DatasetList._style_icon(dataset))
            self._items[dataset.dataset_id] = item
        for dataset in datasets:
            item = self._items[dataset.dataset_id]
            parent = self._items.get(dataset.metadata.get("phase_parent", ""))
            if parent is not None and parent is not item:
                parent.addChild(item)
            else:
                self.list_widget.addTopLevelItem(item)
            item.setExpanded(expanded.get(dataset.dataset_id, True))
        target = self._items.get(selected_id) if selected_id else None
        if target is None and self.list_widget.topLevelItemCount():
            target = self.list_widget.topLevelItem(0)
        if target is not None:
            self.list_widget.setCurrentItem(target)
        self._updating = False
        self._filter()
        self.refresh_diagnostics()
        self.current_dataset_changed.emit(self.current_dataset())

    def _duplicate(self) -> None:
        selected = self.selected_datasets()
        mapping = {}
        copies = []
        for dataset in selected:
            duplicate = dataset.duplicate()
            mapping[dataset.dataset_id] = duplicate.dataset_id
            copies.append(duplicate)
        for duplicate in copies:
            parent = duplicate.metadata.get("phase_parent")
            if parent in mapping:
                duplicate.metadata["phase_parent"] = mapping[parent]
            self.state.add_dataset(duplicate)
        self.refresh()
        self.datasets_changed.emit()

    def _remove(self) -> None:
        ids = {dataset.dataset_id for dataset in self.selected_datasets()}
        self.state.datasets[:] = [dataset for dataset in self.state.datasets
                                  if dataset.dataset_id not in ids and dataset.metadata.get("phase_parent") not in ids]
        self.state.modified = True
        self.refresh()
        self.datasets_changed.emit()

    def _move(self, direction: int) -> None:
        selected = self.current_dataset()
        if selected is None:
            return
        siblings = [dataset for dataset in self.datasets()
                    if dataset.metadata.get("phase_parent") == selected.metadata.get("phase_parent")]
        index = siblings.index(selected)
        if 0 <= index + direction < len(siblings):
            other = siblings[index + direction]
            selected.plot_order, other.plot_order = other.plot_order, selected.plot_order
            self.state.modified = True
            self.refresh()
            self.datasets_changed.emit()

    def _context_menu(self, point) -> None:
        item = self.list_widget.itemAt(point)
        if item is not None:
            self.list_widget.setCurrentItem(item)
        menu = QMenu(self)
        from materials_data_studio.ui.measurement_overview import measurement_overview
        menu.addAction("Measurement order / segmentation…").triggered.connect(lambda: measurement_overview(self))
        for label, callback in (("Show selected", lambda: self._set_selected_visibility(True)),
                                ("Hide selected", lambda: self._set_selected_visibility(False)),
                                ("Style selected…", self._style_selected), ("Rename", self._rename),
                                ("Tag selected…", self._tag_selected),
                                ("Move up", lambda: self._move(-1)), ("Move down", lambda: self._move(1)),
                                ("Duplicate selected", self._duplicate), ("Remove selected", self._remove)):
            menu.addAction(label).triggered.connect(callback)
        menu.exec(self.list_widget.viewport().mapToGlobal(point))
        menu.deleteLater()
