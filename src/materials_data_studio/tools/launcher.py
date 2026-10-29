"""Top-level Tools menu and non-modal utility-window management."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMainWindow, QMenu

from materials_data_studio.tools.windows import (
    MolarMassWindow,
    RasxConverterWindow,
    ShannonRadiiWindow,
    ToolWindow,
)

if TYPE_CHECKING:
    from materials_data_studio.tools.graph_window import GraphEditorWindow
    from materials_data_studio.tools.pxp_window import PxpViewerWindow


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    """One implemented entry in the top-level Tools menu."""

    tool_id: str
    title: str
    factory: Callable[[QMainWindow | None], ToolWindow]


TOOLS = (
    ToolDescriptor(
        "molar_mass",
        "Molecular Weight Calculator",
        MolarMassWindow,
    ),
    ToolDescriptor(
        "shannon_radii",
        "Shannon Radii",
        ShannonRadiiWindow,
    ),
    ToolDescriptor(
        "rasx_converter",
        "RASX Converter",
        RasxConverterWindow,
    ),
    ToolDescriptor("pxp_viewer", "Graph Editor / Igor Import", lambda parent: _new_pxp_viewer(parent)),
)


def _new_pxp_viewer(parent: QMainWindow | None) -> ToolWindow:
    from materials_data_studio.tools.graph_window import GraphEditorWindow

    return GraphEditorWindow(parent)


class ToolMenuManager(QObject):
    """Populate the Tools menu and retain every live non-modal tool window."""

    def __init__(self, menu: QMenu, owner: QMainWindow) -> None:
        super().__init__(owner)
        self.menu = menu
        self.owner = owner
        self._descriptors = {tool.tool_id: tool for tool in TOOLS}
        self._tool_windows: set[ToolWindow] = set()
        self._pxp_windows: set[ToolWindow] = set()
        self._graph_windows: set[GraphEditorWindow] = set()
        self._last_graph: GraphEditorWindow | None = None
        self.actions: dict[str, QAction] = {}
        for descriptor in TOOLS:
            action = menu.addAction(descriptor.title)
            action.triggered.connect(
                lambda _checked=False, tool_id=descriptor.tool_id: self.open_tool(
                    tool_id
                )
            )
            self.actions[descriptor.tool_id] = action

    @property
    def open_windows(self) -> tuple[ToolWindow, ...]:
        """Return currently live tool windows."""
        return tuple(self._tool_windows)

    def open_tool(self, tool_id: str) -> ToolWindow:
        """Create another independent instance of an implemented tool."""
        descriptor = self._descriptors[tool_id]
        # Keep utility windows as true top-level windows.  Passing the main
        # window as their native parent makes Windows minimize them together.
        # Lifetime is still explicit because this manager retains each window.
        if tool_id == "pxp_viewer":
            from materials_data_studio.tools.graph_window import GraphEditorWindow

            window = GraphEditorWindow()
            self._graph_windows.add(window)
            self._last_graph = window
            window.activated.connect(lambda target=window: self._activate_graph(target))
            window.destroyed.connect(lambda _object=None, target=window: self._forget_graph(target))
        else:
            window = descriptor.factory(None)
        self._tool_windows.add(window)
        window.destroyed.connect(
            lambda _object=None, target=window: self._tool_windows.discard(target)
        )
        window.destroyed.connect(
            lambda _object=None, target=window: self._pxp_windows.discard(target)
        )
        window.show()
        window.raise_()
        window.activateWindow()
        return window

    def _activate_graph(self, window: GraphEditorWindow) -> None:
        self._last_graph = window

    def _forget_graph(self, window: GraphEditorWindow) -> None:
        self._graph_windows.discard(window)
        if self._last_graph is window:
            self._last_graph = next(iter(self._graph_windows), None)

    def open_graph(self, path: Path) -> None:
        from materials_data_studio.tools.graph_window import GraphEditorWindow

        window = self.open_tool("pxp_viewer")
        assert isinstance(window, GraphEditorWindow)
        window.open_graph_path(path)
        remember = getattr(self.owner, "remember_file", None)
        if window.editor is not None and callable(remember):
            remember(path)

    def save_graph(self, *, save_as: bool = False) -> None:
        from PySide6.QtWidgets import QApplication, QMessageBox

        window = QApplication.activeWindow()
        if window not in self._graph_windows:
            window = self._last_graph
        if window is None:
            QMessageBox.information(self.owner, "Graph Document", "Open an Igor experiment or graph document first.")
            return
        if save_as:
            window.save_document_as()  # type: ignore[attr-defined]
        else:
            window.save_document()  # type: ignore[attr-defined]

    def confirm_graph_shutdown(self) -> bool:
        return all(window._discard_approved or window.confirm_close() for window in tuple(self._graph_windows))

    def close_graph_documents(self) -> None:
        for window in tuple(self._graph_windows):
            window._discard_approved = True
            window.close()

    def open_pxp(self, path: Path) -> PxpViewerWindow:
        from materials_data_studio.tools.pxp_window import PxpViewerWindow

        window = self.open_tool("pxp_viewer")
        assert isinstance(window, PxpViewerWindow)
        window.open_path(path)
        return window

    def close_pxp_viewers(self) -> None:
        for window in tuple(self._pxp_windows):
            self._tool_windows.discard(window)
            self._pxp_windows.discard(window)
            window.close()

    def sync_pxp_viewers(self) -> None:
        """Rebind open tools or recreate them from a loaded MDSS snapshot."""
        documents = self.owner.state.pxp_documents  # type: ignore[attr-defined]
        if not documents and not self._pxp_windows:
            return
        from materials_data_studio.tools.pxp_window import PxpViewerWindow

        windows = [window for window in tuple(self._pxp_windows)
                   if isinstance(window, PxpViewerWindow) and window.document is not None]
        for window in windows:
            window.refresh_from_state()
        open_ids = {window.document.source_id for window in windows
                    if window.document is not None and window.document.source_id in documents}
        for source_id, document in documents.items():
            if source_id not in open_ids:
                # Legacy MDSS imports retain their old workspace undo/history;
                # new imports use the independent graph-document editor instead.
                viewer = PxpViewerWindow(owner=self.owner)
                self._pxp_windows.add(viewer)
                self._tool_windows.add(viewer)
                viewer.destroyed.connect(lambda _object=None, target=viewer: self._tool_windows.discard(target))
                viewer.destroyed.connect(lambda _object=None, target=viewer: self._pxp_windows.discard(target))
                viewer.bind_document(document)
                viewer.show()
