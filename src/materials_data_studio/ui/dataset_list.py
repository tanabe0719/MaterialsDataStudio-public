"""Reusable module-filtered dataset list controls."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QInputDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from materials_data_studio.core.models import Dataset, ModuleType
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.plotting.palettes import PALETTE_STOPS, gradient_colors
from materials_data_studio.ui.action_layout import (
    ActionBar,
    DiagnosticLabel,
    button_role,
    diagnostic_row,
    palette_section,
)


class DatasetList(QWidget):
    """Dataset selection, visibility, rename, duplicate, remove, and reorder UI."""

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
    ) -> None:
        super().__init__(parent)
        self.state = state
        self.module_filter = module_filter
        self.dataset_predicate = dataset_predicate
        self._updating = False
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter datasets…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        self.details = DiagnosticLabel("Import or drop measurement files to begin.")
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._context_menu)
        self.list_widget.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.list_widget.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list_widget.currentItemChanged.connect(self._emit_current)
        self.list_widget.itemDoubleClicked.connect(self._emit_activated)
        self.list_widget.itemChanged.connect(self._item_changed)
        self.list_widget.model().rowsMoved.connect(self._rows_moved)
        self.palette_combo = QComboBox()
        self.palette_combo.addItems([*PALETTE_STOPS, "Custom"])
        self._custom_stops = ("#2563EB", "#DC2626")
        apply_palette = QPushButton("Apply colors")
        custom_palette = QPushButton("Custom…")
        apply_palette.clicked.connect(self._apply_palette)
        custom_palette.clicked.connect(self._choose_custom_palette)

        duplicate = QPushButton("Duplicate")
        rename = QPushButton("Rename")
        remove = QPushButton("Remove")
        duplicate.clicked.connect(self._duplicate)
        rename.clicked.connect(self._rename)
        remove.clicked.connect(self._remove)
        button_role(remove, "destructive")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.search)
        layout.addWidget(self.list_widget, 1)
        layout.addWidget(diagnostic_row(self.details, self))
        layout.addSpacing(6)
        layout.addWidget(palette_section(self.palette_combo, [apply_palette, custom_palette], str(module_filter)))
        layout.addWidget(ActionBar([[duplicate, rename], [remove]]))
        self.refresh()

    def _filter(self) -> None:
        text = self.search.text().casefold()
        by_id = {dataset.dataset_id: dataset for dataset in self.state.datasets}
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            dataset = by_id.get(item.data(Qt.ItemDataRole.UserRole))
            tags = " ".join(dataset.metadata.get("tags", [])) if dataset else ""
            item.setHidden(text not in (item.text() + " " + tags).casefold())

    def refresh_diagnostics(self) -> None:
        active = self.current_dataset()
        by_id = {dataset.dataset_id: dataset for dataset in self.state.datasets}
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            dataset = by_id.get(item.data(Qt.ItemDataRole.UserRole))
            if dataset is not None:
                warning = dataset.plot_warning
                status = "Fit available" if dataset.analysis_results else "Raw / processed data"
                item.setToolTip(
                    "\n".join(
                        filter(
                            None, (dataset.source_filename, status, warning, *dataset.warnings[:8])
                        )
                    )
                )
        if active is not None:
            warning = active.plot_warning or str(active.metadata.get("analysis_status", ""))
            self.details.setText(
                warning
                or ("Fit available · " if active.analysis_results else "") + active.display_name
            )
            self.details.setToolTip("\n".join([active.source_filename, *active.warnings]))
        else:
            self.details.setText("Import or drop measurement files to begin.")

    @staticmethod
    def _style_icon(dataset: Dataset) -> QIcon:
        pixmap = QPixmap(32, 18)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        colors = [dataset.plot_style.color]
        for branch in ("zfc_style", "fc_style"):
            stored = dataset.metadata.get(branch)
            if isinstance(stored, dict) and isinstance(stored.get("color"), str):
                colors.append(str(stored["color"]))
        segment_width = 26.0 / len(colors)
        for index, color_text in enumerate(colors):
            color = QColor(color_text)
            pen = QPen(color, max(1, min(3, dataset.plot_style.line_width)))
            pen.setStyle({"--": Qt.PenStyle.DashLine, ":": Qt.PenStyle.DotLine, "-.": Qt.PenStyle.DashDotLine, "none": Qt.PenStyle.NoPen}.get(dataset.plot_style.line_style, Qt.PenStyle.SolidLine))
            painter.setPen(pen)
            start = 3.0 + index * segment_width
            painter.drawLine(QPointF(start, 9), QPointF(start + segment_width, 9))
        if dataset.plot_style.marker.lower() != "none":
            color = QColor(dataset.plot_style.color)
            painter.setPen(QPen(QColor(dataset.plot_style.marker_edge_color or color.name()), 1.2))
            fill = dataset.plot_style.marker_fill_color
            painter.setBrush(Qt.BrushStyle.NoBrush if fill == "none" else QBrush(QColor(fill or color.name())))
            symbol = dataset.plot_style.marker
            if symbol == "o":
                painter.drawEllipse(QPointF(16, 9), 3, 3)
            elif symbol == "s":
                painter.drawRect(13, 6, 6, 6)
            elif symbol in {"d", "D", "^", "v"}:
                points = {"d": [(16, 5), (20, 9), (16, 13), (12, 9)], "D": [(16, 5), (20, 9), (16, 13), (12, 9)], "^": [(16, 5), (20, 12), (12, 12)], "v": [(12, 6), (20, 6), (16, 13)]}
                painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in points[symbol]]))
            elif symbol in {"dagger", "|"}:
                painter.drawLine(16, 3, 16, 15)
                if symbol == "dagger":
                    painter.drawLine(12, 7, 20, 7)
            else:
                for points in ([(13, 6, 19, 12), (13, 12, 19, 6)] if symbol == "x" else [(12, 9, 20, 9), (16, 5, 16, 13)]):
                    painter.drawLine(*points)
        painter.end()
        return QIcon(pixmap)

    def datasets(self) -> list[Dataset]:
        """Return datasets shown by this module list."""
        return sorted(
            (
                dataset
                for dataset in self.state.datasets
                if dataset.module_type is self.module_filter
                and (self.dataset_predicate is None or self.dataset_predicate(dataset))
            ),
            key=lambda dataset: dataset.plot_order,
        )

    def current_dataset(self) -> Dataset | None:
        """Return the selected dataset."""
        item = self.list_widget.currentItem()
        if item is None:
            return None
        dataset_id = str(item.data(Qt.ItemDataRole.UserRole))
        return next(
            (dataset for dataset in self.state.datasets if dataset.dataset_id == dataset_id),
            None,
        )

    def refresh(self) -> None:
        """Rebuild list items while preserving selection."""
        selected = self.current_dataset()
        selected_id = selected.dataset_id if selected is not None else None
        self._updating = True
        self.list_widget.clear()
        for dataset in self.datasets():
            item = QListWidgetItem(dataset.display_name)
            item.setIcon(self._style_icon(dataset))
            item.setData(Qt.ItemDataRole.UserRole, dataset.dataset_id)
            item.setFlags(
                item.flags()
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEditable
                | Qt.ItemFlag.ItemIsDragEnabled
            )
            item.setCheckState(
                Qt.CheckState.Checked if dataset.visible else Qt.CheckState.Unchecked
            )
            self.list_widget.addItem(item)
            if dataset.dataset_id == selected_id:
                self.list_widget.setCurrentItem(item)
        self._updating = False
        if self.list_widget.currentItem() is None and self.list_widget.count():
            self.list_widget.setCurrentRow(0)
        self._filter()
        self.refresh_diagnostics()

    def refresh_style_indicators(self) -> None:
        """Refresh line/marker color swatches without rebuilding the list."""
        by_id = {dataset.dataset_id: dataset for dataset in self.datasets()}
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            dataset = by_id.get(str(item.data(Qt.ItemDataRole.UserRole)))
            if dataset is not None:
                item.setIcon(self._style_icon(dataset))

    def _choose_custom_palette(self) -> None:
        start = QColorDialog.getColor(QColor(self._custom_stops[0]), self, "Custom Gradient Start")
        if not start.isValid():
            return
        end = QColorDialog.getColor(QColor(self._custom_stops[1]), self, "Custom Gradient End")
        if not end.isValid():
            return
        self._custom_stops = (start.name(), end.name())
        self.palette_combo.setCurrentText("Custom")

    def _apply_palette(self) -> None:
        datasets = self.datasets()
        stops = (
            self._custom_stops
            if self.palette_combo.currentText() == "Custom"
            else PALETTE_STOPS[self.palette_combo.currentText()]
        )
        for dataset, color in zip(datasets, gradient_colors(stops, len(datasets)), strict=True):
            dataset.plot_style.color = color
            dataset.plot_style.marker_edge_color = None
            for branch in ("zfc_style", "fc_style"):
                stored = dataset.metadata.get(branch)
                if isinstance(stored, dict):
                    stored["color"] = color
                    stored["marker_edge_color"] = None
        self.state.modified = bool(datasets) or self.state.modified
        self.refresh_style_indicators()
        self.datasets_changed.emit()

    def _emit_current(self) -> None:
        self.current_dataset_changed.emit(self.current_dataset())
        self.refresh_diagnostics()

    def _emit_activated(self) -> None:
        self.dataset_activated.emit(self.current_dataset())

    def _item_changed(self, item: QListWidgetItem) -> None:
        if self._updating:
            return
        dataset_id = str(item.data(Qt.ItemDataRole.UserRole))
        dataset = next(
            dataset for dataset in self.state.datasets if dataset.dataset_id == dataset_id
        )
        dataset.display_name = item.text().strip() or dataset.display_name
        dataset.visible = item.checkState() == Qt.CheckState.Checked
        self.state.modified = True
        self.datasets_changed.emit()

    def _rows_moved(self) -> None:
        if self._updating:
            return
        module_ids = [
            str(self.list_widget.item(row).data(Qt.ItemDataRole.UserRole))
            for row in range(self.list_widget.count())
        ]
        shown_ids = set(module_ids)
        reordered = iter(module_ids)
        all_ids = [
            dataset.dataset_id
            for dataset in sorted(self.state.datasets, key=lambda item: item.plot_order)
        ]
        self.state.reorder(
            [next(reordered) if dataset_id in shown_ids else dataset_id for dataset_id in all_ids]
        )
        self.datasets_changed.emit()

    def _duplicate(self) -> None:
        for dataset in self.selected_datasets():
            self.state.add_dataset(dataset.duplicate())
        self.refresh()
        self.datasets_changed.emit()

    def _rename(self) -> None:
        item = self.list_widget.currentItem()
        if item is not None:
            self.list_widget.editItem(item)

    def _remove(self) -> None:
        for dataset in self.selected_datasets():
            self.state.remove_dataset(dataset.dataset_id)
        self.refresh()
        self.datasets_changed.emit()

    def selected_datasets(self) -> list[Dataset]:
        ids = {item.data(Qt.ItemDataRole.UserRole) for item in self.list_widget.selectedItems()}
        return [dataset for dataset in self.datasets() if dataset.dataset_id in ids]

    def _context_menu(self, point) -> None:
        menu = QMenu(self)
        from materials_data_studio.ui.measurement_overview import measurement_overview
        menu.addAction("Measurement order / segmentation…").triggered.connect(lambda: measurement_overview(self))
        for title, callback in (("Show selected", lambda: self._set_selected_visibility(True)), ("Hide selected", lambda: self._set_selected_visibility(False)), ("Style selected…", self._style_selected), ("Tag selected…", self._tag_selected), ("Duplicate selected", self._duplicate), ("Remove selected", self._remove)):
            menu.addAction(title).triggered.connect(callback)
        menu.exec(self.list_widget.viewport().mapToGlobal(point))
        menu.deleteLater()

    def _set_selected_visibility(self, visible: bool) -> None:
        for dataset in self.selected_datasets():
            dataset.visible = visible
        self.state.modified = True
        self.refresh()
        self.datasets_changed.emit()

    def _tag_selected(self) -> None:
        text, ok = QInputDialog.getText(self, "Tag selected datasets", "Comma-separated tags (empty clears tags)")
        if ok:
            for dataset in self.selected_datasets():
                dataset.metadata["tags"] = [tag.strip() for tag in text.split(",") if tag.strip()]
            self.state.modified = True
            self.refresh_diagnostics()
            self.datasets_changed.emit()

    def _style_selected(self) -> None:
        from materials_data_studio.ui.style_inputs import ColorEdit, StyleCombo
        selected = self.selected_datasets()
        if not selected:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Style {len(selected)} selected datasets")
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        color = ColorEdit(selected[0].plot_style.color)
        line, marker = StyleCombo("line"), StyleCombo("marker")
        line.addItems(["-", "--", ":", "-.", "none"])
        marker.addItems(["none", "o", "s", "^", "v", "d", "x", "+"])
        line.setCurrentText(selected[0].plot_style.line_style)
        marker.setCurrentText(selected[0].plot_style.marker)
        for label, control in (("Color", color), ("Line", line), ("Marker", marker)):
            form.addRow(label, control)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted and QColor(color.text()).isValid():
            for dataset in selected:
                dataset.plot_style.color, dataset.plot_style.line_style, dataset.plot_style.marker = color.text(), line.currentText(), marker.currentText()
            self.state.modified = True
            self.refresh_style_indicators()
            self.datasets_changed.emit()
        dialog.deleteLater()
