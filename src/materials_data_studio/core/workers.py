"""Reusable QThreadPool worker abstraction."""

from __future__ import annotations

import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class WorkerSignals(QObject):
    """Signals emitted by a background worker on the GUI thread."""

    result = Signal(object)
    error = Signal(str, str)
    progress = Signal(int, str)
    finished = Signal()


class Worker(QRunnable):
    """Run a callable on a QThreadPool without touching widgets."""

    def __init__(self, function: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.function = function
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        """Execute the callable and translate outcomes to signals."""
        try:
            result = self.function(*self.args, **self.kwargs)
        except Exception as exc:
            self.signals.error.emit(str(exc), traceback.format_exc())
        else:
            self.signals.result.emit(result)
        finally:
            self.signals.finished.emit()

