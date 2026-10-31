"""File / bridge / branch navigation for magnetic and transport measurements."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QInputDialog,
    QLineEdit,
    QMenu,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from materials_data_studio.core.measurement_branches import ensure_hierarchies, root_dataset
from materials_data_studio.core.models import Dataset, ModuleType
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.plotting.palettes import PALETTE_STOPS
from materials_data_studio.ui.action_layout import (
    ActionBar,
    DiagnosticLabel,
    button_role,
    diagnostic_row,
    palette_section,
)
from materials_data_studio.ui.dataset_list import DatasetList


class MeasurementTree(QWidget):
    tree_widget_class = QTreeWidget
    current_dataset_changed = Signal(object)
    dataset_activated = Signal(object)
    datasets_changed = Signal()

    def __init__(
        self,
        state: ProjectState,
        module_filter: ModuleType,
        parent: QWidget | None = None,
        *,
        dataset_predicate: Callable[[Dataset], bool] | None = None,
        kind: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.state, self.module_filter = state, module_filter
        self.dataset_predicate, self.kind = dataset_predicate, kind
        self._updating = False
        self._items: dict[str, QTreeWidgetItem] = {}
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter files / branches…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        self.list_widget = self.tree_widget_class()
        self.list_widget.setHeaderHidden(True)
        self.list_widget.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.list_widget.currentItemChanged.connect(self._emit_current)
        self.list_widget.itemChanged.connect(self._item_changed)
        self.list_widget.itemDoubleClicked.connect(
            lambda: self.dataset_activated.emit(self.current_dataset())
        )
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._context_menu)
        self.details = DiagnosticLabel()
        self.details.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.search)
        layout.addWidget(self.list_widget, 1)
        layout.addWidget(diagnostic_row(self.details, self))
        self.palette_combo = QComboBox()
        self.palette_combo.addItems([*PALETTE_STOPS, "Custom"])
        self._custom_stops = ("#2563EB", "#DC2626")
        palette_buttons = []
        for title, callback in (
            ("Apply colors", self._apply_palette),
            ("Custom…", self._choose_custom_palette),
        ):
            button = QPushButton(title)
            button.clicked.connect(callback)
            palette_buttons.append(button)
        layout.addSpacing(6)
        layout.addWidget(palette_section(self.palette_combo, palette_buttons, kind or str(module_filter)))
        selected_buttons = []
        for title, callback in (
            ("Duplicate", self._duplicate),
            ("Rename", self._rename),
            ("Remove", self._remove),
        ):
            button = QPushButton(title)
            button.clicked.connect(callback)
            if title == "Remove":
                button_role(button, "destructive")
            selected_buttons.append(button)
        layout.addWidget(ActionBar([selected_buttons[:2], selected_buttons[2:]]))
        self.datasets_changed.connect(self._sync_styles)

    def _sync_styles(self) -> None:
        for dataset in self.datasets():
            definition = dataset.metadata.get("measurement_branch", {})
            bridge = definition.get("bridge")
            if bridge is not None:
                setting = dataset.metadata.get("bridge_settings", {}).get(str(bridge), {})
                setting["style"] = dataset.plot_style.to_dict()

    def _choose_custom_palette(self) -> None:
        DatasetList._choose_custom_palette(cast(Any, self))

    def _apply_palette(self) -> None:
        DatasetList._apply_palette(cast(Any, self))

    def _style_selected(self) -> None:
        DatasetList._style_selected(cast(Any, self))

    def _tag_selected(self) -> None:
        DatasetList._tag_selected(cast(Any, self))

    def _duplicate(self) -> None:
        import copy
        import uuid

        from materials_data_studio.core.measurement_branches import apply_branches

        selected = self.selected_datasets()
        root_ids = {
            dataset.dataset_id
            for dataset in selected
            if dataset.metadata.get("measurement_container")
        }
        for dataset in selected:
            parent_id = dataset.metadata.get("measurement_parent")
            if parent_id in root_ids:
                continue
            if parent_id:
                root = root_dataset(self.state, dataset)
                definition = copy.deepcopy(dataset.metadata["measurement_branch"])
                definition.update(id=str(uuid.uuid4()), name=dataset.display_name + " copy")
                apply_branches(self.state, root, [*root.metadata["branch_definitions"], definition])
                child = next(
                    item
                    for item in self.state.datasets
                    if item.metadata.get("measurement_branch", {}).get("id") == definition["id"]
                )
                child.plot_style = copy.deepcopy(dataset.plot_style)
                child.plot_style.legend_text = definition["name"]
                child.metadata = copy.deepcopy(dataset.metadata)
                child.metadata.update(
                    measurement_parent=root.dataset_id, measurement_branch=definition
                )
                child.analysis_results = copy.deepcopy(dataset.analysis_results)
                child.processed_data = {
                    key: value.copy() for key, value in dataset.processed_data.items()
                }
            else:
                duplicate = dataset.duplicate()
                self.state.add_dataset(duplicate)
                definitions = copy.deepcopy(dataset.metadata.get("branch_definitions", []))
                originals = {
                    item.metadata["measurement_branch"]["id"]: item
                    for item in self.state.datasets
                    if item.metadata.get("measurement_parent") == dataset.dataset_id
                }
                mapping = {}
                for definition in definitions:
                    original = originals.get(definition["id"])
                    definition["id"] = str(uuid.uuid4())
                    mapping[definition["id"]] = original
                apply_branches(self.state, duplicate, definitions)
                for child in self.state.datasets:
                    if child.metadata.get("measurement_parent") == duplicate.dataset_id:
                        original = mapping.get(child.metadata["measurement_branch"]["id"])
                        if original is not None:
                            definition = child.metadata["measurement_branch"]
                            child.metadata = copy.deepcopy(original.metadata)
                            child.metadata.update(
                                measurement_parent=duplicate.dataset_id,
                                measurement_branch=definition,
                            )
                            child.plot_style = copy.deepcopy(original.plot_style)
                            child.analysis_results = copy.deepcopy(original.analysis_results)
                            child.processed_data = {
                                key: value.copy() for key, value in original.processed_data.items()
                            }
                            child.visible = original.visible
                            if "branch_analysis_controls" in original.metadata:
                                child.metadata["branch_analysis_controls"] = copy.deepcopy(
                                    original.metadata["branch_analysis_controls"]
                                )
        self.refresh()
        self.datasets_changed.emit()

    def datasets(self) -> list[Dataset]:
        return sorted(
            [
                item
                for item in self.state.datasets
                if item.module_type == self.module_filter
                and (self.dataset_predicate is None or self.dataset_predicate(item))
            ],
            key=lambda item: item.plot_order,
        )

    def _dataset(self, item: QTreeWidgetItem | None) -> Dataset | None:
        identifier = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        return next(
            (dataset for dataset in self.state.datasets if dataset.dataset_id == identifier), None
        )

    def current_dataset(self) -> Dataset | None:
        return self._dataset(self.list_widget.currentItem())

    def selected_datasets(self) -> list[Dataset]:
        ids = set()

        def collect(item: QTreeWidgetItem) -> None:
            ids.add(item.data(0, Qt.ItemDataRole.UserRole))
            for index in range(item.childCount()):
                child = item.child(index)
                if child is not None:
                    collect(child)

        for item in self.list_widget.selectedItems():
            collect(item)
        roots = {
            dataset.dataset_id
            for dataset in self.datasets()
            if dataset.dataset_id in ids and dataset.metadata.get("measurement_container")
        }
        return [
            dataset
            for dataset in self.datasets()
            if dataset.dataset_id in ids or dataset.metadata.get("measurement_parent") in roots
        ]

    def refresh(self) -> None:
        selected = self.current_dataset()
        selection_key = f"measurement_selection/{self.module_filter.value}/{self.kind or ''}"
        selected_id = (
            selected.dataset_id if selected else self.state.axis_settings.get(selection_key)
        )
        ensure_hierarchies(self.state, self.module_filter, self.kind)
        self._updating = True
        self.list_widget.clear()
        self._items.clear()
        bridges: dict[tuple[str, int], QTreeWidgetItem] = {}
        for dataset in self.datasets():
            parent_id = str(dataset.metadata.get("measurement_parent", ""))
            parent = self._items.get(parent_id)
            definition = dataset.metadata.get("measurement_branch", {})
            bridge = definition.get("bridge")
            if parent is not None and bridge is not None:
                key = (parent_id, int(bridge))
                if key not in bridges:
                    group = QTreeWidgetItem(parent, [f"Bridge {bridge}"])
                    group.setFlags(group.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    group.setCheckState(0, Qt.CheckState.Checked)
                    bridges[key] = group
                parent = bridges[key]
            item = QTreeWidgetItem([dataset.display_name])
            item.setData(0, Qt.ItemDataRole.UserRole, dataset.dataset_id)
            item.setFlags(
                item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEditable
            )
            item.setCheckState(
                0, Qt.CheckState.Checked if dataset.visible else Qt.CheckState.Unchecked
            )
            item.setIcon(0, DatasetList._style_icon(dataset))
            if parent is None:
                self.list_widget.addTopLevelItem(item)
            else:
                parent.addChild(item)
            self._items[dataset.dataset_id] = item
        self.list_widget.expandAll()
        target = self._items.get(selected_id) if isinstance(selected_id, str) else None
        if target is None:
            target = next(
                (
                    self._items[item.dataset_id]
                    for item in self.datasets()
                    if item.metadata.get("measurement_parent")
                ),
                None,
            )
        if target is None and self.list_widget.topLevelItemCount():
            target = self.list_widget.topLevelItem(0)
        if target is not None:
            self.list_widget.setCurrentItem(target)
        self._updating = False
        self._filter()
        self.refresh_diagnostics()

    def _emit_current(self) -> None:
        if not self._updating:
            dataset = self.current_dataset()
            if dataset is not None:
                self.state.axis_settings[
                    f"measurement_selection/{self.module_filter.value}/{self.kind or ''}"
                ] = dataset.dataset_id
            self.current_dataset_changed.emit(self.current_dataset())
            self.refresh_diagnostics()

    def _filter(self) -> None:
        text = self.search.text().casefold()

        def visit(item: QTreeWidgetItem | None, inherited: bool = False) -> bool:
            if item is None:
                return False
            matches = inherited or text in item.text(0).casefold()
            child_matches = [
                visit(item.child(index), matches) for index in range(item.childCount())
            ]
            shown = matches or any(child_matches)
            item.setHidden(not shown)
            return shown

        for index in range(self.list_widget.topLevelItemCount()):
            visit(self.list_widget.topLevelItem(index))

    def _item_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        if self._updating:
            return
        dataset = self._dataset(item)
        if (
            dataset is not None
            and dataset.display_name == item.text(0).strip()
            and dataset.visible == (item.checkState(0) == Qt.CheckState.Checked)
        ):
            # Icons/tooltips also emit itemChanged; diagnostics must not trigger
            # another render (which would update the tooltip recursively).
            return
        self._updating = True
        if dataset is not None:
            old_name = dataset.display_name
            dataset.display_name = item.text(0).strip() or dataset.display_name
            if dataset.plot_style.legend_text.endswith(" · " + old_name):
                dataset.plot_style.legend_text = (
                    dataset.plot_style.legend_text[: -len(old_name)] + dataset.display_name
                )
            dataset.visible = item.checkState(0) == Qt.CheckState.Checked
            definition = dataset.metadata.get("measurement_branch")
            if definition:
                definition["name"] = dataset.display_name
                root = root_dataset(self.state, dataset)
                for stored in root.metadata.get("branch_definitions", []):
                    if stored["id"] == definition["id"]:
                        stored["name"] = dataset.display_name

        def descendants(parent: QTreeWidgetItem) -> None:
            for index in range(parent.childCount()):
                child = parent.child(index)
                child.setCheckState(0, item.checkState(0))
                value = self._dataset(child)
                if value:
                    value.visible = item.checkState(0) == Qt.CheckState.Checked
                descendants(child)

        descendants(item)
        self._updating = False
        self.state.modified = True
        self.datasets_changed.emit()

    def refresh_diagnostics(self) -> None:
        dataset = self.current_dataset()
        self.details.setText(
            (dataset.plot_warning or dataset.display_name)
            if dataset
            else "Select a branch to style or analyze."
        )
        for value in self.datasets():
            item = self._items.get(value.dataset_id)
            if item:
                item.setToolTip(
                    0, "\n".join([value.source_filename, value.plot_warning, *value.warnings])
                )

    def refresh_style_indicators(self) -> None:
        for dataset in self.datasets():
            item = self._items.get(dataset.dataset_id)
            if item:
                item.setIcon(0, DatasetList._style_icon(dataset))

    def _rename(self) -> None:
        dataset = self.current_dataset()
        if dataset:
            name, accepted = QInputDialog.getText(self, "Rename", "Name", text=dataset.display_name)
            if accepted and name.strip():
                self._items[dataset.dataset_id].setText(0, name.strip())

    def _remove(self) -> None:
        from materials_data_studio.core.measurement_branches import apply_branches

        selected = self.selected_datasets()
        for dataset in selected:
            if not any(value is dataset for value in self.state.datasets):
                continue
            root = root_dataset(self.state, dataset)
            if dataset is root:
                self.state.datasets = [
                    item
                    for item in self.state.datasets
                    if item is not root
                    and item.metadata.get("measurement_parent") != root.dataset_id
                ]
            else:
                definitions = [
                    value
                    for value in root.metadata["branch_definitions"]
                    if value["id"] != dataset.metadata["measurement_branch"]["id"]
                ]
                apply_branches(self.state, root, definitions)
        self.state.modified = True
        self.refresh()
        self.datasets_changed.emit()

    def _move(self, direction: int) -> None:
        from materials_data_studio.core.measurement_branches import apply_branches

        dataset = self.current_dataset()
        if dataset is None:
            return
        root = root_dataset(self.state, dataset)
        if dataset is not root:
            definitions = list(root.metadata["branch_definitions"])
            index = next(
                index
                for index, item in enumerate(definitions)
                if item["id"] == dataset.metadata["measurement_branch"]["id"]
            )
            target = index + direction
            if not 0 <= target < len(definitions):
                return
            definitions[index], definitions[target] = definitions[target], definitions[index]
            apply_branches(self.state, root, definitions)
        else:
            roots = [
                item for item in self.datasets() if not item.metadata.get("measurement_parent")
            ]
            index = next(index for index, item in enumerate(roots) if item is root)
            target = index + direction
            if not 0 <= target < len(roots):
                return
            roots[index], roots[target] = roots[target], roots[index]
            root_ids = {item.dataset_id for item in roots}
            slots = iter(roots)
            groups = []
            for item in self.state.datasets:
                if item.metadata.get("measurement_parent"):
                    continue
                chosen = next(slots) if item.dataset_id in root_ids else item
                groups.append(chosen.dataset_id)
                groups.extend(
                    child.dataset_id
                    for child in self.state.datasets
                    if child.metadata.get("measurement_parent") == chosen.dataset_id
                )
            self.state.reorder(groups)
        self.refresh()
        self.datasets_changed.emit()

    def _context_menu(self, point) -> None:
        item = self.list_widget.itemAt(point)
        if item is not None:
            self.list_widget.setCurrentItem(item)
        dataset = self.current_dataset()
        menu = QMenu(self)
        if dataset is not None:
            action = menu.addAction("Manually configure branches…")
            action.triggered.connect(self.edit_branches)
        menu.addAction("Style selected…").triggered.connect(self._style_selected)
        menu.addAction("Tag selected…").triggered.connect(self._tag_selected)
        menu.addAction("Duplicate selected").triggered.connect(self._duplicate)
        menu.addAction("Move up").triggered.connect(lambda: self._move(-1))
        menu.addAction("Move down").triggered.connect(lambda: self._move(1))
        menu.addAction("Rename").triggered.connect(self._rename)
        menu.addAction("Remove").triggered.connect(self._remove)
        menu.exec(self.list_widget.viewport().mapToGlobal(point))
        menu.deleteLater()

    def edit_branches(self) -> None:
        from materials_data_studio.ui.branch_editor import BranchEditor

        dataset = self.current_dataset()
        if dataset is None:
            return
        dialog = BranchEditor(self.state, root_dataset(self.state, dataset), self)
        if dialog.exec():
            self.refresh()
            self.current_dataset_changed.emit(self.current_dataset())
            self.datasets_changed.emit()
        dialog.deleteLater()
