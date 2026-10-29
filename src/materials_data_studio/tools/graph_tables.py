"""Virtual multi-wave tables with copy-on-write editing and local history."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np
from PySide6.QtCore import QAbstractTableModel, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
)

from materials_data_studio.tools.graph_document import EditableGraphDocument, GraphState
from materials_data_studio.tools.graph_wave_dialogs import WaveTableModel
from materials_data_studio.tools.graph_waves import put_waves
from materials_data_studio.tools.pxp_model import PxpTable, PxpWave


class TableModel(QAbstractTableModel):
    def __init__(
        self, document: EditableGraphDocument, table: PxpTable, *, editable: bool = False
    ) -> None:
        super().__init__()
        self.editable = editable
        self.table = table
        self.models: dict[str, WaveTableModel] = {}
        self.columns: list[tuple[WaveTableModel, int]] = []
        self.readonly_columns: set[int] = set()
        for reference in table.columns:
            mode = reference[-2:] if reference.endswith((".i", ".l", ".d")) else ""
            wave = document.render_source().wave(reference[:-2] if mode else reference)
            if wave is None or wave.data.ndim not in {1, 2}:
                continue
            if mode in {".i", ".l"}:
                if mode == ".i":
                    data = wave.coordinate(0)
                else:
                    labels = wave.dimension_labels[0] if wave.dimension_labels else ()
                    data = np.asarray([labels[i] if i < len(labels) else "" for i in range(wave.shape[0])])
                self.readonly_columns.add(len(self.columns))
                wave = replace(wave, igor_path=reference, name=wave.name+mode, data=data, shape=data.shape, dtype=str(data.dtype), data_units=wave.dimension_units[0] if mode == ".i" else "")
            model = self.models.setdefault(wave.igor_path, WaveTableModel(wave))
            self.columns.extend((model, index) for index in range(model.columnCount()))

    def rowCount(self, parent: Any = None) -> int:
        return (
            0
            if parent is not None and parent.isValid()
            else max((m.rowCount() for m, _ in self.columns), default=0)
        )

    def columnCount(self, parent: Any = None) -> int:
        return 0 if parent is not None and parent.isValid() else len(self.columns)

    def data(self, index: Any, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        model, column = self.columns[index.column()]
        if index.row() >= model.rowCount():
            return "" if role in {Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole} else None
        from materials_data_studio.tools.igor_graph_extensions import format_cell, table_styles
        styles = table_styles(self.table, model.wave.igor_path)
        if role == Qt.ItemDataRole.DisplayRole and styles:
            value = model.wave.data[index.row()] if model.wave.data.ndim == 1 else model.wave.data[index.row(), column]
            return format_cell(value, styles)
        if role == Qt.ItemDataRole.TextAlignmentRole and "alignment" in styles:
            return [Qt.AlignmentFlag.AlignLeft, Qt.AlignmentFlag.AlignHCenter, Qt.AlignmentFlag.AlignRight][int(styles["alignment"])] | Qt.AlignmentFlag.AlignVCenter
        if role == Qt.ItemDataRole.ForegroundRole and "rgb" in styles:
            from PySide6.QtGui import QColor
            return QColor(*(int(c/257) for c in styles["rgb"][:3]))
        if role == Qt.ItemDataRole.FontRole and any(key in styles for key in {"font", "size", "style"}):
            from PySide6.QtGui import QFont
            font = QFont(styles.get("font", "Arial"), styles.get("size", 12))
            bits = int(styles.get("style", 0))
            font.setBold(bool(bits & 1))
            font.setItalic(bool(bits & 2))
            font.setUnderline(bool(bits & 4))
            font.setStrikeOut(bool(bits & 16))
            return font
        return model.data(model.index(index.row(), column), role)

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> Any:
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Vertical:
            if len(self.columns) == 1:
                wave = self.columns[0][0].wave
                if wave.dimension_labels and section < len(wave.dimension_labels[0]) and wave.dimension_labels[0][section]:
                    return wave.dimension_labels[0][section]
            return str(section)
        model, column = self.columns[section]
        from materials_data_studio.tools.igor_wave_ops import dimension_label
        label = dimension_label(model.wave, 1, column) if model.wave.data.ndim > 1 else ""
        from materials_data_studio.tools.igor_graph_extensions import table_styles
        unit = f" ({model.wave.data_units})" if table_styles(self.table, model.wave.igor_path).get("showunits") and model.wave.data_units else ""
        return table_styles(self.table, model.wave.igor_path).get("title", model.wave.name) + (f"[{label or column}]" if model.columnCount() > 1 else "") + unit

    def flags(self, index: Any) -> Qt.ItemFlag:
        result = super().flags(index)
        if (
            self.editable
            and index.isValid()
            and index.column() not in self.readonly_columns
            and index.row() < self.columns[index.column()][0].rowCount()
        ):
            result |= Qt.ItemFlag.ItemIsEditable
        return result

    def setData(self, index: Any, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if not self.editable or not index.isValid() or index.column() in self.readonly_columns:
            return False
        model, column = self.columns[index.column()]
        if index.row() >= model.rowCount() or not model.setData(
            model.index(index.row(), column), value, role
        ):
            return False
        self.dataChanged.emit(index, index, [role, Qt.ItemDataRole.DisplayRole])
        return True

    def changes(self) -> list[PxpWave]:
        return [model.result() for model in self.models.values() if model.edits]


def table_state(
    document: EditableGraphDocument, table: PxpTable | None, *, replace_name: str = ""
) -> GraphState:
    state = document.current.clone()
    tables = list(document.render_source().table_definitions)
    if table is not None and (
        not table.name.strip()
        or any(t.name == table.name and t.name != replace_name for t in tables)
    ):
        raise ValueError("Provide a unique, non-empty table name.")
    index = next((i for i, t in enumerate(tables) if t.name == replace_name), None)
    if index is not None:
        tables.pop(index)
    if table is not None:
        if any(document.render_source().wave(ref[:-2] if ref.endswith((".i", ".l", ".d")) else ref) is None for ref in table.columns):
            raise ValueError("A table column references a missing wave.")
        tables.insert(index if index is not None else len(tables), table)
    state.tables = tuple(tables)
    return state


class TableDefinitionDialog(QDialog):
    def __init__(
        self, parent: Any, document: EditableGraphDocument, table: PxpTable | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Table" if table is None else "Modify Table Columns")
        self.resize(620, 550)
        self.result_table: PxpTable | None = None
        self.original = table
        layout = QVBoxLayout(self)
        existing = {t.name for t in document.render_source().table_definitions}
        number = 0
        while f"Table{number}" in existing:
            number += 1
        self.name = QLineEdit(table.name if table else f"Table{number}")
        layout.addWidget(QLabel("Table name"))
        layout.addWidget(self.name)
        layout.addWidget(QLabel("Check waves to include; drag rows to set column order."))
        self.waves = QListWidget()
        self.waves.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        source = document.render_source()
        chosen = list({w.id: w for ref in table.columns if (w := source.wave(ref[:-2] if ref.endswith((".i", ".l", ".d")) else ref))}.values()) if table else []
        ordered = [*chosen, *(w for w in source.waves if all(w.id != c.id for c in chosen))]
        for wave in ordered:
            if wave.data.ndim not in {1, 2}:
                continue
            item = QListWidgetItem(wave.igor_path, self.waves)
            item.setData(Qt.ItemDataRole.UserRole, wave.igor_path)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if any(wave.id == c.id for c in chosen)
                else Qt.CheckState.Unchecked
            )
        layout.addWidget(self.waves)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def apply(self) -> None:
        columns = tuple(
            self.waves.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.waves.count())
            if self.waves.item(i).checkState() == Qt.CheckState.Checked
        )
        if self.original:
            def base(ref):
                return ref[:-2] if ref.endswith((".i", ".l", ".d")) else ref
            columns = tuple(ref for selected in columns for ref in ([r for r in self.original.columns if base(r) == selected] or [selected]))
        self.result_table = PxpTable(
            self.name.text().strip(), columns, self.original.widths if self.original else (), self.original.column_styles if self.original else ()
        )
        self.accept()


class TableEditorDialog(QDialog):
    def __init__(self, parent: Any, document: EditableGraphDocument, table: PxpTable) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Table — " + table.name)
        self.resize(1000, 620)
        self.result_waves: list[PxpWave] = []
        self.model = TableModel(document, table, editable=True)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Edit working-wave cells. Unequal lengths stay blank. Original PXP is unchanged."
            )
        )
        self.view = QTableView()
        self.view.setModel(self.model)
        layout.addWidget(self.view)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Apply to Working Waves")
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def apply(self) -> None:
        self.view.setFocus()  # Commit any active delegate before collecting edits.
        self.result_waves = self.model.changes()
        self.accept()


def table_action(window: Any, operation: str, name: str = "") -> None:
    from materials_data_studio.tools.graph_actions import ensure_document

    ensure_document(window)
    document = window.editor.document
    tables = document.render_source().table_definitions
    if not name and window._selection[0] == "table":
        name = window._selection[1]
    table = next((t for t in tables if t.name == name), None)
    if operation != "new" and table is None:
        QMessageBox.information(window, "Table", "Select a table in the experiment tree first.")
        return
    try:
        if operation == "delete":
            if (
                QMessageBox.question(
                    window,
                    "Delete Table",
                    f"Delete table {name}? Underlying waves are retained; this can be undone.",
                )
                != QMessageBox.StandardButton.Yes
            ):
                return
            state = table_state(document, None, replace_name=name)
        elif operation == "edit":
            assert table is not None
            dialog = TableEditorDialog(window, document, table)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            if not dialog.result_waves:
                return
            state = put_waves(document, dialog.result_waves, overwrite=True)
        else:
            dialog = TableDefinitionDialog(window, document, table if operation != "new" else None)
            if dialog.exec() != QDialog.DialogCode.Accepted or dialog.result_table is None:
                return
            state = table_state(
                document, dialog.result_table, replace_name=name if operation != "new" else ""
            )
            window._selection = "table", dialog.result_table.name
            window.view_state["selected"] = window._selection
        window.editor.commit(document.current, state, operation.title() + " table")
    except (ValueError, TypeError) as exc:
        QMessageBox.warning(window, "Table", str(exc))


def install_tables(window: Any) -> None:
    page = QVBoxLayout()
    widget = window.centralWidget().__class__()
    widget.setLayout(page)
    controls = QHBoxLayout()
    for title, op in (
        ("Edit cells…", "edit"),
        ("Modify columns…", "modify"),
        ("Delete table…", "delete"),
    ):
        button = QPushButton(title)
        button.clicked.connect(lambda _checked=False, operation=op: table_action(window, operation))
        controls.addWidget(button)
    page.addLayout(controls)
    window.table_view = QTableView()
    window.table_view.doubleClicked.connect(lambda _index: table_action(window, "edit"))
    page.addWidget(window.table_view)
    window.table_page = widget
    window.preview_stack.addWidget(widget)
    menu = window.menuBar().addMenu("Table")
    for title, op in (
        ("New Table…", "new"),
        ("Edit Table…", "edit"),
        ("Modify Columns…", "modify"),
        ("Delete Table…", "delete"),
    ):
        menu.addAction(title).triggered.connect(
            lambda _checked=False, operation=op: table_action(window, operation)
        )


def show_table(window: Any, name: str) -> None:
    table = next(
        (t for t in window.editor.document.render_source().table_definitions if t.name == name),
        None,
    )
    if table is None:
        return
    window.table_model = TableModel(window.editor.document, table)
    window.table_view.setModel(window.table_model)
    for index, (model, _column) in enumerate(window.table_model.columns):
        width = next(
            (v for ref, v in table.widths if ref in {model.wave.name, model.wave.igor_path}), 140
        )
        window.table_view.setColumnWidth(index, round(width))
    window.preview_stack.setCurrentWidget(window.table_page)
    window.options.setEnabled(False)
    window.compatibility.setText(
        f"{name} · {window.table_model.columnCount()} columns · {window.table_model.rowCount()} rows · double-click to edit working waves"
    )
