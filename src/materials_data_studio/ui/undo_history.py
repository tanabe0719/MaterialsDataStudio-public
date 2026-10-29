"""Bounded workspace snapshots backing the application Undo/Redo actions."""

from __future__ import annotations

import copy
import hashlib
import json
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QUndoCommand, QUndoStack

from materials_data_studio.core.models import Dataset, ModuleType
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.tools.pxp_model import PxpDocument


def clone_dataset(dataset: Dataset) -> Dataset:
    """Share immutable array revisions; independently own mutable metadata."""
    result = copy.copy(dataset)
    result.raw_data = dict(dataset.raw_data)
    result.processed_data = dict(dataset.processed_data)
    for values in result.processed_data.values():
        values.setflags(write=False)
    result.metadata = copy.deepcopy(dataset.metadata)
    result.analysis_results = copy.deepcopy(dataset.analysis_results)
    result.plot_style = copy.copy(dataset.plot_style)
    result.warnings = list(dataset.warnings)
    return result


def _json_default(value: object) -> object:
    if isinstance(value, np.ndarray):
        contiguous = np.ascontiguousarray(value)
        return {
            "dtype": contiguous.dtype.str,
            "shape": contiguous.shape,
            "sha256": hashlib.sha256(contiguous.tobytes()).hexdigest(),
        }
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, bytes):
        return hashlib.sha256(value).hexdigest()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"Cannot fingerprint {type(value).__name__}.")


def _without_layout_state(value: Any) -> Any:
    """Exclude geometry-only values that change when Qt first lays out a window."""
    if isinstance(value, dict):
        return {
            key: _without_layout_state(item)
            for key, item in value.items()
            if key != "splitter_sizes"
        }
    if isinstance(value, list):
        return [_without_layout_state(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    """One immutable-by-convention workspace state."""

    datasets: tuple[Dataset, ...]
    active_module: ModuleType
    module_states: dict[str, dict[str, Any]]
    axis_settings: dict[str, Any]
    pxp_documents: dict[str, PxpDocument]
    pxp_view_states: dict[str, dict[str, Any]]
    digest: str

    @classmethod
    def capture(
        cls,
        state: ProjectState,
        module_states: dict[str, dict[str, Any]],
        *,
        digest: str | None = None,
    ) -> WorkspaceSnapshot:
        snapshot_digest = digest or _workspace_digest(
            state.datasets,
            state.active_module,
            module_states,
            state.axis_settings,
            state.pxp_documents,
            state.pxp_view_states,
            lambda values: hashlib.sha256(np.ascontiguousarray(values).tobytes()).digest(),
        )
        datasets = tuple(clone_dataset(dataset) for dataset in state.datasets)
        copied_modules = copy.deepcopy(module_states)
        copied_axes = copy.deepcopy(state.axis_settings)
        return cls(
            datasets=datasets,
            active_module=state.active_module,
            module_states=copied_modules,
            axis_settings=copied_axes,
            pxp_documents=dict(state.pxp_documents),
            pxp_view_states=copy.deepcopy(state.pxp_view_states),
            digest=snapshot_digest,
        )


def _workspace_digest(
    datasets: list[Dataset] | tuple[Dataset, ...],
    active_module: ModuleType,
    module_states: dict[str, dict[str, Any]],
    axis_settings: dict[str, Any],
    pxp_documents: dict[str, PxpDocument],
    pxp_view_states: dict[str, dict[str, Any]],
    array_digest: Callable[[np.ndarray[Any, Any]], bytes],
    source_digest: Callable[[bytes], bytes] = lambda value: hashlib.sha256(value).digest(),
) -> str:
    digest = hashlib.sha256()
    digest.update(active_module.value.encode())
    digest.update(
        json.dumps(
            {
                "module_states": module_states,
                "axis_settings": axis_settings,
                "pxp_documents": {key: value.sha256 for key, value in pxp_documents.items()},
                "pxp_view_states": {
                    source_id: {key: value for key, value in view.items() if key != "selected"}
                    for source_id, view in pxp_view_states.items()
                },
            },
            sort_keys=True,
            ensure_ascii=False,
            default=_json_default,
        ).encode()
    )
    for dataset in datasets:
        digest.update(
            json.dumps(
                dataset.to_manifest(),
                sort_keys=True,
                ensure_ascii=False,
                default=_json_default,
            ).encode()
        )
        for kind, arrays in (
            ("raw", dataset.raw_data),
            ("processed", dataset.processed_data),
        ):
            for name, values in sorted(arrays.items()):
                contiguous = np.ascontiguousarray(values)
                digest.update(kind.encode())
                digest.update(name.encode())
                digest.update(contiguous.dtype.str.encode())
                digest.update(repr(contiguous.shape).encode())
                digest.update(array_digest(contiguous))
        if dataset.source_bytes is not None:
            digest.update(source_digest(dataset.source_bytes))
    return digest.hexdigest()


class WorkspaceFingerprinter:
    """Hash immutable source arrays once while still checking derived arrays."""

    def __init__(self) -> None:
        self._immutable_arrays: dict[
            int, tuple[weakref.ReferenceType[np.ndarray[Any, Any]], bytes]
        ] = {}
        self._source_hashes: dict[tuple[int, int, int], bytes] = {}

    def digest(
        self,
        state: ProjectState,
        module_states: dict[str, dict[str, Any]],
    ) -> str:
        # Immutable original bytes are hashed once, not every idle checkpoint.
        result = _workspace_digest(
            state.datasets,
            state.active_module,
            _without_layout_state(module_states),
            state.axis_settings,
            state.pxp_documents,
            state.pxp_view_states,
            self._array_digest,
            self._source_digest,
        )
        return result

    def _source_digest(self, value: bytes) -> bytes:
        key = (id(value), len(value), hash(value))
        if key not in self._source_hashes:
            if len(self._source_hashes) >= 1024:
                self._source_hashes.clear()
            self._source_hashes[key] = hashlib.sha256(value).digest()
        return self._source_hashes[key]

    def _array_digest(self, values: np.ndarray[Any, Any]) -> bytes:
        if values.flags.writeable:
            return hashlib.sha256(values.tobytes()).digest()
        key = id(values)
        cached = self._immutable_arrays.get(key)
        if cached is not None and cached[0]() is values:
            return cached[1]
        result = hashlib.sha256(values.tobytes()).digest()
        self._immutable_arrays[key] = (
            weakref.ref(values, lambda _ref: self._immutable_arrays.pop(key, None)),
            result,
        )
        return result


def restore_snapshot(state: ProjectState, snapshot: WorkspaceSnapshot) -> None:
    """Replace serializable project content from a snapshot."""
    state.datasets = [clone_dataset(dataset) for dataset in snapshot.datasets]
    state.active_module = snapshot.active_module
    state.module_states = copy.deepcopy(snapshot.module_states)
    state.axis_settings = copy.deepcopy(snapshot.axis_settings)
    state.pxp_documents = dict(snapshot.pxp_documents)
    state.pxp_view_states = copy.deepcopy(snapshot.pxp_view_states)


class _WorkspaceCommand(QUndoCommand):
    def __init__(
        self,
        controller: WorkspaceUndoController,
        before: WorkspaceSnapshot,
        after: WorkspaceSnapshot,
        text: str,
    ) -> None:
        super().__init__(text)
        self._controller = controller
        self._before = before
        self._after = after
        self._first_redo = True

    def undo(self) -> None:
        self._controller.restore(self._before, "Undo")

    def redo(self) -> None:
        if self._first_redo:
            self._first_redo = False
            return
        self._controller.restore(self._after, "Redo")


class WorkspaceUndoController(QObject):
    """Detect settled workspace changes and record bounded undo commands."""
    historyTrimmed = Signal(str)

    def __init__(
        self,
        stack: QUndoStack,
        capture: Callable[[], WorkspaceSnapshot],
        fingerprint: Callable[[], str],
        apply: Callable[[WorkspaceSnapshot, str], None],
        set_modified: Callable[[bool], None],
        parent: QObject | None = None,
        *,
        change_token: Callable[[], int] | None = None,
        memory_limit_bytes: int = 256 * 1024**2,
    ) -> None:
        super().__init__(parent)
        self.stack = stack
        self._capture = capture
        self._fingerprint = fingerprint
        self._apply = apply
        self._set_modified = set_modified
        self._restoring = False
        self._change_token = change_token
        self._last_token = change_token() if change_token else None
        self.memory_limit_bytes = memory_limit_bytes
        self.stack.setUndoLimit(20)
        self._last = self._capture()
        self._timer = QTimer(self)
        self._timer.setInterval(350)
        self._timer.timeout.connect(lambda: self.checkpoint(force=False))
        self._timer.start()
        self.stack.indexChanged.connect(self._index_changed)

    @property
    def restoring(self) -> bool:
        return self._restoring

    def checkpoint(self, *, force: bool = True) -> None:
        """Record one command after a group of UI signals has settled."""
        if self._restoring:
            return
        token = self._change_token() if self._change_token else None
        if not force and self._change_token is not None and token == self._last_token:
            return
        self._last_token = token
        if self._fingerprint() == self._last.digest:
            self._set_modified(not self.stack.isClean())
            return
        current = self._capture()
        text = self._describe_change(self._last, current)
        command = _WorkspaceCommand(self, self._last, current, text)
        self._last = current
        self.stack.push(command)
        self._bound_memory()

    def _bound_memory(self) -> None:
        from materials_data_studio.core.memory import retained_bytes

        snapshots = []
        for index in range(self.stack.count()):
            command = self.stack.command(index)
            if isinstance(command, _WorkspaceCommand):
                snapshots.extend((command._before, command._after))
        self.retained_memory_bytes = retained_bytes(self._last, snapshots)
        if self.retained_memory_bytes > self.memory_limit_bytes:
            self.stack.clear()
            self.stack.resetClean()
            self._set_modified(True)
            self.retained_memory_bytes = 0
            self.historyTrimmed.emit("Older Undo history was cleared to respect the memory limit; current data is unchanged.")

    def restore(self, snapshot: WorkspaceSnapshot, direction: str) -> None:
        self._restoring = True
        try:
            self._apply(snapshot, direction)
            self._last = snapshot
        finally:
            self._restoring = False

    def reset(self, *, clean: bool = True) -> None:
        """Discard history after New/Open Session and establish a new baseline."""
        self._restoring = True
        try:
            self.stack.clear()
            self._last = self._capture()
            self._last_token = self._change_token() if self._change_token else None
            if clean:
                self.stack.setClean()
                self._set_modified(False)
        finally:
            self._restoring = False

    def mark_clean(self) -> None:
        """Mark the current history index as the last successfully saved state."""
        self.checkpoint()
        self.stack.setClean()
        self._set_modified(False)

    def _index_changed(self, _index: int) -> None:
        try:
            modified = not self.stack.isClean()
        except RuntimeError:
            # QMainWindow teardown can destroy the C++ QUndoStack before queued
            # indexChanged delivery reaches this Python-owned controller.
            return
        self._set_modified(modified)

    @staticmethod
    def _describe_change(before: WorkspaceSnapshot, after: WorkspaceSnapshot) -> str:
        before_ids = {dataset.dataset_id for dataset in before.datasets}
        after_ids = {dataset.dataset_id for dataset in after.datasets}
        if len(after_ids - before_ids) == 1 and not before_ids - after_ids:
            return "Import or Duplicate Dataset"
        if len(before_ids - after_ids) == 1 and not after_ids - before_ids:
            return "Remove Dataset"
        if before.active_module != after.active_module:
            return "Switch Module"
        return "Edit Workspace"
