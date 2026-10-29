"""Route local-file drops from child viewports to the viewer's import dialog."""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import QWidget
from shiboken6 import isValid


class FileDropFilter(QObject):
    def __init__(self, window: Any) -> None:
        super().__init__(window)
        self.window = window
        self.pending: deque[list[Path]] = deque()
        self.busy = False
        self.closed = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._import_next)

    def enqueue(self, paths: list[Path]) -> None:
        """Return from native OLE drop before opening a modal import/progress loop."""
        if self.closed or not paths:
            return
        self.pending.append(list(paths))
        if not self.busy and not self.timer.isActive():
            self.timer.start(0)

    def cancel(self) -> None:
        self.closed = True
        self.pending.clear()
        self.timer.stop()

    def _import_next(self) -> None:
        if self.closed or self.busy or not self.pending or not isValid(self.window):
            return
        self.busy = True
        paths = self.pending.popleft()
        try:
            self.window._import_dropped_paths(paths)
        finally:
            if isValid(self):
                self.busy = False
                if not self.closed and self.pending and isValid(self.window):
                    self.timer.start(0)

    def eventFilter(self, watched: Any, event: Any) -> bool:
        if (
            event.type() not in {QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop}
            or not event.mimeData().hasUrls()
        ):
            return False
        if event.type() == QEvent.Type.Drop:
            self.window.dragEnterEvent(event)
            if event.isAccepted():
                self.window.dropEvent(event)
        else:
            self.window.dragEnterEvent(event)
        return True


def install_file_drops(window: Any) -> FileDropFilter:
    window.file_drop_filter = FileDropFilter(window)
    for child in window.centralWidget().findChildren(QWidget):
        child.setAcceptDrops(True)
        child.installEventFilter(window.file_drop_filter)
    return window.file_drop_filter
