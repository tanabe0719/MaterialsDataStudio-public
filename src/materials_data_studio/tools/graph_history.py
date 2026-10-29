"""GUI-local history; deliberately separate from the Qt-free document codec."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtGui import QUndoCommand, QUndoStack

from materials_data_studio.tools.graph_document import (
    EditableGraphDocument,
    GraphState,
    save_graph_document,
)
from materials_data_studio.tools.pxp_model import PxpGraph


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
            from materials_data_studio.tools.command_services import semantic_events
            try:
                events = semantic_events(self.document, before, after, title)
            except Exception as exc:
                events = [f"// GUI {title}: command display unavailable ({type(exc).__name__}); document edit retained"]
            # Persist descriptive operations separately from replayable command
            # inputs. Never suggest that abbreviated GUI logs reconstruct waves.
            from materials_data_studio.tools.command_recording import verified_replay
            from materials_data_studio.version import __version__
            replay = verified_replay(self.document, before, after, events)
            after = after.clone()
            history = list(before.environment.get("operation_history", []))
            history.append({"kind": "command" if title.startswith("Command: ") else "gui-description", "title": title, "application_version": __version__, "description": events, "replayable": replay is not None, **({"script": replay} if replay else {})})
            after.environment["operation_history"] = history
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
        self.retained_memory_bytes = retained_bytes(self.document, states)
        if self.retained_memory_bytes > self.memory_limit_bytes:
            self.history.clear()
            self.history.resetClean()
            self.saved = False
            self.retained_memory_bytes = 0
            self.notify(["Older Graph Editor Undo history was cleared to respect the memory limit; current data is unchanged."])

    def edit_graph(self, graph: PxpGraph, title: str, layout: dict[str, Any] | None = None) -> None:
        from materials_data_studio.tools.command_services import replace_graph_state
        self.commit(self.document.current.clone(), replace_graph_state(self.document, graph, layout), title)

    def save(self, path: Path) -> None:
        save_graph_document(path, self.document)
        self.path, self.saved = path, True
        self.history.setClean()
