"""Read-only Igor PXP explorer with editable MDS-only view settings."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from materials_data_studio.tools.pxp_model import PxpDocument, PxpGraph
from materials_data_studio.tools.pxp_recreation import formatted_text
from materials_data_studio.tools.pxp_render import render_graph, render_wave
from materials_data_studio.tools.pxp_style import COLORMAPS, igor_rgb, marker
from materials_data_studio.tools.windows import ToolWindow
from materials_data_studio.ui.workspace_layout import (
    WorkspaceSplitter,
    install_workspace,
    populate_view_menu,
)

if TYPE_CHECKING:
    from materials_data_studio.tools.pxp_backend import PxpBackend


class _LazyPxpBackend:
    """Graph-document opening must not depend on loading the source parser."""

    def open(self, path: Path) -> PxpDocument:
        from materials_data_studio.tools.pxp_backend import Igor2Backend

        return Igor2Backend().open(path)


class PxpViewerWindow(ToolWindow):
    """A top-level tool; the owning workspace holds its session and undo history."""

    def __init__(self, parent: QWidget | None = None, *, owner: QMainWindow | None = None,
                 backend: PxpBackend | None = None) -> None:
        super().__init__("Igor PXP Viewer", parent)
        self.owner = owner
        self.backend = backend or _LazyPxpBackend()
        self.document: PxpDocument | None = None
        self._selection: tuple[str, str] = ("", "")
        self._building = False
        self.resize(1350, 810)
        central = QWidget()
        outer = QVBoxLayout(central)
        top = QHBoxLayout()
        self.open_button = QPushButton("Open Igor Experiment (.pxp)…")
        self.source_label = QLabel("PXP source · Read-only")
        self.source_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        top.addWidget(self.open_button)
        top.addWidget(self.source_label, 1)
        outer.addLayout(top)
        splitter = self.splitter = WorkspaceSplitter()
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Experiment"])
        splitter.addWidget(self.tree)
        center = QWidget()
        center_layout = QVBoxLayout(center)
        self.preview_stack = QStackedWidget()
        chart_page = QWidget()
        chart_layout = QVBoxLayout(chart_page)
        self.figure = Figure(figsize=(8, 5), dpi=100)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.mpl_connect("resize_event", self._canvas_resized)
        chart_layout.addWidget(self.canvas, 1)
        self.wave_table = QTableWidget()
        self.wave_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.wave_table.setVisible(False)
        chart_layout.addWidget(self.wave_table)
        self.preview_stack.addWidget(chart_page)
        self.text_preview = QPlainTextEdit()
        self.text_preview.setReadOnly(True)
        self.preview_stack.addWidget(self.text_preview)
        center_layout.addWidget(self.preview_stack, 1)
        self.compatibility = QLabel("Open a PXP to inspect its waves and saved windows.")
        self.compatibility.setWordWrap(True)
        center_layout.addWidget(self.compatibility)
        splitter.addWidget(center)
        self.options = QWidget()
        self.option_layout = QVBoxLayout(self.options)
        self.option_layout.addWidget(QLabel("MDS view settings"))
        self.option_form = QFormLayout()
        self.option_layout.addLayout(self.option_form)
        self.option_layout.addStretch(1)
        splitter.addWidget(self.options)
        # Extra width belongs to the plot, not the navigation/properties panes.
        for index, stretch in enumerate((0, 1, 0)):
            splitter.setStretchFactor(index, stretch)
        splitter.setSizes([250, 820, 280])
        install_workspace(outer, splitter, "graph_editor")
        self.setCentralWidget(central)
        self.view_menu = self.menuBar().addMenu("View")
        populate_view_menu(self.view_menu, splitter)
        self.open_button.clicked.connect(self.choose_file)
        self.tree.currentItemChanged.connect(self._selected)

    @property
    def view_state(self) -> dict[str, Any]:
        if self.owner is None or self.document is None:
            return {}
        state = self.owner.state  # type: ignore[attr-defined]
        return state.pxp_view_states.setdefault(self.document.source_id, {})

    def choose_file(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Open Igor Experiment", "",
                                                  "Igor Packed Experiment (*.pxp)")
        if filename:
            self.open_path(Path(filename))

    def open_path(self, path: Path) -> None:
        try:
            document = self.backend.open(path)
        except Exception as exc:
            QMessageBox.warning(self, "PXP Open Error", f"The PXP parser failed: {exc}")
            return
        if document.status == "OPEN_FAILED":
            message = "; ".join(issue.message for issue in document.issues)
            QMessageBox.warning(self, "PXP Open Error", message or "No readable records found")
            return
        if self.owner is not None:
            history = self.owner.undo_history  # type: ignore[attr-defined]
            history.checkpoint()
            state = self.owner.state  # type: ignore[attr-defined]
            state.pxp_documents[document.source_id] = document
            state.pxp_view_states[document.source_id] = {}
            history.checkpoint()
        self.bind_document(document)

    def bind_document(self, document: PxpDocument) -> None:
        self.document = document
        self.setWindowTitle(f"Igor PXP Viewer — {document.basename}")
        self.source_label.setText(f"PXP source · Read-only    {document.basename}    {document.status}")
        self.tree.blockSignals(True)
        self.tree.clear()
        root = QTreeWidgetItem([document.basename])
        self.tree.addTopLevelItem(root)
        folders_parent = QTreeWidgetItem(root, ["Data Folders"])
        folders: dict[str, QTreeWidgetItem] = {"root": QTreeWidgetItem(folders_parent, ["root"])}
        for path in document.folders:
            if path == "root":
                continue
            parent, _, name = path.rpartition(":")
            folders[path] = QTreeWidgetItem(folders.get(parent, folders["root"]), [name])
        for wave in document.waves:
            parent_path = wave.igor_path.rpartition(":")[0]
            node = QTreeWidgetItem(folders.get(parent_path, folders["root"]),
                                   [f"{wave.name}  {wave.shape}"])
            node.setData(0, Qt.ItemDataRole.UserRole, ("wave", wave.id))
        graphs = QTreeWidgetItem(root, ["Graphs"])
        for graph in document.graphs:
            from materials_data_studio.tools.graph_fidelity import assess_graph
            report = assess_graph(graph,document)
            node = QTreeWidgetItem(graphs, [f"{graph.name}  [{'Partial fidelity' if report.diagnostics else 'Supported subset'}]"])
            node.setToolTip(0,report.tooltip)
            node.setData(0, Qt.ItemDataRole.UserRole, ("graph", graph.name))
        tables = QTreeWidgetItem(root, ["Tables"])
        for table in document.tables:
            node = QTreeWidgetItem(tables, [table])
            node.setData(0, Qt.ItemDataRole.UserRole, ("table", table))
        procedures = QTreeWidgetItem(root, ["Procedures"])
        for index, _text in enumerate(document.procedures):
            node = QTreeWidgetItem(procedures, [f"Procedure {index + 1}"])
            node.setData(0, Qt.ItemDataRole.UserRole, ("procedure", str(index)))
        self.tree.expandToDepth(2)
        self.tree.blockSignals(False)
        selected = self.view_state.get("selected")
        first = None
        for index in range(self.tree.topLevelItemCount()):
            iterator = [self.tree.topLevelItem(index)]
            while iterator:
                item = iterator.pop()
                if item is None:
                    continue
                if selected and item.data(0, Qt.ItemDataRole.UserRole) == tuple(selected):
                    first = item
                    break
                iterator.extend(item.child(i) for i in range(item.childCount()))
        if first is None:
            first = graphs.child(0) if graphs.childCount() else folders["root"].child(0)
        if first is not None:
            self.tree.setCurrentItem(first)

    def refresh_from_state(self) -> None:
        """Refresh editable settings after global Undo/Redo without reparsing."""
        if self.document is None or self.owner is None:
            return
        document = self.owner.state.pxp_documents.get(self.document.source_id)  # type: ignore[attr-defined]
        if document is None:
            self.close()
            return
        self.document = document
        self._show_selection()

    def _selected(self, item: QTreeWidgetItem | None, _old: QTreeWidgetItem | None) -> None:
        if item is None:
            return
        selection = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(selection, tuple):
            return
        self._selection = selection
        self.view_state["selected"] = selection
        self._show_selection()

    def _clear_options(self) -> None:
        while self.option_form.rowCount():
            self.option_form.removeRow(0)

    def _show_selection(self) -> None:
        if self.document is None:
            return
        self._building = True
        self._clear_options()
        kind, identifier = self._selection
        if kind == "wave":
            wave = next((wave for wave in self.document.waves if wave.id == identifier), None)
            if wave is not None:
                render_wave(self.figure, wave)
                self.canvas.draw_idle()
                self.preview_stack.setCurrentIndex(0)
                self.compatibility.setText(f"{wave.igor_path} · {wave.dtype} · {wave.shape} · read-only data")
                self._fill_wave_table(wave)
        elif kind == "graph":
            graph = next((graph for graph in self.document.graphs if graph.name == identifier), None)
            if graph is not None:
                self.wave_table.setVisible(False)
                self.preview_stack.setCurrentIndex(0)
                self._graph_controls(graph)
                self._draw_graph(graph)
                from materials_data_studio.tools.graph_fidelity import assess_graph
                report = assess_graph(graph,self.document)
                self.compatibility.setText(f"{graph.name} · {report.summary}")
                self.compatibility.setToolTip(report.tooltip)
        elif kind == "procedure":
            self.text_preview.setPlainText(self.document.procedures[int(identifier)])
            self.preview_stack.setCurrentIndex(1)
            self.compatibility.setText("Procedure source text · Read-only · Never executed")
        elif kind == "table":
            self.text_preview.setPlainText(f"{identifier}\n\nIgor table layout preview is not supported. All underlying waves remain available in Data Folders.")
            self.preview_stack.setCurrentIndex(1)
            self.compatibility.setText("Table recognized · layout not reconstructed")
        self._building = False

    def _fill_wave_table(self, wave: Any) -> None:
        if wave.data.ndim != 1:
            self.wave_table.setVisible(False)
            return
        count = min(len(wave.data), 1000)
        self.wave_table.setRowCount(count)
        self.wave_table.setColumnCount(2)
        self.wave_table.setHorizontalHeaderLabels(["Coordinate", "Value"])
        coords = wave.coordinate(0)
        for index in range(count):
            self.wave_table.setItem(index, 0, QTableWidgetItem(str(coords[index])))
            self.wave_table.setItem(index, 1, QTableWidgetItem(str(wave.data[index])))
        self.wave_table.setMaximumHeight(180)
        self.wave_table.setVisible(True)

    def _graph_view(self, graph: PxpGraph) -> dict[str, Any]:
        return self.view_state.get("graphs", {}).get(graph.name, {})

    def _change(self, graph: PxpGraph, section: str, key: str, field: str, value: Any) -> None:
        if self._building:
            return
        if self.owner is not None:
            self.owner.undo_history.checkpoint()  # type: ignore[attr-defined]
        view = self.view_state.setdefault("graphs", {}).setdefault(graph.name, {})
        if section:
            view.setdefault(section, {}).setdefault(key, {})[field] = value
        else:
            view[field] = value
        if self.owner is not None:
            self.owner.undo_history.checkpoint()  # type: ignore[attr-defined]
        self._draw_graph(graph)

    def _draw_graph(self, graph: PxpGraph) -> None:
        if self.document is not None:
            render_graph(self.figure, self.document, graph, self._graph_view(graph))
            self.canvas.draw_idle()

    def _canvas_resized(self, _event: Any) -> None:
        """Recalculate Igor plot-area geometry when the viewer changes size."""
        if self.document is None or self._selection[0] != "graph" or self._building:
            return
        graph = next((item for item in self.document.graphs if item.name == self._selection[1]), None)
        if graph is not None:
            self._draw_graph(graph)

    def _graph_controls(self, graph: PxpGraph) -> None:
        view = self._graph_view(graph)
        if graph.traces:
            selector = QListWidget()
            selector.addItems([trace.name for trace in graph.traces])
            selector.setMaximumHeight(105)
            self.option_form.addRow("Trace", selector)
            visible = QCheckBox("Visible")
            color = QPushButton("Color…")
            width = QLineEdit()
            marker_box = QComboBox()
            for label, value in [("None", ""), ("Circle", "o"), ("Square", "s"),
                                 ("Triangle", "^"), ("Cross", "x"), ("Diamond", "D")]:
                marker_box.addItem(label, value)
            size = QLineEdit()
            for label, widget in [("", visible), ("", color), ("Line width", width),
                                  ("Marker", marker_box), ("Marker size", size)]:
                self.option_form.addRow(label, widget)

            def configure(index: int) -> None:
                if index < 0:
                    return
                self._building = True
                trace = graph.traces[index]
                settings = view.get("traces", {}).get(trace.name, {})
                visible.setChecked(settings.get("visible", True))
                width.setText(str(settings.get("line_width", trace.line_width if trace.line_width is not None else 1)))
                size.setText(str(settings.get("marker_size", trace.marker_size or 3)))
                symbol = settings.get("marker", marker(trace.marker) if trace.mode in {2, 3, 4} else "")
                marker_box.setCurrentIndex(max(0, marker_box.findData(symbol)))
                current_color = settings.get("color", igor_rgb(trace.color_igor))
                swatch = current_color if isinstance(current_color, str) else QColor.fromRgbF(*current_color).name()
                color.setStyleSheet(f"background-color: {swatch};")
                self._building = False

            def trace_change(field: str, value: Any) -> None:
                index = selector.currentRow()
                if index >= 0:
                    self._change(graph, "traces", graph.traces[index].name, field, value)

            def choose_color() -> None:
                chosen = QColorDialog.getColor(parent=self)
                if chosen.isValid():
                    trace_change("color", chosen.name())
                    color.setStyleSheet(f"background-color: {chosen.name()};")

            selector.currentRowChanged.connect(configure)
            visible.toggled.connect(lambda checked: trace_change("visible", checked))
            color.clicked.connect(choose_color)
            width.editingFinished.connect(lambda: self._positive_number(width, lambda value: trace_change("line_width", value)))
            marker_box.currentIndexChanged.connect(lambda _index: trace_change("marker", marker_box.currentData()))
            size.editingFinished.connect(lambda: self._positive_number(size, lambda value: trace_change("marker_size", value)))
            selector.setCurrentRow(0)
        if graph.images:
            image = graph.images[0]
            image_view = view.get("images", {}).get(image.wave, {})
            cmap = QComboBox()
            for value in sorted({*COLORMAPS, "viridis", "plasma", "magma", "inferno", "cividis", "gray"}):
                cmap.addItem(value)
            cmap.setCurrentText(image_view.get("colormap", image.color_table or "viridis"))
            range_values = image_view.get("range", image.color_range or (None, None))
            low = QLineEdit("" if range_values[0] is None else str(range_values[0]))
            high = QLineEdit("" if range_values[1] is None else str(range_values[1]))
            low.setPlaceholderText("Auto")
            high.setPlaceholderText("Auto")
            log = QCheckBox("Log color normalization")
            log.setChecked(image_view.get("log", image.log_color))
            reverse = QCheckBox("Reverse colormap")
            reverse.setChecked(image_view.get("reverse", image.reverse))
            visible = QCheckBox("Show image")
            visible.setChecked(image_view.get("visible", not image.hidden))
            self.option_form.addRow("Colormap", cmap)
            self.option_form.addRow("Color minimum", low)
            self.option_form.addRow("Color maximum", high)
            self.option_form.addRow("", visible)
            self.option_form.addRow("", log)
            self.option_form.addRow("", reverse)
            cmap.currentTextChanged.connect(lambda value: self._change(graph, "images", image.wave, "colormap", value))
            log.toggled.connect(lambda checked: self._change(graph, "images", image.wave, "log", checked))
            reverse.toggled.connect(lambda checked: self._change(graph, "images", image.wave, "reverse", checked))
            visible.toggled.connect(lambda checked: self._change(graph, "images", image.wave, "visible", checked))
            for field in (low, high):
                field.editingFinished.connect(lambda: self._image_range(graph, image.wave, low, high))
        if graph.traces:
            legend = QCheckBox("Show legend")
            legend.setChecked(view.get("legend", bool(graph.legend_raw or graph.annotations)))
            legend.toggled.connect(lambda checked: self._change(graph, "", "", "legend", checked))
            self.option_form.addRow("", legend)
        axis_selector = QComboBox()
        for axis in graph.axes:
            axis_selector.addItem(axis.name)
        label = QLineEdit()
        minimum = QLineEdit()
        maximum = QLineEdit()
        apply_axis = QPushButton("Apply axis")
        for title, widget in [("Axis", axis_selector), ("Label", label), ("Minimum", minimum),
                              ("Maximum", maximum), ("", apply_axis)]:
            self.option_form.addRow(title, widget)

        def configure_axis(name: str) -> None:
            axis = next(item for item in graph.axes if item.name == name)
            settings = view.get("axes", {}).get(name, {})
            label.setText(settings.get("label", formatted_text(axis.label_raw)))
            limits = settings.get("limits", axis.limits)
            minimum.setText(str(limits[0]) if limits else "")
            maximum.setText(str(limits[1]) if limits else "")

        def apply() -> None:
            name = axis_selector.currentText()
            if not name:
                return
            limits = None
            if minimum.text().strip() and maximum.text().strip():
                try:
                    limits = [float(minimum.text()), float(maximum.text())]
                except ValueError:
                    return
                if limits[0] >= limits[1]:
                    return
            if self.owner is not None:
                self.owner.undo_history.checkpoint()  # type: ignore[attr-defined]
            axis_view = self.view_state.setdefault("graphs", {}).setdefault(graph.name, {}).setdefault(
                "axes", {}
            ).setdefault(name, {})
            axis = next(item for item in graph.axes if item.name == name)
            if label.text() != formatted_text(axis.label_raw):
                axis_view["label"] = label.text()
            else:
                axis_view.pop("label", None)
            if limits is not None:
                axis_view["limits"] = limits
            if self.owner is not None:
                self.owner.undo_history.checkpoint()  # type: ignore[attr-defined]
            self._draw_graph(graph)

        axis_selector.currentTextChanged.connect(configure_axis)
        apply_axis.clicked.connect(apply)
        if graph.axes:
            configure_axis(axis_selector.currentText())

    @staticmethod
    def _positive_number(field: QLineEdit, callback: Any) -> None:
        try:
            value = float(field.text())
            if value > 0:
                callback(value)
        except ValueError:
            pass

    def _image_range(self, graph: PxpGraph, wave: str, low: QLineEdit, high: QLineEdit) -> None:
        try:
            values = [float(field.text()) if field.text().strip() else None for field in (low, high)]
            if values[0] is None or values[1] is None or values[0] < values[1]:
                self._change(graph, "images", wave, "range", values)
        except ValueError:
            pass
