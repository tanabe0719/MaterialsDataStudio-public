"""Non-modal MDS Graph Editor; Igor files remain immutable import sources."""

from __future__ import annotations

import copy
import math
import uuid
from contextlib import suppress
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from threading import Event
from typing import Any, cast

import numpy as np
from matplotlib.backends.backend_qt import NavigationToolbar2QT
from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent, QColor, QCursor, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QInputDialog,
    QLabel,
    QLayout,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QRubberBand,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from materials_data_studio.tools.graph_actions import (
    edit_annotation,
    import_text,
    install_menus,
    run_quick_fit,
    wave_action,
)
from materials_data_studio.tools.graph_document import (
    EditableGraphDocument,
    load_graph_document,
    save_graph_document,
)
from materials_data_studio.tools.graph_drawing import (
    KINDS,
    drawing_operation,
    drawing_transform,
    edit_drawing,
    install_drawing_toolbar,
    moved_drawing,
)
from materials_data_studio.tools.graph_export import GraphDataDialog, SaveGraphicsDialog
from materials_data_studio.tools.graph_handles import DrawingSelectionOverlay, modifier
from materials_data_studio.tools.graph_history import GraphEditorController
from materials_data_studio.tools.graph_menus import graph_context_menu, zoom_region
from materials_data_studio.tools.graph_render import (
    IgorMatplotlibRenderer,
    explicit_font,
    label_position,
)
from materials_data_studio.tools.graph_symbols import visual_choices, visual_combo
from materials_data_studio.tools.pxp_model import (
    PxpAnnotation,
    PxpAnnotationStyle,
    PxpAxis,
    PxpDocument,
    PxpDrawing,
    PxpGraph,
)
from materials_data_studio.tools.pxp_render import (
    _annotation_anchor,
    _AnnotationFrame,
    _graph_margins,
    _style,
    _visual_scale,
    annotation_offsets,
)
from materials_data_studio.tools.pxp_style import COLORMAPS
from materials_data_studio.tools.pxp_window import PxpViewerWindow
from materials_data_studio.ui.icons import toolbar_icon
from materials_data_studio.ui.panels import compact_forms
from materials_data_studio.ui.style_inputs import ColorEdit, FontEdit, colormap_swatches
from materials_data_studio.ui.tasks import blocking_task, run_task


class GraphSelectionBand(QRubberBand):
    """Igor-like dashed outline: the selected data remains unobscured."""

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        color = QColor("#334155")
        painter.setPen(QPen(color, 1, Qt.PenStyle.DashLine))
        box = self.rect().adjusted(2, 2, -3, -3)
        painter.drawRect(box)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        for point in (box.topLeft(), box.topRight(), box.bottomLeft(), box.bottomRight()):
            painter.drawRect(QRect(point - QPoint(2, 2), QSize(4, 4)))
        painter.end()


class DrawingGridOverlay(QWidget):
    """Editing-only grid; never enters a graph's figure/export or history."""

    def __init__(self, canvas: QWidget) -> None:
        super().__init__(canvas)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        canvas.installEventFilter(self)
        self.hide()

    def eventFilter(self, watched: Any, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Resize:
            self.setGeometry(watched.rect())
        return False

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setPen(QPen(QColor(128, 128, 128, 60), .5))
        for x in range(0, self.width(), 10):
            painter.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), 10):
            painter.drawLine(0, y, self.width(), y)
        painter.end()


class GraphEditorWindow(PxpViewerWindow):
    """Independent documents/history: never add newly opened PXP files to MDSS."""

    activated = Signal()
    analysis_menu: QMenu
    statistics_menu: QMenu
    data_menu: QMenu

    def __init__(self, parent: QWidget | None = None, **kwargs: Any) -> None:
        self.editor: GraphEditorController | None = None
        self._local_view: dict[str, Any] = {}
        self._property_target = ("axis", "bottom")
        self._drag: dict[str, Any] | None = None
        self._region_drag: dict[str, Any] | None = None
        self.region: dict[str, Any] | None = None
        self._discard_approved = False
        self._text_import_in_progress = False
        self._recovery_instance = uuid.uuid4().hex
        self.artist_targets: dict[Any, tuple[str, str]] = {}
        self.renderer = IgorMatplotlibRenderer()
        self.mode = "Pointer"
        self.drawing_actions: dict[str, QAction] = {}
        self.drawing_defaults = PxpDrawing("DrawLine", (0, 0, .2, .2))
        self.drawing_selected: int | None = None
        self._selected_object: tuple[str, str] | None = None
        self.drawing_grid = False
        self.drawing_snap = False
        self._bezier_drag: dict[str, Any] | None = None
        super().__init__(parent, **kwargs)
        from materials_data_studio.ui.input_wheel_guard import install_input_wheel_guard
        install_input_wheel_guard()
        self.setWindowTitle("MDS Graph Editor — Igor PXP Viewer")
        self.setAcceptDrops(True)
        self.open_button.hide()
        self.source_label.hide()
        self.resize(1500, 880)
        # Reserve guidance space before any graph is rendered. Creating it on
        # the first press resizes the canvas and invalidates the blit background
        # and Axes held by an in-progress drawing.
        self.statusBar()
        self.navigation = NavigationToolbar2QT(self.canvas, self)
        self.navigation.hide()
        self.canvas.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.properties = QTabWidget()
        self.properties.setObjectName("GraphProperties")
        self.properties.tabBar().hide()
        self.option_layout.insertWidget(1, self.properties, 1)
        self.property_selector = QComboBox()
        self.property_selector.setAccessibleName("Property category")
        self.option_layout.insertWidget(1, self.property_selector)
        self.property_selector.currentIndexChanged.connect(self.properties.setCurrentIndex)
        self.properties.currentChanged.connect(self.property_selector.setCurrentIndex)
        self.option_layout.takeAt(self.option_layout.count() - 1)
        self.apply_button = QPushButton("Apply properties")
        self.apply_button.clicked.connect(self.apply_active_properties)
        self.option_layout.addWidget(self.apply_button)
        self.options.setMinimumWidth(330)
        self.property_widgets: dict[str, dict[str, Any]] = {}
        heading_item = self.option_layout.itemAt(0)
        heading = heading_item.widget() if heading_item is not None else None
        if heading is not None:
            heading.setProperty("text", "Properties")
        self.file_menu = self.menuBar().addMenu("File")
        self.open_action = self._action("Open experiment / graph (.pxp / .mdsg / .h5xp)…", self.choose_file, "Ctrl+O")
        self._action("Open Graph Document (.mdsg / .h5xp)…", self.choose_graph, "Ctrl+Shift+O")
        self.save_action = self._action("Save Graph Document", self.save_document, "Ctrl+S")
        self._action("Save Graph Document As…", self.save_document_as, "Ctrl+Shift+S")
        self.import_action = self._action("Load General Text…", lambda: import_text(self), "Ctrl+I")
        self.export_figure_action = self._action("Export Figure…", self.export_graph, "Ctrl+Shift+E")
        self.export_data_action = self._action("Export Data…", self.export_data, "Ctrl+E")
        self.send_to_igor_action = self._action("Send to Igor Pro", self.send_to_igor)
        self.send_to_igor_action.setEnabled(False)
        self.export_figure_action.setEnabled(False)
        self.export_data_action.setEnabled(False)
        self._action("Recover Graph Document…", self.recover_document)
        from materials_data_studio.ui.graph_workflow_dialogs import (
            compatibility_dialog,
            history_dialog,
            roi_dialog,
            share_graphs,
        )
        self._action("Create reviewed sharing copy…", lambda: share_graphs(self))
        self._action("Image ROI / line profiles…", lambda: roi_dialog(self))
        self._action("Compatibility report…", lambda: compatibility_dialog(self))
        self._action("Processing history / command recipe…", lambda: history_dialog(self))
        self.edit_menu = self.menuBar().addMenu("Edit")
        self.quick_toolbar = self.addToolBar("Graph Quick Actions")
        self.quick_toolbar.setObjectName("QuickActionsToolbar")
        self.quick_toolbar.setIconSize(QSize(15, 15))
        self.quick_toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.quick_toolbar.setMovable(False)
        self.quick_toolbar.setFloatable(False)
        for action in (self.open_action, self.save_action, self.import_action):
            self.quick_toolbar.addAction(action)
        self.quick_toolbar.addSeparator()
        self.quick_toolbar.addAction(self.export_data_action)
        self.quick_toolbar.addAction(self.export_figure_action)
        self.quick_toolbar.addAction(self.send_to_igor_action)
        self.quick_toolbar.addSeparator()
        self.undo_action = QAction("Undo", self)
        self.redo_action = QAction("Redo", self)
        self.undo_action.setEnabled(False)
        self.redo_action.setEnabled(False)
        self.quick_toolbar.addAction(self.undo_action)
        self.quick_toolbar.addAction(self.redo_action)
        self.update_icons()
        self.drawing_grid_overlay = DrawingGridOverlay(self.canvas)
        self.drawing_selection_overlay = DrawingSelectionOverlay(self)
        self.drawing_selection_overlay.show()
        install_drawing_toolbar(self)
        self._insert_drag: dict[str, Any] | None = None
        self.rubber_band = GraphSelectionBand(QRubberBand.Shape.Rectangle, self.canvas)
        self.drag_paint_timer = QTimer(self)
        self.drag_paint_timer.setSingleShot(True)
        self.drag_paint_timer.setInterval(16)
        self.drag_paint_timer.timeout.connect(self._paint_drag)
        self.mode_actions: dict[str, QAction] = {}
        install_menus(self)
        from materials_data_studio.tools.graph_tables import install_tables
        install_tables(self)
        from materials_data_studio.tools.graph_file_drop import install_file_drops
        self.file_drop_filter = install_file_drops(self)
        self.reset_action = QAction("Reset to Igor Original…", self)
        self.reset_action.triggered.connect(self.reset_original)
        self.edit_menu.addAction(self.reset_action)
        self.install_paste_action()
        for event, handler in (("button_press_event", self.pointer_press),
                               ("motion_notify_event", self.pointer_move),
                               ("button_release_event", self.pointer_release)):
            self.canvas.mpl_connect(event, handler)  # type: ignore[arg-type, call-overload]
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setInterval(120000)
        self.autosave_timer.timeout.connect(self.autosave)
        self.autosave_timer.start()
        self.recovery_path: Path | None = None
        self.canvas.mpl_connect("key_press_event", self.pointer_key)
        from materials_data_studio.tools.command_window import CommandWorkspace
        self.command_workspace = CommandWorkspace(self)
        self.menuBar().removeAction(self.view_menu.menuAction())
        self.view_menu.deleteLater()
        self.view_menu = self.command_workspace.view_menu
        from materials_data_studio.ui.workspace_layout import populate_view_menu
        populate_view_menu(self.command_workspace.view_menu, self.splitter, prepend=True)
        from materials_data_studio.ui.workspace_layout import restore_graph_panels
        restore_graph_panels(self)
        self.help_menu = self.menuBar().addMenu("Help")
        from materials_data_studio.ui.updates import add_update_actions
        add_update_actions(self.help_menu, self)
        self.help_menu.addSeparator()
        self.graph_manual_dialogs: dict[str, Any] = {}
        self.graph_manual_action = self.help_menu.addAction("Graph Viewer Manual (English)")
        self.graph_manual_action.setShortcut("F1")
        self.graph_manual_action.triggered.connect(lambda: self.show_graph_manual("en"))
        self.japanese_graph_manual_action = self.help_menu.addAction("Graph Viewer マニュアル (日本語)")
        self.japanese_graph_manual_action.triggered.connect(lambda: self.show_graph_manual("ja"))
        from materials_data_studio.ui.licenses import show_licenses
        self.english_license_action = self.help_menu.addAction("Licenses and Source (English)", lambda: show_licenses(self, "en"))
        self.japanese_license_action = self.help_menu.addAction("ライセンス・対応ソース (日本語)", lambda: show_licenses(self, "ja"))
        self.help_menu.addSeparator()
        self.python_help_action = self.help_menu.addAction("Python / mds Help…")
        self.python_help_action.triggered.connect(self.command_workspace.show_procedure_help)
        self.history_memory = QLabel("Undo: 0 MiB")
        self.history_memory.setToolTip("Graph Editor's independent retained-history storage; not process RAM.")
        self.statusBar().addPermanentWidget(self.history_memory)
        from PySide6.QtWidgets import QHBoxLayout

        from materials_data_studio.tools.graph_actions import graph_action
        self.start_panel = QWidget()
        row = QHBoxLayout(self.start_panel)
        for title, callback in (("Open experiment / graph", self.choose_file), ("Import general text", lambda: import_text(self)), ("New graph", lambda: graph_action(self, "new"))):
            button = QPushButton(title)
            button.clicked.connect(callback)
            row.addWidget(button)
        central_layout = self.centralWidget().layout()
        if isinstance(central_layout, QVBoxLayout):
            central_layout.insertWidget(0, self.start_panel)

    def pointer_key(self, event: Any) -> None:
        if event.key in {"delete", "backspace"}:
            self.delete_selected()
            return
        if event.key in {"enter", "return"} and self._insert_drag:
            from materials_data_studio.tools.graph_insertion import finish
            finish(self)
            return
        if event.key == "escape":
            self._insert_drag = None
            self._bezier_drag = None
            self.drag_paint_timer.stop()
            self._drag = None
            self.clear_region()
            graph = self.graph()
            if graph is not None:
                self._draw_graph(graph)

    @property
    def view_state(self) -> dict[str, Any]:
        return self._local_view

    def _action(self, title: str, callback: Any, shortcut: str = "") -> QAction:
        action = self.file_menu.addAction(title)
        action.triggered.connect(callback)
        if shortcut:
            action.setShortcut(shortcut)
        return action

    def open_path(self, path: Path) -> None:
        if path.suffix.lower() in {".h5xp", ".mdsg"}:
            self.open_graph_path(path)
            return
        if not self.confirm_close():
            return
        try:
            self.bind_document(blocking_task(self, f"Open {path.name}", lambda: self.backend.open(path)))
        except Exception as exc:
            QMessageBox.warning(self, "PXP Open Error", str(exc))

    def bind_document(self, document: PxpDocument) -> None:
        self.bind_graph_document(EditableGraphDocument.from_pxp(document))

    def bind_graph_document(self, document: EditableGraphDocument, path: Path | None = None) -> None:
        self.property_widgets.clear()
        self._property_selection = None
        self.start_panel.hide()
        self._remove_recovery()
        # Undo actions are also owned internally by their stack. Detach them
        # before replacing that stack, not after its C++ actions are destroyed.
        for old in (self.undo_action, self.redo_action):
            self.quick_toolbar.removeAction(old)
            self.edit_menu.removeAction(old)
            old.deleteLater()
        self._local_view = {}
        editable_path = path if path is not None and path.suffix.lower() == ".mdsg" else None
        self.editor = GraphEditorController(document, saved=editable_path is not None)
        self.editor.path = editable_path
        self.edit_menu.clear()
        for title, shortcut, method, attribute in (("Undo", "Ctrl+Z", self.editor.history.createUndoAction, "undo_action"),
                                        ("Redo", "Ctrl+Y", self.editor.history.createRedoAction, "redo_action")):
            action = method(self, title)
            action.setShortcut(shortcut)
            action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            setattr(self, attribute, action)
            self.edit_menu.addAction(action)
            self.quick_toolbar.addAction(action)
        self.update_icons()
        self.edit_menu.addAction(self.reset_action)
        self.install_paste_action()
        self.editor.history.indexChanged.connect(self._state_changed)
        self.editor.history.indexChanged.connect(lambda _index: QTimer.singleShot(0, self._update_history_memory))
        self.editor.history.cleanChanged.connect(self._title)
        import os

        from materials_data_studio.core.recovery import recovery_directory
        self.recovery_path = recovery_directory() / f"graph-recovery-{os.getpid()}-{self._recovery_instance}.mdsg"
        # The inherited explorer stays read-only; its renderer gets a current
        # immutable graph projection, never a mutable original import.
        super().bind_document(document.render_source())
        self.command_workspace.bind()
        self._title()

    def install_paste_action(self) -> None:
        action = self.edit_menu.addAction("Paste Drawing")
        action.triggered.connect(self.paste_drawing)

    def paste_drawing(self) -> None:
        graph = self.graph()
        if graph is None or self.editor is None:
            return
        payload = QApplication.clipboard().text()
        try:
            import json

            from materials_data_studio.tools.pxp_snapshot import _tuple_values
            if len(payload) > 1024 * 1024:
                raise ValueError("Drawing clipboard is too large")
            record = json.loads(payload)
            if not isinstance(record, dict) or record.get("mds_drawing") != 1:
                return
            drawing = PxpDrawing(**_tuple_values(record["drawing"]))
            if drawing.kind not in KINDS.values() or not all(math.isfinite(v) for v in drawing.coordinates):
                raise ValueError("Invalid drawing clipboard")
            count = len(drawing.coordinates)
            valid = (
                count == 2 if drawing.kind == "DrawText"
                else count in {4, 6} if drawing.kind == "DrawArc"
                else count >= 8 and not (count // 2 - 1) % 3 if drawing.kind == "DrawBezier"
                else count >= 4 if drawing.kind == "DrawPoly"
                else count == 4
            )
            if count % 2 or not valid:
                raise ValueError("Invalid drawing coordinates")
            drawing = moved_drawing(self.figure.axes[0], drawing, (12, -12))
            self.editor.edit_graph(replace(graph, drawings=(*graph.drawings, drawing)), "Paste drawing")
            self.select_target("drawing", str(len(graph.drawings)))
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            QMessageBox.warning(self, "Paste Drawing", str(exc))

    def choose_graph(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Open Graph Document", "", "Graph Documents (*.mdsg *.h5xp)")
        if filename:
            self.open_graph_path(Path(filename))

    def send_to_igor(self, _checked: bool = False) -> None:
        if self.editor is None:
            return
        from materials_data_studio.core.settings import create_settings
        from materials_data_studio.integrations.igor import (
            IGOR_EXECUTABLE_SETTING,
            IgorLaunchError,
            IgorTransferService,
        )

        self.command_workspace.flush()
        document = replace(self.editor.document, current=self.editor.document.current.clone())
        executable = str(create_settings().value(IGOR_EXECUTABLE_SETTING, ""))
        try:
            result = blocking_task(
                self, "Send to Igor Pro",
                lambda: IgorTransferService().send_document(document, executable),
                allow_cancel=False,
            )
        except IgorLaunchError as exc:
            QMessageBox.warning(self, "Send to Igor Pro", f"{exc}\n\nThe experiment was retained at:\n{exc.experiment_path}")
            return
        except Exception as exc:
            QMessageBox.warning(self, "Send to Igor Pro", str(exc))
            return
        self.statusBar().showMessage(f"Sent document to Igor Pro: {result.destination}", 8000)

    def open_graph_path(self, path: Path) -> None:
        if not self.confirm_close():
            return
        try:
            from materials_data_studio.tools.graph_h5xp import load_h5xp
            document = blocking_task(self, f"Open {path.name}", lambda: load_h5xp(path) if path.suffix.lower() == ".h5xp" else load_graph_document(path))
            self.bind_graph_document(document, path)
        except Exception as exc:
            QMessageBox.warning(self, "Graph Document Open Error", str(exc))

    def save_document(self, _checked: bool = False) -> bool:
        if self.editor is None:
            return False
        if self.editor.path is None:
            return self.save_document_as()
        return self._save(self.editor.path)

    def save_document_as(self, _checked: bool = False) -> bool:
        if self.editor is None:
            return False
        suggested = (self.editor.path or Path(self.editor.document.source.basename)).with_suffix(".mdsg")
        filename, selected = QFileDialog.getSaveFileName(self, "Save Graph Document", str(suggested), "MDS Graph Document (*.mdsg);;Igor HDF5 Experiment (*.h5xp)")
        if not filename:
            return False
        path = Path(filename)
        if not path.suffix or ("*.h5xp" in selected and path.suffix.lower() == ".mdsg"):
            path = path.with_suffix(".h5xp" if "*.h5xp" in selected else ".mdsg")
        return self._save(path)

    def _save(self, path: Path) -> bool:
        assert self.editor is not None
        self.command_workspace.flush()
        try:
            if path.suffix.lower() == ".h5xp":
                from materials_data_studio.tools.graph_h5xp import save_h5xp
                if path.exists():
                    raise FileExistsError("H5XP export cannot overwrite an existing file. Choose a new filename; use MDSG for editable saves.")
                if QMessageBox.question(self, "Export Igor Experiment", "Export all working waves, graphs (including annotations and drawings), and tables? User procedures are excluded. H5XP is export-only; save MDSG to preserve the editable document and original import.") != QMessageBox.StandardButton.Yes:
                    return False
                document = replace(self.editor.document, current=self.editor.document.current.clone())
                blocking_task(self, "Export H5XP", lambda: save_h5xp(path, document), allow_cancel=False)
                self.statusBar().showMessage("Exported entire document as H5XP; editable save remains MDSG", 5000)
                return True
            elif path.suffix.lower() == ".mdsg":
                document = replace(self.editor.document, current=self.editor.document.current.clone())
                blocking_task(self, "Save graph document", lambda: save_graph_document(path, document), allow_cancel=False)
                self.editor.path, self.editor.saved = path, True
                self.editor.history.setClean()
            else:
                raise ValueError("Choose .mdsg or .h5xp; original .pxp files are never overwritten.")
        except Exception as exc:
            QMessageBox.warning(self, "Graph Document Save Error", str(exc))
            return False
        self._remove_recovery()
        self._title()
        return True

    def _update_history_memory(self) -> None:
        from shiboken6 import isValid
        if self.editor is not None and isValid(self.history_memory):
            self.history_memory.setText(f"Undo: {self.editor.retained_memory_bytes / 1024**2:.1f} MiB")

    def _title(self, *_args: Any) -> None:
        from shiboken6 import isValid
        if not isValid(self) or (self.editor is not None and not isValid(self.editor.history)):
            return
        if self.editor is None:
            return
        name = self.editor.path.name if self.editor.path else self.editor.document.source.basename
        graph = self._selection[1] if self._selection[0] == "graph" else ""
        self.setWindowTitle(f"MDS Graph Editor — {graph} — {name}" + (" [Modified]" if self.editor.dirty else ""))

    def _state_changed(self, *_args: Any) -> None:
        from shiboken6 import isValid
        if not isValid(self) or (self.editor is not None and not isValid(self.editor.history)):
            return
        if self.editor is not None:
            projection = self.editor.document.render_source()
            def structure(d):
                return (tuple((w.id, w.igor_path, w.shape) for w in d.waves), tuple(g.name for g in d.graphs), d.table_definitions, d.folders) if d else None
            if structure(self.document) != structure(projection):
                super().bind_document(projection)
                if not projection.waves and not projection.graphs and not projection.tables:
                    self._selection = ("", "")
                    self.figure.clear()
                    self.canvas.draw_idle()
            else:
                self.document = projection
                self._show_selection()
            self._title()
            self.command_workspace.sync_source()

    def _draw_graph(self, graph: PxpGraph) -> None:
        if self.editor is None:
            return
        self._drag = None
        self.clear_region()
        self.artist_targets = self.renderer.render(self.figure, self.editor.document, graph.name)
        self.canvas.draw_idle()
        source = self.editor.document.source
        issues = [*source.issues, *graph.issues]
        from materials_data_studio.tools.graph_fidelity import assess_graph
        report = assess_graph(graph,self.editor.document.render_source())
        detail = report.detail + "\n" + "; ".join(f"{item.scope}: {item.message}" for item in source.issues)
        self.source_label.setText(f"PXP source · Read-only · {source.basename}    Igor Fidelity: {self.editor.document.fidelity(graph.name)}")
        self.compatibility.setText(f"{graph.name} · {report.summary}")
        from html import escape
        self.compatibility.setToolTip("<pre>" + escape(detail) + "</pre>")
        self.compatibility.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if issues or report.diagnostics:
            self.compatibility.setText(f"{graph.name} · {report.summary} · {len(report.diagnostics)+len(source.issues)} notes (hover for details)")

    def _show_selection(self) -> None:
        previous_selection = getattr(self, "_property_selection", self._selection)
        if previous_selection != self._selection and any(self._property_record_dirty(record) for record in self.property_widgets.values()) and QMessageBox.question(self, "Unapplied property edits", "Discard pending property edits before changing the graph or wave selection? Choose No to stay and apply them first.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            self._selection = previous_selection
            return
        self._property_selection = self._selection
        self._selected_object = None
        self.drawing_selected = None
        self._bezier_drag = None
        self.drawing_selection_overlay.update()
        self._drag = None
        self.clear_region()
        # Forms belong to the selected graph, not to the document. Non-graph
        # selections do not call _graph_controls(), so clear the Python registry
        # here, BEFORE deleteLater destroys its C++ controls on the next event.
        self.property_widgets.clear()
        while self.properties.count():
            widget = self.properties.widget(0)
            self.properties.removeTab(0)
            if widget is not None:
                widget.deleteLater()
        self.options.setEnabled(True)
        super()._show_selection()
        self.send_to_igor_action.setEnabled(self.editor is not None)
        if self.editor is not None and self._selection[0] == "table":
            from materials_data_studio.tools.graph_tables import show_table
            show_table(self, self._selection[1])
        for action in (self.export_figure_action, self.export_data_action):
            action.setEnabled(self.graph() is not None)
        if self.editor is not None and self._selection[0] == "wave":
            wave = next((w for w in self.document.waves if w.id == self._selection[1]), None) if self.document else None
            if wave:
                button = QPushButton("Edit working wave…")
                button.clicked.connect(lambda _checked=False, path=wave.igor_path: wave_action(self, "edit", path))
                self.option_form.addRow(button)
                self.compatibility.setText(f"{wave.igor_path} · {wave.dtype} · {wave.shape} · Working wave (original PXP preserved)")
        self._title()

        self.property_selector.blockSignals(True)
        self.property_selector.clear()
        self.property_selector.addItems([self.properties.tabText(i) for i in range(self.properties.count())])
        self.property_selector.setCurrentIndex(self.properties.currentIndex())
        self.property_selector.blockSignals(False)
        compact_forms(self.properties)
        for index in range(self.properties.count()):
            scroll = self.properties.widget(index)
            page = scroll.widget() if isinstance(scroll, QScrollArea) else None
            layout = page.layout() if page is not None else None
            if layout is not None:
                layout.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)

    def show_graph_manual(self, language: str = "en") -> None:
        """Open lazily, without blocking editing or changing document history."""
        from materials_data_studio.ui.manual import UserManualDialog, resolve_graph_manual_path
        dialog = self.graph_manual_dialogs.get(language)
        if dialog is None:
            try:
                dialog = UserManualDialog(resolve_graph_manual_path(language), self)
            except (OSError, UnicodeError) as exc:
                QMessageBox.warning(self, "Graph Viewer Manual", str(exc))
                return
            self.graph_manual_dialogs[language] = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def update_icons(self) -> None:
        from materials_data_studio.ui.theme import current_theme

        theme = current_theme()
        for action, name in ((self.open_action, "open"), (self.save_action, "save"), (self.import_action, "import"),
                             (self.export_data_action, "export_data"), (self.export_figure_action, "export_figure"),
                             (self.send_to_igor_action, "send_to_igor"),
                             (self.undo_action, "undo"), (self.redo_action, "redo")):
            action.setIcon(toolbar_icon(name, theme))
            action.setToolTip(action.text().replace("&", ""))

    def graph(self) -> PxpGraph | None:
        if self.editor is not None and self._selection[0] == "graph":
            return next((g for g in self.editor.document.current.graphs if g.name == self._selection[1]), None)
        return None

    def graph_layout(self) -> dict[str, Any]:
        graph = self.graph()
        return copy.deepcopy(self.editor.document.current.layout.get(graph.name, {})) if self.editor and graph else {}

    def _graph_controls(self, graph: PxpGraph) -> None:
        self.property_widgets = {}
        axes = list(dict.fromkeys([*(a.name for a in graph.axes), "bottom", *(t.axis for t in graph.traces), *(t.x_axis for t in graph.traces)]))
        if not graph.traces:
            axes = list(dict.fromkeys([*axes, "left"]))
        self._tab("Axes", "axis", axes, self._axis_fields)
        if graph.traces:
            self._tab("Traces", "trace", [t.name for t in graph.traces], self._trace_fields)
        annotations = [a.name for a in graph.annotations]
        if graph.legend_raw and not annotations:
            annotations = ["legend"]
        if annotations:
            self._tab("Text / Legend", "annotation", annotations, self._annotation_fields)
        if graph.images:
            self._tab("Images", "image", [i.wave for i in graph.images], self._image_fields)
            self._tab("ColorScale", "colorbar", [i.wave for i in graph.images], self._colorbar_fields)
        self._tab("Geometry", "geometry", [graph.name], lambda _id: [
            ("width_points", "Plot width (pt)", graph.style.width_points, float),
            ("height_points", "Plot height (pt)", graph.style.height_points, float),
            ("width_aspect", "Width / height aspect", graph.style.width_aspect, float),
            ("height_aspect", "Height / width aspect", graph.style.height_aspect, float),
            ("font_family", "Graph default font", graph.style.font_family, str),
            ("font_size", "Graph default size (pt; 0 = auto)", graph.style.font_size, float),
            ("relative_font_size", "Relative font size (%)", graph.style.relative_font_size, float),
            ("font_multiplier", "Font / marker multiplier (%)", graph.style.font_multiplier, float),
            ("marker_size", "Graph default marker size", graph.style.marker_size, float),
            *[(f"margin_{side}", f"{side.title()} margin (pt)", dict(graph.style.margins).get(side), float)
              for side in ("left", "right", "bottom", "top")]])

    def _tab(self, title: str, kind: str, identifiers: list[str], fields: Any) -> None:
        page = QWidget()
        form = QFormLayout(page)
        form.setSizeConstraint(QLayout.SizeConstraint.SetDefaultConstraint)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        selector = QComboBox()
        selector.addItems(identifiers)
        form.addRow("Selected object", selector)
        scroll = QScrollArea()
        scroll.setProperty("graph_property_kind", kind)
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        index = self.properties.addTab(scroll, title)
        record: dict[str, Any] = {"selector": selector, "form": form, "widgets": {}, "initial": {}}
        self.property_widgets[kind] = record

        def populate(identifier: str) -> None:
            if self.property_widgets.get(kind) is not record or not isValid(form):
                return
            previous = record.get("identifier")
            if previous and previous != identifier and self._property_record_dirty(record):
                response = QMessageBox.question(self, "Unapplied property edits", "Apply changes before switching objects? No discards the pending form edits.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
                if response == QMessageBox.StandardButton.Cancel:
                    selector.blockSignals(True)
                    selector.setCurrentText(previous)
                    selector.blockSignals(False)
                    return
                if response == QMessageBox.StandardButton.Yes:
                    apply_fields(previous)
                    return
            record["identifier"] = identifier
            record["widgets"], record["initial"] = {}, {}
            while form.rowCount() > 1:
                form.removeRow(1)
            for key, caption, value, parser in fields(identifier):
                if isinstance(parser, list):
                    if key in {"marker", "line_style"}:
                        graph = self.graph()
                        widget = visual_combo("marker" if key == "marker" else "line", value,
                                              self.palette().window().color().lightness() < 128,
                                              graph.style.dash_patterns if graph else ())
                    else:
                        widget = QComboBox()
                        for text, data in parser:
                            widget.addItem(text, data)
                        if key == "color_table":
                            colormap_swatches(widget)
                        widget.setCurrentIndex(max(0, widget.findData(value)))
                    value = widget.currentData()
                elif parser is bool:
                    widget = QCheckBox()
                    widget.setChecked(bool(value))
                    value = bool(value)
                else:
                    control = FontEdit if key in {"font", "font_family"} else ColorEdit if key in {"color", "marker_fill", "marker_edge"} or key.endswith("_color") else QLineEdit
                    widget = control("" if value is None else str(value))
                    if parser in {float, int}:
                        widget.setPlaceholderText("Auto / inherited")
                    value = widget.text()
                # Many axis fields must scroll, not squeeze below their text
                # metrics when the containing window is shorter than the form.
                widget.setMinimumHeight(max(widget.sizeHint().height(), widget.fontMetrics().height() + 8))
                form.addRow(caption, widget)
                record["widgets"][key] = (widget, parser)
                record["initial"][key] = value
            compact_forms(page)
            form.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)

        def apply_fields(identifier: str) -> None:
            if (self.property_widgets.get(kind) is not record
                or any(not isValid(widget) for widget, _parser in record["widgets"].values())):
                return
            changes: dict[str, Any] = {}
            try:
                for key, (widget, parser) in record["widgets"].items():
                    value = widget.text() if isinstance(widget, FontEdit) else widget.currentData() if isinstance(widget, QComboBox) else widget.isChecked() if isinstance(widget, QCheckBox) else widget.text()
                    if value != record["initial"][key]:
                        changes[key] = value if isinstance(parser, list) or parser is bool else (None if value == "" and parser in {float, int} else parser(value))
                self.apply_properties(kind, identifier, changes)
            except (ValueError, TypeError) as exc:
                QMessageBox.warning(self, "Invalid Graph Property", str(exc))

        selector.currentTextChanged.connect(populate)
        record["apply"] = lambda: apply_fields(record["identifier"])
        preferred_kind, preferred_id = self._property_target
        if kind == preferred_kind and preferred_id in identifiers:
            selector.setCurrentText(preferred_id)
            self.properties.setCurrentIndex(index)
        populate(selector.currentText())

    def apply_active_properties(self) -> None:
        page = self.properties.currentWidget()
        if page is not None:
            record = self.property_widgets.get(page.property("graph_property_kind"))
            if record:
                record["apply"]()

    @staticmethod
    def _property_record_dirty(record: dict[str, Any]) -> bool:
        if any(not isValid(widget) for widget, _parser in record["widgets"].values()):
            return False  # A disposed form cannot have pending editable values.
        for key, (widget, _parser) in record["widgets"].items():
            value = widget.text() if isinstance(widget, FontEdit) else widget.currentData() if isinstance(widget, QComboBox) else widget.isChecked() if isinstance(widget, QCheckBox) else widget.text()
            if value != record["initial"].get(key):
                return True
        return False

    def _axis_fields(self, identifier: str) -> list[Any]:
        graph = self.graph()
        assert graph is not None
        axis = next((a for a in graph.axes if a.name == identifier), PxpAxis(identifier))
        style = _style(graph, identifier)
        position = self.graph_layout().get("labels", {}).get(identifier, {})
        return [
            ("minimum", "Range minimum", axis.limits[0] if axis.limits else None, float),
            ("maximum", "Range maximum", axis.limits[1] if axis.limits else None, float),
            ("log", "Scale", style.log or 0, [("Linear", 0), ("Log 10", 1), ("Log 2", 2)]),
            ("major_interval", "Major interval", style.manual_ticks[1] * 10**style.manual_ticks[2] if style.manual_ticks else None, float),
            ("minor_count", "Minor ticks / major interval", style.manual_minor[0] if style.manual_minor else None, int),
            ("tick_count", "Approximate major tick count (auto subset)", style.tick_count, int),
            ("tick_separation", "Minimum log minor separation (px)", style.tick_separation, float),
            ("tick_zap_text", "Hide tick labels at values (comma-separated)", ",".join(map(str, style.tick_zap or ())), str),
            ("minor_enabled", "Show minor ticks", style.minor_enabled is not False, bool),
            ("tick", "Tick direction", style.tick or 0, [("Outside", 0), ("Cross", 1), ("Inside", 2), ("None", 3)]),
            ("major_length", "Major tick length (pt)", style.major_length, float),
            ("minor_length", "Minor tick length (pt)", style.minor_length, float),
            ("major_thickness", "Major tick width (pt)", style.major_thickness, float),
            ("minor_thickness", "Minor tick width (pt)", style.minor_thickness, float),
            ("subminor_length", "Log subminor tick length (pt)", style.subminor_length, float),
            ("subminor_thickness", "Log subminor tick width (pt)", style.subminor_thickness, float),
            ("grid_style", "Grid style", style.grid_style or 0, [("Automatic", 0), ("Dotted / dashed", 1), ("All dotted", 2), ("Solid / dotted", 3), ("Solid / tiny dotted", 4), ("All solid", 5)]),
            ("standoff", "Axis stand-off", bool(style.standoff), bool),
            ("label_line_spacing", "Label extra line spacing (pt)", style.label_line_spacing, float),
            ("thickness", "Axis width (pt)", style.thickness, float),
            ("axis_color", "Axis line color (#RRGGBB)", self._hex(style.axis_color) if style.axis_color is not None else "", str),
            ("label_color", "Axis label color (#RRGGBB)", self._hex(style.label_color) if style.label_color is not None else "", str),
            ("major_tick_color", "Major tick color (blank inherits)", self._hex(style.major_tick_color) if style.major_tick_color is not None else "", str),
            ("minor_tick_color", "Minor tick color (blank inherits)", self._hex(style.minor_tick_color) if style.minor_tick_color is not None else "", str),
            ("tick_label_color", "Tick label color (#RRGGBB)", self._hex(style.tick_label_color) if style.tick_label_color is not None else "", str),
            ("tick_enable_min", "Tick / label lower bound (-inf = unbounded)", (style.tick_enable or (-math.inf, math.inf))[0], float),
            ("tick_enable_max", "Tick / label upper bound (inf = unbounded)", (style.tick_enable or (-math.inf, math.inf))[1], float),
            ("mirror", "Mirror / frame", style.mirror or 0, [("None", 0), ("Ticks", 1), ("Frame", 2), ("Ticks + labels", 3)]),
            ("label", "Label (Igor formatting allowed)", axis.label_raw, str),
            ("font_family", "Label / tick font", style.font_family, str),
            ("font_size", "Label / tick size (pt; 0 = graph default)", axis.style.font_size, float),
            ("label_rotation", "Label rotation offset (deg)", style.label_rotation, float),
            ("offset_points", "Label perpendicular offset (pt)", position.get("offset_points"), float),
            ("lateral", "Label lateral position (0–1)", position.get("lateral"), float),
            ("label_position_mode", "Native label coordinate mode", style.label_position_mode or 0, [("Compatibility", 0), ("Window absolute", 1), ("Window font-scaled", 2), ("Axis absolute", 3), ("Axis font-scaled", 4)]),
            ("label_position", "Native perpendicular position (pt)", axis.style.label_position, float),
            ("label_lateral_position", "Native lateral offset (pt; vertical positive down)", axis.style.label_lateral_position, float),
        ]

    def _trace_fields(self, identifier: str) -> list[Any]:
        graph = self.graph()
        assert graph is not None
        trace = next(t for t in graph.traces if t.name == identifier)
        settings = self.graph_layout().get("view", {}).get("traces", {}).get(identifier, {})
        channel_fields = [
            ("z_color_config", "Point color\nwave,min,max,table\n[,reverse[,colorWave]]", ",".join("*" if v is None else str(v) for v in trace.z_color).rstrip(",") if trace.z_color else "", str),
            ("log_z_color", "Logarithmic point colors", trace.log_z_color, bool),
            ("color_scale", "Show point color scale", trace.color_scale, bool),
            ("z_size_config", "Point size\nwave,min,max\nsizeMin,sizeMax[,axis]", ",".join("*" if v is None else str(v) for v in trace.z_size).rstrip(",") if trace.z_size else "", str),
            ("z_marker_config", "Point marker wave", trace.z_marker or "", str),
        ]
        box_fields = [] if trace.box is None else [
            ("box_notched", "Notched median confidence interval", trace.box.notched, bool),
            ("box_quartile_method", "Quartiles", trace.box.quartile_method, [("Tukey", 0), ("Minitab", 1), ("Moore and McCabe", 2), ("Mendenhall and Sincich", 3)]),
            ("box_whisker_config", "Whisker method[,parameters]", ",".join(map(str, trace.box.whisker_method)), str),
            ("box_outlier_config", "Outlier method[,parameters]", ",".join(map(str, trace.box.outlier_method)), str),
            ("box_show_data", "Show raw points", trace.box.show_data, [(n, n) for n in ["All", "None", "Outliers", "FarOutliers"]]),
            ("box_show_mean", "Show mean", trace.box.show_mean, bool),
            ("box_show_fences", "Show inner/outer fences", trace.box.show_fences, bool),
            ("box_jitter", "Maximum jitter (fraction of box width)", trace.box.jitter, float),
        ]
        violin_fields = [] if trace.violin is None else [
            ("violin_bandwidth", "KDE bandwidth (0=automatic)", trace.violin.bandwidth, float),
            ("violin_bandwidth_method", "Bandwidth method", trace.violin.bandwidth_method, [("Silverman", 0), ("Scott", 1), ("Bowman/Azzalini", 2)]),
            ("violin_kernel", "KDE kernel", trace.violin.kernel, [("Epanechnikov", 1), ("Bi-weight", 2), ("Tri-weight", 3), ("Triangular", 4), ("Gaussian", 5), ("Rectangular", 6)]),
            ("violin_extension", "KDE extension (bandwidths)", trace.violin.extension, float),
            ("violin_max_density", "Shared maximum density (0=auto)", trace.violin.max_density, float),
            ("violin_side", "Violin side", trace.violin.side, [("Both", 0), ("Left", 1), ("Right", 2)]),
            ("violin_show_data", "Show raw data", trace.violin.show_data, bool),
            ("violin_show_mean", "Show mean", trace.violin.show_mean, bool),
            ("violin_show_median", "Show median", trace.violin.show_median, bool),
            ("violin_jitter", "Jitter", trace.violin.jitter, float),
        ]
        return [("visible", "Visible", settings.get("visible", True), bool),
                ("axis", "Y axis", trace.axis, [(name,name) for name in dict.fromkeys(["left","right",*(a.name for a in graph.axes if (a.side or a.name) not in {"bottom", "top"})])]),
                ("x_axis", "X axis", trace.x_axis, [(name,name) for name in dict.fromkeys(["bottom","top",*(a.name for a in graph.axes if (a.side or a.name) in {"bottom", "top"})])]),
                ("color", "Color (#RRGGBB)", settings.get("color", self._hex(trace.color_igor)), str),
                ("line_width", "Line width (pt)", trace.line_width, float),
                ("x_offset", "Offset: X", trace.offset[0], float),
                ("y_offset", "Offset: Y", trace.offset[1], float),
                ("x_multiplier", "Offset: X Multiplier (0 = unchanged)", trace.multiplier[0], float),
                ("y_multiplier", "Offset: Y Multiplier (0 = unchanged)", trace.multiplier[1], float),
                ("point_range_text", "Display points first,last (blank=all)", ",".join(map(str, trace.point_range)) if trace.point_range else "", str),
                ("error_width", "Error bar width (pt)", trace.error_width, float),
                ("error_cap", "Error bar cap size (pt)", trace.error_cap, float),
                ("error_cap_thickness", "Error bar cap width (pt)", trace.error_cap_thickness, float),
                ("error_clip", "Clip error bars", trace.error_clip, bool),
                ("line_style", "Line style", trace.line_style, visual_choices("line")),
                ("marker", "Marker", trace.marker or 0, visual_choices("marker")),
                ("mode", "Trace mode", trace.mode or 0, [("Lines", 0), ("Dots", 2), ("Markers", 3), ("Lines + markers", 4), ("Steps", 6)]),
                ("marker_size", "Marker size (Igor diameter, pt)", trace.marker_size, float),
                ("marker_fill", "Marker fill (#RRGGBB / none)", settings.get("marker_fill", ""), str),
                ("marker_edge", "Marker edge (#RRGGBB)", settings.get("marker_edge", ""), str),
                ("marker_thickness", "Marker edge width (pt)", trace.marker_thickness, float),
                *(box_fields if trace.box else violin_fields if trace.violin else channel_fields)]

    @staticmethod
    def _hex(color: tuple[int, int, int] | None) -> str:
        return "#" + "".join(f"{round(value / 257):02x}" for value in color) if color else "#000000"

    def _annotation_fields(self, identifier: str) -> list[Any]:
        graph = self.graph()
        assert graph is not None
        annotation = next((a for a in graph.annotations if a.name == identifier), None)
        style = annotation.style if annotation else graph.legend_style
        x, y, _ = _annotation_anchor(style)
        settings = self.graph_layout().get("annotations", {}).get(identifier, {})
        return [("raw", "Text / legend (\\s(trace) symbol)", annotation.raw if annotation else graph.legend_raw, str),
                ("visible", "Visible", settings.get("visible", True), bool),
                ("x", "Position X (axes fraction)", x, float), ("y", "Position Y (axes fraction)", y, float),
                ("font", "Font", settings.get("font", ""), str),
                ("font_size", "Font size (pt)", style.font_size, float),
                ("color", "Text color (#RRGGBB)", self._hex(style.color), str),
                ("rotation", "Rotation (deg)", settings.get("rotation", 0), float),
                ("frame", "Border", style.frame != 0, bool),
                ("transparent", "Transparent background", style.transparent, bool)]

    def _image_fields(self, identifier: str) -> list[Any]:
        graph = self.graph()
        assert graph is not None
        image = next(i for i in graph.images if i.wave == identifier)
        values = image.color_range or (None, None)
        tables = sorted({*COLORMAPS, "viridis", "plasma", "magma", "inferno", "cividis", "gray", image.color_table})
        return [("color_table", "Colormap", image.color_table, [(t, t) for t in tables]),
                ("minimum", "Color minimum", values[0], float), ("maximum", "Color maximum", values[1], float),
                ("log_color", "Log normalization", image.log_color, bool),
                ("reverse", "Reverse colormap", image.reverse, bool),
                ("interpolation", "Bilinear interpolation", image.interpolation, bool),
                ("hidden", "Hide image", image.hidden, bool),
                ("colorbar_visible", "Show ColorScale", image.colorbar_visible, bool)]

    def _colorbar_fields(self, identifier: str) -> list[Any]:
        graph = self.graph()
        assert graph is not None
        image = next(i for i in graph.images if i.wave == identifier)
        style = image.colorbar_style
        settings = self.graph_layout().get("colorbars", {}).get(identifier, {})
        position = settings.get("position", (None, None))
        return [("colorbar_visible", "Visible", image.colorbar_visible, bool),
                ("colorbar_label_raw", "Label", image.colorbar_label_raw, str),
                ("x", "Position X (axes fraction)", position[0], float),
                ("y", "Position Y (axes fraction)", position[1], float),
                ("width", "Bar width (pt)", style.width, float),
                ("height", "Bar height (pt; 0 = relative)", style.height, float),
                ("height_percent", "Relative height (%)", style.height_percent, float),
                ("vertical", "Vertical", style.vertical, bool),
                ("font", "Font", settings.get("font", "Arial"), str),
                ("font_size", "Tick font size (pt)", style.font_size, float),
                ("label_size", "Label font size (pt)", settings.get("label_size", 14), float)]

    def apply_properties(self, kind: str, identifier: str, changes: dict[str, Any]) -> None:
        """Validate and commit one semantic command; unchanged imported fields survive."""
        graph = self.graph()
        if not self.editor or graph is None or not changes:
            return
        self._property_target = kind, identifier
        layout = self.graph_layout()
        changes = dict(changes)
        for key, value in changes.items():
            if isinstance(value, float) and not math.isfinite(value) and key not in {"tick_enable_min", "tick_enable_max"}:
                raise ValueError(f"{key}: enter a finite number")
            if key == "font_size" and value is not None and value < 0:
                raise ValueError("Font size cannot be negative")
            if key == "font_size" and kind in {"geometry", "axis", "label"} and value is not None and value != 0 and not 3 <= value <= 99:
                raise ValueError("Font size must be 0 (inherited/auto) or 3..99 pt")
            if key in {"label_size", "width_points", "height_points", "width_aspect", "height_aspect", "width", "major_interval"} and value is not None and value <= 0:
                raise ValueError(f"{key}: enter a positive number")
            if key in {"error_width", "error_cap", "error_cap_thickness", "marker_size", "line_width", "marker_thickness", "minor_count", "height", "thickness", "major_length", "minor_length", "major_thickness", "minor_thickness", "subminor_length", "subminor_thickness", "tick_count", "tick_separation"} and value is not None and value < 0:
                raise ValueError(f"{key}: cannot be negative")
            if key in {"color", "marker_fill", "marker_edge"} and value:
                from matplotlib.colors import is_color_like
                if not is_color_like(value):
                    raise ValueError(f"{key}: invalid color")
        if kind in {"axis", "label"}:
            axis = next((a for a in graph.axes if a.name == identifier), PxpAxis(identifier))
            style = axis.style
            for key in ("axis_color", "label_color", "major_tick_color", "minor_tick_color", "tick_label_color"):
                if key not in changes:
                    continue
                value = changes[key]
                if value is None or value == "":
                    changes[key] = None
                elif isinstance(value, (tuple, list)) and len(value) == 3 and all(
                    isinstance(channel, int) and not isinstance(channel, bool) and 0 <= channel <= 65535 for channel in value
                ):
                    changes[key] = tuple(value)
                else:
                    color = QColor(str(value))
                    if not color.isValid() or color.alpha() != 255:
                        raise ValueError(f"{key}: enter an opaque RGB color or leave blank to inherit")
                    changes[key] = tuple(channel * 257 for channel in (color.red(), color.green(), color.blue()))
            if {"tick_enable_min", "tick_enable_max"}.intersection(changes):
                previous = _style(graph, identifier).tick_enable or (-math.inf, math.inf)
                lo = changes.pop("tick_enable_min", previous[0])
                hi = changes.pop("tick_enable_max", previous[1])
                lo, hi = (-math.inf if lo is None else float(lo)), (math.inf if hi is None else float(hi))
                if math.isnan(lo) or math.isnan(hi) or lo > hi:
                    raise ValueError("Tick / label bounds must be ordered numbers; -inf and inf are allowed, NaN is not")
                changes["tick_enable"] = (lo, hi)
            if "tick_zap_text" in changes:
                text = changes.pop("tick_zap_text")
                values = tuple(float(v.strip()) for v in str(text).split(",") if v.strip())
                if len(values) > 3 or not all(math.isfinite(v) for v in values):
                    raise ValueError("TickZap accepts at most three finite values")
                changes["tick_zap"] = values
            limits = axis.limits
            if changes.get("log") and limits is not None and min(limits) <= 0 and not {"minimum", "maximum"}.intersection(changes):
                raise ValueError("Logarithmic limits must be positive; change the range first")
            if "minimum" in changes or "maximum" in changes:
                lo = changes.pop("minimum", limits[0] if limits else None)
                hi = changes.pop("maximum", limits[1] if limits else None)
                if (lo is None) != (hi is None) or (lo is not None and (lo == hi or ((changes.get("log", _style(graph, identifier).log) or 0) and min(lo, hi) <= 0))):
                    raise ValueError("Enter two different range endpoints; logarithmic limits must be positive")
                limits = (lo, hi) if lo is not None else None
            if "major_interval" in changes:
                spacing = changes.pop("major_interval")
                inherited = _style(graph, identifier).manual_ticks
                canon = inherited[0] * 10**inherited[2] if inherited else 0
                digits = max(inherited[3] if inherited else 0, max(0, -int(Decimal(str(spacing)).normalize().as_tuple().exponent))) if spacing is not None else 0
                style = replace(style, manual_ticks=(canon, spacing, 0, digits) if spacing is not None else None,
                                tick_count=6 if graph.style.axis_defaults.tick_count == 0 and spacing else None)
            if "minor_count" in changes:
                count = changes.pop("minor_count")
                style = replace(style, manual_minor=(count, 0) if count is not None else None,
                                minor_enabled=count is None or count > 0)
            position_changes = {key: changes.pop(key) for key in ("offset_points", "lateral") if key in changes}
            position: dict[str, Any] = {}
            if position_changes:
                position = self.current_label_position(identifier)
                position.update({key: value for key, value in position_changes.items() if value is not None})
            label = changes.pop("label", axis.label_raw)
            label = explicit_font(label, changes.get("font_size"), changes.get("font_family"))
            style = replace(style, **changes)
            axis = replace(axis, limits=limits, label_raw=label, style=style)
            graph = replace(graph, axes=tuple(axis if a.name == identifier else a for a in graph.axes) if any(a.name == identifier for a in graph.axes) else (*graph.axes, axis))
            if position_changes:
                graph = self.store_label_position(graph, layout, identifier, position)
            graph = self.reserve_label_margin(graph, layout, identifier)
        elif kind == "trace":
            from materials_data_studio.tools.igor_box import modify_box
            from materials_data_studio.tools.igor_trace_channels import apply_channel
            from materials_data_studio.tools.igor_violin import modify_violin
            traces = list(graph.traces)
            for key, option in [("z_color_config", "zcolor"), ("z_size_config", "zmrksize"), ("z_marker_config", "zmrknum")]:
                if key in changes:
                    value = str(changes.pop(key)).strip()
                    from materials_data_studio.tools.command_services import DocumentServices
                    service = DocumentServices(self.editor.document)
                    apply_channel(traces, identifier, option, "{" + value + "}" if value else "0", lambda ref, svc=service: svc.wave(ref).igor_path)
            for key, option in [("box_notched", "notched"), ("box_quartile_method", "quartileMethod"), ("box_whisker_config", "whiskerMethod"), ("box_outlier_config", "outlierMethod"), ("box_show_data", "showData"), ("box_show_mean", "showMean"), ("box_show_fences", "showFences"), ("box_jitter", "jitter")]:
                if key in changes:
                    value = changes.pop(key)
                    if isinstance(value, bool):
                        value = int(value)
                    if "config" in key:
                        value = "{" + str(value) + "}"
                    modify_box(traces, f"trace={identifier},{option}={value}")
            for key, option in [("bandwidth", "bandwidth"), ("bandwidth_method", "bandwidthMethod"), ("kernel", "kernel"), ("extension", "curveExtension"), ("max_density", "maxDensity"), ("side", "plotSide"), ("show_data", "showData"), ("show_mean", "showMean"), ("show_median", "showMedian"), ("jitter", "jitter")]:
                if "violin_" + key in changes:
                    value = changes.pop("violin_" + key)
                    modify_violin(traces, f"trace={identifier},{option}={int(value) if isinstance(value, bool) else value}")
            graph = replace(graph, traces=tuple(traces))
            trace = next(item for item in graph.traces if item.name == identifier)
            for key, names in (("offset", ("x_offset", "y_offset")), ("multiplier", ("x_multiplier", "y_multiplier"))):
                if any(name in changes for name in names):
                    old = getattr(trace, key)
                    values = tuple(float(changes.pop(name, old[index])) for index, name in enumerate(names))
                    if not np.isfinite(values).all():
                        raise ValueError("Trace offsets and multipliers must be finite")
                    changes[key] = values
            if "axis" in changes or "x_axis" in changes:
                from materials_data_studio.tools.command_services import assign_trace_axes
                graph = assign_trace_axes(graph,identifier,axis=changes.pop("axis",None),x_axis=changes.pop("x_axis",None))
            if "point_range_text" in changes:
                text = str(changes.pop("point_range_text")).strip()
                values = tuple(int(v.strip()) for v in text.split(",")) if text else None
                source_wave = self.editor.document.render_source().wave(next(t.y_wave for t in graph.traces if t.name == identifier))
                if values is not None and (len(values) != 2 or source_wave is None or not 0 <= values[0] <= values[1] < source_wave.shape[0]):
                    raise ValueError("Point range requires first,last inside the Y wave")
                changes["point_range"] = values
            view = layout.setdefault("view", {}).setdefault("traces", {}).setdefault(identifier, {})
            for key in ("visible", "color", "marker_fill", "marker_edge"):
                if key in changes:
                    value = changes.pop(key)
                    if value == "":
                        view.pop(key, None)
                    else:
                        view[key] = value
            graph = replace(graph, traces=tuple(replace(t, **changes) if t.name == identifier else t for t in graph.traces))
        elif kind in {"image", "colorbar"}:
            image = next(i for i in graph.images if i.wave == identifier)
            if kind == "image":
                if "minimum" in changes or "maximum" in changes:
                    old = image.color_range or (None, None)
                    limits = changes.pop("minimum", old[0]), changes.pop("maximum", old[1])
                    if limits[0] is not None and limits[1] is not None and limits[0] >= limits[1]:
                        raise ValueError("Color minimum must be smaller than maximum")
                    changes["color_range"] = limits
                color_limits = changes.get("color_range", image.color_range)
                if changes.get("log_color", image.log_color) and color_limits and color_limits[0] is not None and color_limits[0] <= 0:
                    raise ValueError("Log color minimum must be positive")
                image = replace(image, **changes)
                if "color_table" in changes:
                    from materials_data_studio.tools.pxp_style import freeze_palette
                    image = freeze_palette(image)
            else:
                settings = layout.setdefault("colorbars", {}).setdefault(identifier, {})
                for key in ("font", "label_size"):
                    if key in changes:
                        settings[key] = changes.pop(key)
                bar_position = settings.get("position", self.current_colorbar_position(identifier))
                if "x" in changes or "y" in changes:
                    settings["position"] = [changes.pop("x", bar_position[0]), changes.pop("y", bar_position[1])]
                direct = {key: changes.pop(key) for key in ("colorbar_visible", "colorbar_label_raw") if key in changes}
                image = replace(image, **direct, colorbar_style=replace(image.colorbar_style, **changes))
            graph = replace(graph, images=tuple(image if i.wave == identifier else i for i in graph.images))
        elif kind == "annotation":
            annotation = next((a for a in graph.annotations if a.name == identifier), None)
            style = annotation.style if annotation else graph.legend_style
            raw = changes.pop("raw", annotation.raw if annotation else graph.legend_raw)
            settings = layout.setdefault("annotations", {}).setdefault(identifier, {})
            for key in ("visible", "font", "rotation"):
                if key in changes:
                    settings[key] = changes.pop(key)
            if annotation is None and "visible" in settings:
                layout.setdefault("view", {})["legend"] = settings["visible"]
            x, y, _ = _annotation_anchor(style)
            if "x" in changes or "y" in changes:
                x, y = changes.pop("x", x), changes.pop("y", y)
                # Keep the original alignment/anchor; store offsets explicitly.
                offset_x, offset_y = annotation_offsets(style, x, y)
                changes.update(x_percent=offset_x, y_percent=offset_y)
            if "color" in changes:
                from matplotlib.colors import to_rgb
                changes["color"] = tuple(round(v * 65535) for v in to_rgb(changes["color"]))
            raw = explicit_font(raw, changes.get("font_size"), settings.get("font") if "font" in settings else None)
            if "font_size" in changes:
                changes["inherit_graph_font"] = changes["font_size"] == 0
            if "padding" in changes:
                changes["padding_explicit"] = True
            style = replace(style, **changes)
            if annotation:
                updated = replace(annotation, raw=raw, style=style)
                graph = replace(graph, annotations=tuple(updated if a.name == identifier else a for a in graph.annotations))
            else:
                graph = replace(graph, legend_raw=raw, legend_style=style)
        elif kind == "geometry":
            if "font_size" in changes:
                changes["relative_font_size"] = None
            elif "relative_font_size" in changes:
                changes["font_size"] = None
            relative_size = changes.get("relative_font_size")
            if relative_size is not None and not 0 <= relative_size <= 100:
                raise ValueError("Relative font size must be 0..100%")
            if "font_multiplier" in changes and not 25 <= changes["font_multiplier"] <= 400:
                raise ValueError("Font multiplier must be 25..400%")
            if changes.get("width_aspect") is not None:
                changes["height_aspect"] = None
            elif changes.get("height_aspect") is not None:
                changes["width_aspect"] = None
            margins = dict(graph.style.margins)
            for key in list(changes):
                if key.startswith("margin_"):
                    value = changes.pop(key)
                    if value is None:
                        margins.pop(key[7:], None)
                    else:
                        margins[key[7:]] = value
            graph = replace(graph, style=replace(graph.style, margins=tuple(margins.items()), **changes))
        if kind == "trace":
            from materials_data_studio.tools.command_services import replace_graph_state
            from materials_data_studio.tools.graph_waves import validate_references
            validate_references(self.editor.document, replace_graph_state(self.editor.document, graph))
        self.editor.edit_graph(graph, f"Edit {kind}: {identifier}", layout)

    def set_mode(self, mode: str) -> None:
        if self._bezier_drag:
            self._bezier_drag = None
            graph = self.graph()
            if graph is not None:
                self._draw_graph(graph)
        if self._insert_drag and mode != self.mode:
            self._insert_drag = None
            self.drag_paint_timer.stop()
            self.canvas.draw_idle()
        if self.navigation.mode:
            (self.navigation.pan if str(self.navigation.mode) == "pan/zoom" else self.navigation.zoom)()
        self.mode = mode
        for name, action in self.drawing_actions.items():
            action.setChecked(name == mode)
        for name, action in self.mode_actions.items():
            action.setChecked(name == mode)
        if mode == "Pan":
            self.navigation.pan()
        elif mode == "Zoom":
            self.navigation.zoom()
        self.drawing_selection_overlay.update()

    def select_target(self, kind: str, identifier: str) -> None:
        self._selected_object = kind, identifier
        self.drawing_selected = int(identifier) if kind == "drawing" else None
        self.drawing_selection_overlay.update()
        if kind != "drawing":
            self.drawing_selected = None
        kind = "axis" if kind in {"axis", "label"} else "annotation" if kind == "legend" else kind
        self._property_target = kind, identifier
        record = self.property_widgets.get(kind)
        if record:
            selector = record["selector"]
            selector.setCurrentText(identifier)
            self.properties.setCurrentWidget(selector.parentWidget().parentWidget().parentWidget())

    def pointer_press(self, event: Any) -> None:
        if not self.editor or self.graph() is None:
            return
        self.canvas.setFocus()
        if event.button == 3:
            if self._insert_drag and self._insert_drag["mode"] == "Polygon":
                from materials_data_studio.tools.graph_insertion import finish
                finish(self)
                return
            if self._drag:
                self.pointer_release(event)
            self.context_menu = graph_context_menu(self, event)
            self.context_menu.popup(QCursor.pos())
            return
        if event.button != 1:
            return
        self.clear_region()
        if self.mode in KINDS:
            if self.mode == "Bezier" and not self._insert_drag:
                from materials_data_studio.tools.graph_handles import begin_edit
                if begin_edit(self, event):
                    return
            if event.inaxes and not str(event.inaxes.get_gid() or "").startswith("colorbar:"):
                transform = drawing_transform(event.inaxes, self.drawing_defaults)
                point = transform.inverted().transform((event.x, event.y))
                if self.drawing_snap and not modifier(event, "alt") and self.drawing_defaults.xcoord == self.drawing_defaults.ycoord == "prel":
                    point = [round(v * 20) / 20 for v in point]
                if self.mode == "Text":
                    edit_drawing(self, new=replace(self.drawing_defaults, kind="DrawText", coordinates=tuple(float(v) for v in point)))
                else:
                    from materials_data_studio.tools.graph_insertion import begin
                    begin(self, event)
            return
        if self.mode != "Pointer":
            return
        renderer = self.canvas.get_renderer()
        # Labels and annotations take precedence over underlying axes/images.
        priority = {"drawing": 0, "label": 0, "annotation": 1, "legend": 1, "colorbar": 2, "trace": 3, "axis": 4, "image": 5}
        for artist, target in sorted(self.artist_targets.items(), key=lambda item: priority.get(item[1][0], 9)):
            artist: Any = artist
            if not artist.get_visible():
                continue
            kind, identifier = target
            try:
                hit = artist.get_window_extent(renderer).contains(event.x, event.y) if kind in {"label", "annotation", "legend", "colorbar"} else artist.contains(event)[0]
            except (TypeError, AttributeError):
                hit = False
            if not hit:
                continue
            self.select_target(kind, identifier)
            if kind == "trace" and modifier(event, "shift") and not event.dblclick:
                graph = self.graph()
                assert graph is not None
                trace = next(t for t in graph.traces if t.name == identifier)
                screen_component = 0 if modifier(event, "alt") else 1
                # Swap X/Y changes the screen direction, not the stored wave axis.
                component = 1 - screen_component if graph.swap_xy else screen_component
                ax = artist.axes
                self._drag = {"before": self.editor.document.current.clone(), "target": target,
                              "ax": ax, "start": (event.x, event.y), "anchor": (event.x, event.y),
                              "point": (event.x, event.y), "artist": artist, "text": artist,
                              "component": component, "screen_component": screen_component,
                              "transform": ax.transData.frozen(),
                              "data": (np.asarray(artist.get_xdata()).copy(), np.asarray(artist.get_ydata()).copy()),
                              "offset": trace.offset}
                artist.set_visible(False)
                self.canvas.draw()
                self.canvas._draw_pending = False
                artist.set_visible(True)
                self._drag["background"] = self.canvas.copy_from_bbox(self.figure.bbox)
                return
            if kind == "drawing":
                self.drawing_selected = int(identifier)
                if event.dblclick:
                    edit_drawing(self, int(identifier))
                    return
            if event.dblclick and kind in {"annotation", "legend", "colorbar"}:
                self.edit_annotation(kind, identifier)
                return
            if event.dblclick and kind == "label":
                self.edit_text(kind, identifier)
                return
            if kind in {"label", "annotation", "legend", "colorbar", "drawing"}:
                if kind in {"annotation", "legend"} and not self.graph_layout().get("annotations", {}).get(identifier, {}).get("movable", True):
                    return
                text = artist.text if isinstance(artist, _AnnotationFrame) else artist
                if kind == "legend":
                    box = cast(Any, artist).get_bbox_to_anchor()
                    anchor = box.x0, box.y0
                elif kind == "colorbar":
                    box = cast(Any, artist).get_position()
                    anchor = self.figure.transFigure.transform((box.x0, box.y0))
                elif kind == "drawing":
                    anchor = text.get_position()
                else:
                    anchor = text.get_transform().transform(text.get_position())
                ax: Any = artist.axes if kind != "colorbar" else self.figure.axes[0]
                self._drag = {"before": self.editor.document.current.clone(), "target": target,
                              "ax": ax, "start": (event.x, event.y), "anchor": anchor,
                              "artist": artist, "text": text, "point": anchor,
                              "horizontal": kind == "label" and artist is ax.xaxis.label,
                              "box": cast(Any, artist).get_position().frozen() if kind == "colorbar" else None}
                # Render the static scene once. Subsequent frames blit only the
                # moved object; no wave copies, figure.clear or axes rebuilding.
                artist.set_visible(False)
                self.canvas.draw()
                # Freeze the initial coordinate system: never invert an artist's
                # moving transform or mix data units with pixel translations.
                self._drag["transform"] = text.get_transform().frozen()
                # A previously queued idle draw must not repaint all traces
                # while the drag's static background is being reused.
                self.canvas._draw_pending = False
                artist.set_visible(True)
                self._drag["background"] = self.canvas.copy_from_bbox(self.figure.bbox)
                self.figure.draw_artist(artist)
                self.canvas.blit(self.figure.bbox)
                return
            if kind not in {"trace", "image"}:
                return
            break
        if event.inaxes is not None and not str(event.inaxes.get_gid() or "").startswith("colorbar:"):
            self._selected_object = None
            self.drawing_selected = None
            self.drawing_selection_overlay.update()
            self._region_drag = {"start": (event.x, event.y), "ax": event.inaxes}

    def pointer_move(self, event: Any) -> None:
        if self._bezier_drag and event.x is not None and event.y is not None:
            from materials_data_studio.tools.graph_handles import move_edit
            move_edit(self, event)
            return
        if self._insert_drag and event.x is not None and event.y is not None:
            from materials_data_studio.tools.graph_insertion import update
            update(self, event)
            return
        if self._region_drag and event.x is not None and event.y is not None:
            self.region = {**self._region_drag, "end": (event.x, event.y)}
            self._show_region()
            return
        if not self._drag or not self.editor or event.x is None or event.y is None:
            return
        drag = self._drag
        kind, _identifier = drag["target"]
        if kind == "trace":
            screen = drag["screen_component"]
            start = drag["transform"].inverted().transform(drag["start"])
            end = drag["transform"].inverted().transform((event.x, event.y))
            delta = float(end[screen] - start[screen])
            if not np.isfinite(delta):
                return
            data = [drag["data"][0], drag["data"][1]]
            data[screen] = data[screen] + delta
            drag["artist"].set_data(*data)
            drag["delta"] = delta
            drag["point"] = event.x, event.y
            if not self.drag_paint_timer.isActive():
                self.drag_paint_timer.start()
            return
        dx, dy = event.x - drag["start"][0], event.y - drag["start"][1]
        shift = "shift" in str(getattr(event, "key", "")).lower() or bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)
        if shift:
            if "lock" not in drag and max(abs(dx), abs(dy)) > 2:
                drag["lock"] = "x" if abs(dx) >= abs(dy) else "y"
            if drag.get("lock") == "x":
                dy = 0
            elif drag.get("lock") == "y":
                dx = 0
        else:
            drag.pop("lock", None)
        x = drag["anchor"][0] + dx
        y = drag["anchor"][1] + dy
        if kind == "drawing" and self.drawing_snap and not modifier(event, "alt"):
            dx, dy = round(dx / 10) * 10, round(dy / 10) * 10
            x, y = drag["anchor"][0] + dx, drag["anchor"][1] + dy
        drag["point"] = x, y
        artist, text = drag["artist"], drag["text"]
        if kind == "colorbar":
            box = drag["box"]
            dx, dy = (x - drag["anchor"][0]) / self.figure.bbox.width, (y - drag["anchor"][1]) / self.figure.bbox.height
            artist.set_position((box.x0 + dx, box.y0 + dy, box.width, box.height))
        elif kind == "legend":
            artist.set_bbox_to_anchor(drag["ax"].transAxes.inverted().transform((x, y)))
        elif kind == "drawing":
            text.set_position((x, y))
        else:
            text.set_position(drag["transform"].inverted().transform((x, y)))
        if not self.drag_paint_timer.isActive():
            self.drag_paint_timer.start()
        self.drawing_selection_overlay.update()

    def _paint_drag(self) -> None:
        if self._bezier_drag:
            from materials_data_studio.tools.graph_handles import paint_edit
            paint_edit(self)
            return
        if self._insert_drag:
            from materials_data_studio.tools.graph_insertion import paint
            paint(self)
            return
        if self._drag:
            self.canvas.restore_region(self._drag["background"])
            self.figure.draw_artist(self._drag["artist"])
            self.canvas.blit(self.figure.bbox)

    def pointer_release(self, _event: Any) -> None:
        if self._bezier_drag:
            from materials_data_studio.tools.graph_handles import finish_edit
            finish_edit(self)
            return
        if self._insert_drag:
            from materials_data_studio.tools.graph_insertion import finish
            finish(self, _event)
            return
        if self._region_drag:
            self._region_drag = None
            if self.region and (abs(self.region["start"][0] - self.region["end"][0]) < 5 or abs(self.region["start"][1] - self.region["end"][1]) < 5):
                self.clear_region()
        if not self._drag or not self.editor:
            return
        self.drag_paint_timer.stop()
        drag, self._drag = self._drag, None
        if tuple(drag["point"]) == tuple(drag["anchor"]):
            self.canvas.draw_idle()
            return
        kind, identifier = drag["target"]
        x, y = drag["point"]
        before = drag["before"]
        graph = self.graph()
        assert graph is not None
        layout = self.graph_layout()
        ax = drag["ax"]
        if kind == "trace":
            component = drag["component"]
            offset = list(drag["offset"])
            offset[component] += drag.get("delta", 0.)
            graph = replace(graph, traces=tuple(replace(t, offset=tuple(offset)) if t.name == identifier else t for t in graph.traces))
        elif kind == "drawing":
            index = int(identifier)
            drawing = moved_drawing(ax, graph.drawings[index], (x - drag["anchor"][0], y - drag["anchor"][1]))
            graph = replace(graph, drawings=tuple(drawing if i == index else d for i, d in enumerate(graph.drawings)))
        elif kind == "label":
            position = label_position(ax, drag["horizontal"], x, y, self.figure.dpi,
                                      _visual_scale(self.figure, graph), identifier)
            graph = self.store_label_position(graph, layout, identifier, position)
            graph = self.reserve_label_margin(graph, layout, identifier)
        else:
            u, v = ax.transAxes.inverted().transform((x, y))
            if kind == "colorbar":
                layout.setdefault("colorbars", {}).setdefault(identifier, {})["position"] = [float(u), float(v)]
            else:
                annotation = next((a for a in graph.annotations if a.name == identifier), None)
                style = annotation.style if annotation else graph.legend_style
                offset_x, offset_y = annotation_offsets(style, float(u), float(v))
                style = replace(style, x_percent=offset_x, y_percent=offset_y)
                graph = replace(graph, annotations=tuple(replace(a, style=style) if a.name == identifier else a for a in graph.annotations)) if annotation else replace(graph, legend_style=style)
        after = before.clone()
        after.graphs = tuple(graph if item.name == graph.name else item for item in after.graphs)
        after.layout[graph.name] = layout
        self.editor.commit(before, after, f"Move {kind}")
        self.select_target(kind, identifier)
        if kind == "drawing":
            self.drawing_selected = int(identifier)

    def clear_region(self) -> None:
        self.region = None
        self._region_drag = None
        if hasattr(self, "rubber_band"):
            self.rubber_band.hide()

    def _show_region(self) -> None:
        if not self.region:
            return
        ratio = self.canvas.device_pixel_ratio
        def point(coordinates: tuple[float, float]) -> QPoint:
            return QPoint(round(coordinates[0] / ratio), round((self.figure.bbox.height - coordinates[1]) / ratio))
        self.rubber_band.setGeometry(QRect(point(self.region["start"]), point(self.region["end"])).normalized())
        self.rubber_band.show()

    def zoom_region(self, direction: str, dimensions: str) -> None:
        zoom_region(self, direction, dimensions)

    def current_label_position(self, identifier: str) -> dict[str, Any]:
        self.canvas.draw()
        graph = self.graph()
        assert graph is not None
        artist = next((a for a, target in self.artist_targets.items() if target == ("label", identifier)), None)
        if artist is None:
            return {"coordinate_system": "axis_label", "offset_points": 34, "lateral": 0.5}
        x, y = artist.get_transform().transform(artist.get_position())
        return label_position(artist.axes, artist is artist.axes.xaxis.label, x, y, self.figure.dpi,
                              _visual_scale(self.figure, graph), identifier)

    def current_colorbar_position(self, identifier: str) -> list[float]:
        artist = next((a for a, target in self.artist_targets.items() if target == ("colorbar", identifier)), None)
        if artist is None:
            return [1.05, 0.1]
        box = artist.get_position()
        x, y = self.figure.transFigure.transform((box.x0, box.y0))
        return [float(v) for v in self.figure.axes[0].transAxes.inverted().transform((x, y))]

    def store_label_position(self, graph: PxpGraph, layout: dict[str, Any], identifier: str, position: dict[str, Any]) -> PxpGraph:
        from materials_data_studio.tools.graph_geometry import set_label_position
        artist = next((a for a, target in self.artist_targets.items() if target == ("label", identifier)), None)
        if artist is None:
            raise ValueError("Render the axis before positioning its label")
        horizontal = artist is artist.axes.xaxis.label
        extent = artist.axes.bbox.width if horizontal else artist.axes.bbox.height
        extent *= 72 / self.figure.dpi / _visual_scale(self.figure, graph)
        layout.get("labels", {}).pop(identifier, None)
        return set_label_position(graph, identifier, position, extent)

    @staticmethod
    def reserve_label_margin(graph: PxpGraph, layout: dict[str, Any], identifier: str) -> PxpGraph:
        """Moved labels reserve explicit saved margins, never auto-layout."""
        position = layout.get("labels", {}).get(identifier)
        if not position:
            return graph
        style = _style(graph, identifier)
        axis = next((a for a in graph.axes if a.name == identifier), None)
        side = axis.side if axis and axis.side else "bottom" if identifier == "bottom" else "top" if identifier == "top" else "right" if identifier == "right" else "left"
        if graph.swap_xy:
            side = {"bottom": "left", "left": "bottom", "top": "right", "right": "top"}[side]
        needed = max(0, position["offset_points"]) + (style.font_size or 16) * 1.8
        if needed <= _graph_margins(graph)[side]:
            return graph
        stored_side = {"bottom": "left", "left": "bottom", "top": "right", "right": "top"}[side] if graph.swap_xy else side
        margins = dict(graph.style.margins)
        margins[stored_side] = needed
        return replace(graph, style=replace(graph.style, margins=tuple(margins.items())))

    def edit_text(self, kind: str, identifier: str) -> None:
        graph = self.graph()
        if graph is None:
            return
        if kind == "label":
            raw = next(a.label_raw for a in graph.axes if a.name == identifier)
        else:
            raw = next((a.raw for a in graph.annotations if a.name == identifier), graph.legend_raw)
        value, accepted = QInputDialog.getMultiLineText(self, "Edit Graph Text", "Text (Igor formatting supported)", raw)
        if accepted:
            self.apply_properties("axis" if kind == "label" else "annotation", identifier,
                                  {"label" if kind == "label" else "raw": value})

    def delete_selected(self) -> None:
        if self._drag or self._insert_drag or self._selected_object is None:
            return
        graph = self.graph()
        if graph is None or self.editor is None:
            return
        kind, identifier = self._selected_object
        if kind == "drawing":
            drawing_operation(self, "delete")
        elif kind == "label":
            self.editor.edit_graph(replace(graph, axes=tuple(replace(a, label_raw="") if a.name == identifier else a for a in graph.axes)), "Delete axis label")
        elif kind in {"annotation", "legend"}:
            updated = replace(graph, annotations=tuple(a for a in graph.annotations if a.name != identifier), legend_raw="")
            self.editor.edit_graph(updated, "Delete annotation")
        self._selected_object = None
        self._property_target = "axis", "bottom"

    def add_text(self, event: Any = None) -> None:
        graph = self.graph()
        if graph is None or self.editor is None:
            return
        value, accepted = QInputDialog.getMultiLineText(self, "Add Annotation", "Text")
        if not accepted or not value:
            return
        position = event.inaxes.transAxes.inverted().transform((event.x, event.y)) if hasattr(event, "inaxes") else (0.5, 0.5)
        count = 1
        while any(a.name == f"MDS Text {count}" for a in graph.annotations):
            count += 1
        annotation = PxpAnnotation(f"MDS Text {count}", value,
                                   PxpAnnotationStyle(anchor="LB", x_percent=float(position[0]) * 100,
                                                      y_percent=float(position[1]) * 100, frame=0, transparent=True))
        self._property_target = "annotation", annotation.name
        self.editor.edit_graph(replace(graph, annotations=(*graph.annotations, annotation)), "Add annotation")

    def reset_original(self) -> None:
        graph = self.graph()
        if graph is None or self.editor is None:
            return
        if QMessageBox.question(self, "Reset to Igor Original", "Reset this graph's edits to the imported Igor state?") != QMessageBox.StandardButton.Yes:
            return
        original = next((g for g in self.editor.document.source.graphs if g.name == graph.name), None)
        if original is None:
            QMessageBox.information(self, "Reset to Igor Original", "This graph was created in MDS and has no imported Igor original.")
            return
        self.editor.edit_graph(original, "Reset to Igor Original", {})

    def export_graph(self) -> None:
        graph = self.graph()
        if graph is None or self.editor is None:
            return
        dialog = SaveGraphicsDialog(self, self.editor.document, graph.name)
        dialog.exec()
        dialog.deleteLater()

    def choose_file(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Open Igor Experiment", "", "Igor / MDS Experiments (*.pxp *.h5xp *.mdsg)")
        if filename:
            self.open_path(Path(filename))

    def export_data(self) -> None:
        graph = self.graph()
        if graph is not None and self.editor is not None:
            dialog = GraphDataDialog(self, self.editor.document, graph.name)
            dialog.exec()
            dialog.deleteLater()

    def edit_annotation(self, kind: str, identifier: str) -> None:
        edit_annotation(self, kind, identifier)

    def quick_fit(self, trace_name: str = "", model: str = "line") -> None:
        run_quick_fit(self, trace_name, model)

    def dragEnterEvent(self, event: Any) -> None:
        urls = event.mimeData().urls()
        if urls and all(u.isLocalFile() and Path(u.toLocalFile()).suffix.lower() in {".dat", ".csv", ".tsv", ".txt", ".xy", ".mdsg", ".h5xp", ".pxp", ".ipf"} for u in urls):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: Any) -> None:
        # Revalidate here as child viewports and direct drops can bypass dragEnter.
        self.dragEnterEvent(event)
        if not event.isAccepted():
            return
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls()]
        event.acceptProposedAction()
        self.file_drop_filter.enqueue(paths)

    def _import_dropped_paths(self, paths: list[Path]) -> None:
        """Run serially only after the native drag/drop event has returned."""
        procedures = []
        for path in paths:
            if self.file_drop_filter.closed:
                return
            if path.suffix.lower() == ".ipf":
                procedures.append(path)
            elif path.suffix.lower() in {".mdsg", ".h5xp"}:
                self.open_graph_path(path)
            elif path.suffix.lower() == ".pxp":
                self.open_path(path)
            else:
                import_text(self, path)
            if not isValid(self):
                return
        if procedures:
            # One source-only preview for all IPFs; never parse them as columns.
            self.command_workspace.convert_ipf_paths(procedures)

    def autosave(self) -> None:
        if self.editor is None or not self.editor.dirty or self.recovery_path is None:
            return
        if getattr(self, "_autosave_running", False):
            return
        path = self.recovery_path
        document = replace(self.editor.document, current=self.editor.document.current.clone())
        stop = Event()
        self._autosave_stop = stop
        self._autosave_running = True
        def save() -> None:
            if not stop.is_set():
                path.parent.mkdir(parents=True, exist_ok=True)
                save_graph_document(path, document)
            if stop.is_set():
                path.unlink(missing_ok=True)
        task = run_task(self, "Save graph recovery copy", save, lambda _result: None, cancelled=stop,
                        on_error=lambda message: self.statusBar().showMessage(f"Graph recovery save failed: {message}", 15000))
        task.dialog.setMinimumDuration(2_000_000_000)
        task.finished.connect(lambda: setattr(self, "_autosave_running", False))

    def recover_document(self) -> None:
        from materials_data_studio.ui.recovery_center import RecoveryCenter
        dialog = RecoveryCenter(self)
        dialog.exec()
        dialog.deleteLater()

    def _remove_recovery(self) -> None:
        stop = getattr(self, "_autosave_stop", None)
        if stop is not None:
            stop.set()
        path = getattr(self, "recovery_path", None)
        if path is not None:
            with suppress(OSError):
                path.unlink(missing_ok=True)

    def event(self, event: QEvent) -> bool:
        if event.type() in {QEvent.Type.StyleChange, QEvent.Type.PaletteChange} and hasattr(self, "quick_toolbar"):
            self.update_icons()
        if event.type() == QEvent.Type.WindowActivate:
            self.activated.emit()
        return super().event(event)

    def confirm_close(self) -> bool:
        self.command_workspace.flush()
        if (any(self._property_record_dirty(record) for record in self.property_widgets.values())
            and QMessageBox.question(self, "Unapplied property edits", "Discard unapplied property form edits before closing or replacing the document? Choose No to return and apply them first.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes):
            return False
        if self.editor is None or not self.editor.dirty:
            return True
        response = QMessageBox.question(self, "Unsaved Graph Document", "Save this graph document before closing/replacing it?",
                                       QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        return response == QMessageBox.StandardButton.Discard or (
            response == QMessageBox.StandardButton.Save
            and self.save_document()
            and not self.editor.dirty
        )

    def confirm_update_shutdown(self) -> bool:
        return self.confirm_close()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.command_workspace.flush()
        if self.command_workspace.is_running():
            if QMessageBox.question(self, "Stop Python procedure?", "Stop the running procedure and close this window? Document changes from the run will be discarded.") != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.command_workspace.stop()
            if self.command_workspace.process is not None:
                self.command_workspace.process.waitForFinished(1500)
        if self._discard_approved or self.confirm_close():
            from materials_data_studio.ui.workspace_layout import save_graph_panels
            save_graph_panels(self)
            self.file_drop_filter.cancel()
            self._remove_recovery()
            event.accept()
        else:
            event.ignore()
