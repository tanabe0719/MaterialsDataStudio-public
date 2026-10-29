"""Bounded, cancellable GUI jobs with immutable request identity."""

from __future__ import annotations

import copy
import logging
import traceback
from collections.abc import Callable
from threading import Event
from typing import Any

from PySide6.QtCore import QEventLoop, QObject, Qt, QThread, QThreadPool, Signal, Slot
from PySide6.QtWidgets import QMainWindow, QProgressDialog, QWidget
from shiboken6 import delete, isValid

from materials_data_studio.core.models import Dataset
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.core.workers import Worker
from materials_data_studio.ui.dialogs import show_error

_POOL: QThreadPool | None = None
_LIVE: set[Task] = set()


class Task(QObject):
    finished = Signal()

    def __init__(
        self,
        owner: QWidget,
        title: str,
        function: Callable[[], Any],
        accept: Callable[[Any], None],
        *,
        state: ProjectState | None = None,
        dataset: Dataset | None = None,
        cancelled: Event | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__()
        self.owner, self.state, self.dataset = owner, state, dataset
        self.title = title
        self.identity = (id(owner), title, id(dataset))
        if dataset is not None:
            for previous in tuple(_LIVE):
                if previous.identity == self.identity:
                    previous.cancelled.set()
        self.generation = state.generation if state is not None else None
        self.metadata = copy.deepcopy(dataset.metadata) if dataset is not None else None
        self.arrays = (
            tuple((key, id(value)) for key, value in dataset.processed_data.items())
            if dataset is not None
            else ()
        )
        self.cancelled = cancelled or Event()
        self.on_error = on_error
        self.accept = accept
        self.dialog = QProgressDialog(title, "Stop", 0, 0, owner)
        self.dialog.setWindowTitle(title)
        self.dialog.setWindowModality(Qt.WindowModality.NonModal)
        self.dialog.setMinimumDuration(200)
        self.dialog.canceled.connect(self.cancelled.set)
        self._owner_destroyed = lambda _object=None, stop=self.cancelled: stop.set()
        owner.destroyed.connect(self._owner_destroyed)

        def execute() -> Any:
            if self.cancelled.is_set():
                raise InterruptedError("Task stopped before execution")
            self.worker.signals.progress.emit(0, "Started")
            return function()

        self.worker = Worker(execute)
        # A Python lambda/list callback is not a QObject receiver. Explicit
        # queued slots guarantee that *all* widget/result handling occurs on
        # this object's GUI thread, even when a job completes immediately.
        self.worker.signals.result.connect(self._accept, Qt.ConnectionType.QueuedConnection)
        self.worker.signals.error.connect(self._error, Qt.ConnectionType.QueuedConnection)
        self.worker.signals.progress.connect(self._started, Qt.ConnectionType.QueuedConnection)
        self.worker.signals.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        _LIVE.add(self)

    @Slot(int, str)
    def _started(self, _value: int, _message: str) -> None:
        if self.valid() and isValid(self.dialog):
            self.dialog.setValue(0)

    def valid(self) -> bool:
        if self.cancelled.is_set() or not isValid(self.owner):
            return False
        if self.state is not None and self.state.generation != self.generation:
            return False
        if self.dataset is not None:
            if self.state is not None and not any(
                item is self.dataset for item in self.state.datasets
            ):
                return False
            if self.dataset.metadata != self.metadata:
                return False
            if (
                tuple((key, id(value)) for key, value in self.dataset.processed_data.items())
                != self.arrays
            ):
                return False
        return True

    @Slot(object)
    def _accept(self, result: Any) -> None:
        if self.valid():
            try:
                self.accept(result)
            except Exception as exc:
                show_error(self.owner, "Result could not be applied", str(exc))
        elif isValid(self.owner) and not self.cancelled.is_set():
            message = "An obsolete calculation was discarded because its input workspace changed."
            window = self.owner.window()
            if isinstance(window, QMainWindow):
                window.statusBar().showMessage(message, 10000)
            else:
                self.owner.setToolTip(message)

    @Slot(str, str)
    def _error(self, message: str, _trace: str) -> None:
        logging.getLogger(__name__).error("Job failed: %s\n%s", self.title, _trace)
        if self.valid():
            if self.on_error is not None:
                self.on_error(message)
            else:
                show_error(self.owner, self.title, message)

    @Slot()
    def _finished(self) -> None:
        if isValid(self.dialog):
            self.dialog.canceled.disconnect()
            self.dialog.reset()
            self.dialog.hide()
            delete(self.dialog)
        if isValid(self.owner):
            self.owner.destroyed.disconnect(self._owner_destroyed)
            self.finished.emit()
        self.worker.function = lambda: None
        self.worker.args = ()
        self.worker.kwargs.clear()
        self.worker.signals.result.disconnect()
        self.worker.signals.error.disconnect()
        self.worker.signals.progress.disconnect()
        _LIVE.discard(self)
        self.deleteLater()


class _BlockingResult(QObject):
    """Receive worker outcomes in order, on the nested event loop's thread."""

    def __init__(self, title: str, loop: QEventLoop, stop: Event) -> None:
        super().__init__()
        self.title, self.loop, self.stop = title, loop, stop
        self.result: list[Any] = []
        self.errors: list[str] = []
        self.done = False

    @Slot(object)
    def accept(self, result: Any) -> None:
        self.result.append(result)

    @Slot(str, str)
    def failed(self, message: str, trace: str) -> None:
        if not self.stop.is_set():
            logging.getLogger(__name__).error("Job failed: %s\n%s", self.title, trace)
            self.errors.append(message)

    @Slot()
    def finish(self) -> None:
        self.done = True
        self.loop.quit()


class _BlockingThread(QThread):
    """An owned thread whose native lifetime ends before modal-job cleanup.

    In contrast to an auto-deleting QRunnable, QThread.finished is emitted only
    after run() returns. wait() then fences native cleanup before the GUI drops
    the last Python references or disposes its receiver/progress widgets.
    """

    result = Signal(object)
    error = Signal(str, str)

    def __init__(self, function: Callable[[], Any]) -> None:
        super().__init__()
        self.function = function

    def run(self) -> None:
        try:
            value = self.function()
        except Exception as exc:
            self.error.emit(str(exc), traceback.format_exc())
        else:
            self.result.emit(value)


def run_task(
    owner: QWidget,
    title: str,
    function: Callable[[], Any],
    accept: Callable[[Any], None],
    *,
    state: ProjectState | None = None,
    dataset: Dataset | None = None,
    cancelled: Event | None = None,
    on_error: Callable[[str], None] | None = None,
) -> Task:
    global _POOL
    if _POOL is None:
        _POOL = QThreadPool()
        _POOL.setMaxThreadCount(2)
    task = Task(
        owner,
        title,
        function,
        accept,
        state=state,
        dataset=dataset,
        cancelled=cancelled,
        on_error=on_error,
    )
    _POOL.start(task.worker)
    return task


def cancel_tasks(state: ProjectState) -> None:
    for task in tuple(_LIVE):
        if task.state is state:
            task.cancelled.set()


def blocking_task(
    owner: QWidget,
    title: str,
    function: Callable[[], Any],
    *,
    cancelled: Event | None = None,
    allow_cancel: bool = True,
) -> Any:
    """Keep synchronous Save/Open semantics while the Qt event loop stays responsive."""
    stop = cancelled or Event()
    loop = QEventLoop()
    outcome = _BlockingResult(title, loop, stop)
    dialog = QProgressDialog(title, "Stop" if allow_cancel else "", 0, 0, owner)
    if not allow_cancel:
        dialog.setCancelButton(None)
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    dialog.setMinimumDuration(200)
    dialog.canceled.connect(stop.set)
    def owner_destroyed(_object: object = None) -> None:
        stop.set()

    owner.destroyed.connect(owner_destroyed)
    worker = _BlockingThread(function)
    worker.result.connect(outcome.accept, Qt.ConnectionType.QueuedConnection)
    worker.error.connect(outcome.failed, Qt.ConnectionType.QueuedConnection)
    worker.finished.connect(outcome.finish, Qt.ConnectionType.QueuedConnection)
    dialog.setValue(0)
    worker.start()
    # QObject slots are queued even for an already-finished/synchronous worker:
    # quit() can therefore never be lost before exec() starts. The state guard
    # also covers nested events processed during worker startup.
    if not outcome.done:
        loop.exec()
    # The last finished signal may arrive while native thread-local cleanup is
    # still ending. Fence it before releasing Python/Qt callback owners.
    worker.wait()
    worker.result.disconnect()
    worker.error.disconnect()
    worker.finished.disconnect()
    worker.function = lambda: None
    worker.deleteLater()
    if isValid(dialog):
        dialog.canceled.disconnect()
        # close() alone leaves QProgressDialog's minimum-duration timer armed.
        # It can resurrect a completed stage in the next modal import dialog,
        # whose nested event loop also postpones DeferredDelete delivery.
        dialog.reset()
        dialog.hide()
        delete(dialog)
    if isValid(owner):
        owner.destroyed.disconnect(owner_destroyed)
    else:
        stop.set()
    if stop.is_set():
        raise InterruptedError(f"{title} stopped")
    if outcome.errors:
        raise RuntimeError(outcome.errors[0])
    if not outcome.result:
        raise InterruptedError(f"{title} stopped")
    return outcome.result[0]
