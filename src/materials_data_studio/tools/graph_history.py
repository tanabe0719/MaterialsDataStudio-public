"""GUI-local history; deliberately separate from the Qt-free document codec."""
from __future__ import annotations

import math
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtGui import QUndoCommand, QUndoStack

from materials_data_studio.tools.graph_document import (
    EditableGraphDocument,
    GraphState,
    save_graph_document,
)
from materials_data_studio.tools.pxp_model import PxpGraph

_VERIFIER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="graph-verification")


class _EditCommand(QUndoCommand):
    def __init__(self, editor: GraphEditorController, before: GraphState, after: GraphState, title: str) -> None:
        super().__init__(title)
        self.editor, self.before, self.after = editor, before, after

    def undo(self) -> None:
        self.editor.document.current = self.before.clone()
        self.editor.notify(["Undo"])

    def redo(self) -> None:
        self.editor.document.current = self.after.clone()
        if not self.editor.pushing:
            self.editor.notify(["Redo"])


class GraphEditorController:
    def __init__(self, document: EditableGraphDocument, *, saved: bool = False) -> None:
        self.document, self.history, self.saved = document, QUndoStack(), saved
        self.history.setUndoLimit(100)
        self.path: Path | None = None
        self.observers: list[Any] = []
        self.pushing = False
        self.memory_limit_bytes = 256 * 1024**2
        self.retained_memory_bytes = 0
        self._replay_cache = OrderedDict()
        self._pending_verifications = []
        self._verification_timer = None

    def finish_verifications(self, *, wait=True):
        from materials_data_studio.core.immutable_json import freeze
        remaining = []
        for identifier, future, before, after in self._pending_verifications:
            if not wait and not future.done():
                remaining.append((identifier, future, before, after))
                continue
            try:
                replay = future.result()
            except Exception:
                replay = None
            states = [self.document.current]
            for index in range(self.history.count()):
                command = self.history.command(index)
                if isinstance(command, _EditCommand):
                    states.extend((command.before, command.after))
            for state in states:
                history = state.environment.get("operation_history", [])
                if any(record.get("verification_id") == identifier for record in history):
                    state.environment["operation_history"] = freeze([
                        {**record, "replayable": replay is not None, **({"script": replay} if replay else {})}
                        if record.get("verification_id") == identifier else record for record in history])
            self.document.current = self.document.current.clone()
        self._pending_verifications = remaining
        if not remaining and self._verification_timer is not None:
            self._verification_timer.stop()

    def notify(self, events: list[str]) -> None:
        for callback in self.observers:
            try:
                callback(events)
            except Exception:
                import logging
                logging.getLogger(__name__).warning("Graph command display observer failed", exc_info=True)

    @property
    def dirty(self) -> bool:
        from shiboken6 import isValid
        return not self.saved or (isValid(self.history) and not self.history.isClean())

    def commit(self, before: GraphState, after: GraphState, title: str) -> None:
        if before != after:
            verification_id = None
            from materials_data_studio.tools.command_services import semantic_events
            try:
                events = semantic_events(self.document, before, after, title)
            except Exception as exc:
                events = [f"// GUI {title}: command display unavailable ({type(exc).__name__}); document edit retained"]
            # Persist descriptive operations separately from replayable command
            # inputs. Never suggest that abbreviated GUI logs reconstruct waves.
            from materials_data_studio.tools.command_recording import verified_replay
            from materials_data_studio.version import __version__
            # Reuse only a result already verified against these exact semantic
            # revisions. Cache entries retain their states, preventing recycled
            # array identities from validating a different input document.
            key = tuple(events)
            cached = self._replay_cache.get(key)
            if cached is not None and cached[0] == before and cached[1] == after:
                replay = cached[2]
                self._replay_cache.move_to_end(key)
            else:
                large = len(before.environment.get("operation_history", [])) > 100 or sum(math.prod(w.shape) * np.dtype(w.dtype).itemsize for w in (before.waves if before.waves is not None else self.document.source.waves)) > 8 * 1024**2
                if large and QCoreApplication.instance() is not None:
                    if len(self._pending_verifications) >= 4:
                        self.finish_verifications()
                        # The edit was prepared before the flush. Preserve the
                        # newly verified log prefix in both semantic revisions.
                        history = self.document.current.environment.get("operation_history", [])
                        before, after = before.clone(), after.clone()
                        before.environment["operation_history"] = history
                        after.environment["operation_history"] = history
                    verification_id = uuid4().hex
                    from dataclasses import replace
                    source = replace(self.document, current=before.clone())
                    future = _VERIFIER.submit(verified_replay, source, before.clone(), after.clone(), list(events))
                    self._pending_verifications.append((verification_id, future, before, after))
                    if self._verification_timer is None:
                        self._verification_timer = QTimer()
                        self._verification_timer.setInterval(50)
                        self._verification_timer.timeout.connect(lambda: self.finish_verifications(wait=False))
                    self._verification_timer.start()
                    replay = None
                else:
                    replay = verified_replay(self.document, before, after, events)
                if verification_id is None:
                    self._replay_cache[key] = (before.clone(), after.clone(), replay)
                while len(self._replay_cache) > 8:
                    self._replay_cache.popitem(last=False)
            after = after.clone()
            from materials_data_studio.core.immutable_json import HistoryLog
            record = {"kind": "command" if title.startswith("Command: ") else "gui-description", "title": title, "application_version": __version__, "description": events, "replayable": replay is not None, **({"script": replay} if replay else {})}
            if verification_id is not None:
                record["verification_id"] = verification_id
            after.environment["operation_history"] = HistoryLog(before.environment.get("operation_history", []), record)
            self.pushing = True
            try:
                self.history.push(_EditCommand(self, before.clone(), after.clone(), title))
            finally:
                self.pushing = False
            self.notify(events)
            self._bound_memory()

    def _bound_memory(self) -> None:
        from materials_data_studio.core.memory import retained_bytes

        states = []
        for index in range(self.history.count()):
            command = self.history.command(index)
            if isinstance(command, _EditCommand):
                states.extend((command.before, command.after))
        pending_states = [(before, after) for _identifier, _future, before, after in self._pending_verifications]
        self.retained_memory_bytes = retained_bytes(self.document, [states, pending_states, list(self._replay_cache.values())])
        if self.retained_memory_bytes > self.memory_limit_bytes:
            self.finish_verifications()
            self.history.clear()
            self._replay_cache.clear()
            self.history.resetClean()
            self.saved = False
            self.retained_memory_bytes = 0
            self.notify(["Older Graph Editor Undo history was cleared to respect the memory limit; current data is unchanged."])

    def edit_graph(self, graph: PxpGraph, title: str, layout: dict[str, Any] | None = None) -> None:
        from materials_data_studio.tools.command_services import replace_graph_state
        self.commit(self.document.current.clone(), replace_graph_state(self.document, graph, layout), title)

    def save(self, path: Path) -> None:
        self.finish_verifications()
        save_graph_document(path, self.document)
        self.path, self.saved = path, True
        self.history.setClean()
