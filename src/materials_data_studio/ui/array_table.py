"""Virtual array editing: no per-cell widgets or eager string conversions."""

from __future__ import annotations

from typing import Any

import numpy as np
from PySide6.QtCore import QAbstractTableModel, Qt


class ArrayTableModel(QAbstractTableModel):
    def __init__(self, values: np.ndarray, parent: Any = None) -> None:
        super().__init__(parent)
        self.values = values
        self.edits: dict[tuple[int, int], Any] = {}

    def rowCount(self, parent: Any = None) -> int:
        return 0 if parent is not None and parent.isValid() else len(self.values)

    def columnCount(self, parent: Any = None) -> int:
        return (
            0
            if parent is not None and parent.isValid()
            else (self.values.shape[1] if self.values.ndim == 2 else 1)
        )

    def data(self, index: Any, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or role not in {
            Qt.ItemDataRole.DisplayRole,
            Qt.ItemDataRole.EditRole,
        }:
            return None
        position = (index.row(), index.column())
        value = self.edits.get(
            position, self.values[position] if self.values.ndim == 2 else self.values[index.row()]
        )
        return str(value)

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> Any:
        if role == Qt.ItemDataRole.DisplayRole:
            return (
                str(section)
                if orientation == Qt.Orientation.Vertical
                else "Value"
                if self.values.ndim == 1
                else f"Column {section}"
            )
        return None

    def flags(self, index: Any) -> Qt.ItemFlag:
        return super().flags(index) | Qt.ItemFlag.ItemIsEditable

    def setData(self, index: Any, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if not index.isValid() or role != Qt.ItemDataRole.EditRole:
            return False
        try:
            kind = self.values.dtype.kind
            converted: Any = (
                str(value) if kind in "SU" else complex(value) if kind == "c" else float(value)
            )
            if kind in "biu":
                if not np.isfinite(converted) or int(converted) != converted:
                    return False
                if (
                    kind != "b"
                    and not np.iinfo(self.values.dtype).min
                    <= converted
                    <= np.iinfo(self.values.dtype).max
                ):
                    return False
                if kind == "b" and converted not in {0, 1}:
                    return False
                converted = int(converted)
        except (ValueError, TypeError):
            return False
        self.edits[index.row(), index.column()] = converted
        self.dataChanged.emit(index, index, [role, Qt.ItemDataRole.DisplayRole])
        return True

    def result(self) -> np.ndarray:
        dtype = self.values.dtype
        if dtype.kind in "SU":
            width = max(
                [
                    dtype.itemsize // (4 if dtype.kind == "U" else 1),
                    *(len(str(v)) for v in self.edits.values()),
                ]
            )
            dtype = np.dtype(f"U{width}")
        if self.values.size * dtype.itemsize > 512 * 1024**2:
            raise ValueError("Edited column exceeds the 512 MiB memory safety limit")
        result = np.array(self.values, dtype=dtype, copy=True)
        for (row, column), value in self.edits.items():
            if result.ndim == 1:
                result[row] = value
            else:
                result[row, column] = value
        return result
