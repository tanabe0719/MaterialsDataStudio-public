"""Igor-style graphics saving and scientific CSV/TSV export for graph documents."""

from __future__ import annotations

import csv
import io
import math
import os
import re
import tempfile
from dataclasses import dataclass, fields, is_dataclass, replace
from pathlib import Path
from typing import Any, cast

import numpy as np
from matplotlib import colors
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from materials_data_studio.tools.graph_document import EditableGraphDocument
from materials_data_studio.tools.graph_render import IgorMatplotlibRenderer

FORMATS = {"Enhanced Metafile": "emf", "Bitmap": "bmp", "EPS File": "eps",
           "PDF": "pdf", "PNG File": "png", "JPEG File": "jpg",
           "TIFF File": "tiff", "SVG": "svg"}
RASTER = {"bmp", "png", "jpg", "tiff"}
UNITS = {"cm": 1 / 2.54, "mm": 1 / 25.4, "in": 1.0, "pt": 1 / 72}


def _monochrome(value: Any, key: str = "") -> Any:
    """Convert canonical colors before drawing, including errors/legend symbols."""
    if isinstance(value, str) and key in {"raw", "label_raw", "legend_raw", "colorbar_label_raw", "text"}:
        def inline(match: re.Match[str]) -> str:
            grey = round(sum(int(v) * w for v, w in zip(match.groups(), (0.2126, 0.7152, 0.0722), strict=True)))
            return f"\\K({grey},{grey},{grey})"
        return re.sub(r"\\K\((\d+),(\d+),(\d+)\)", inline, value)
    if isinstance(value, tuple) and len(value) == 3 and ("color" in key or key.endswith("_igor") or key == "plot_background"):
        grey = round(sum(component * weight for component, weight in zip(value, (0.2126, 0.7152, 0.0722), strict=True)))
        return grey, grey, grey
    if isinstance(value, str) and key in {"color", "marker_fill", "marker_edge"} and value != "none":
        rgba = colors.to_rgba(value)
        grey = sum(component * weight for component, weight in zip(rgba[:3], (0.2126, 0.7152, 0.0722), strict=True))
        return grey, grey, grey, rgba[3]
    if is_dataclass(value) and not isinstance(value, type):
        return replace(value, **{item.name: _monochrome(getattr(value, item.name), item.name) for item in fields(value)})
    if isinstance(value, dict):
        return {item: _monochrome(content, item) for item, content in value.items()}
    if isinstance(value, tuple):
        return tuple(_monochrome(item) for item in value)
    return value


@dataclass(frozen=True)
class GraphicsSettings:
    format: str = "svg"
    size_inches: tuple[float, float] | None = None
    dpi: float = 300
    color: bool = True
    transparent: bool = False


def save_graphics(path: Path, document: EditableGraphDocument, name: str,
                  settings: GraphicsSettings) -> None:
    """Render canonical Igor geometry without changing the live figure/document."""
    if settings.format not in set(FORMATS.values()) - {"emf"}:
        raise ValueError("Enhanced Metafile is not supported; use SVG or PDF for vector graphics.")
    report = document.compatibility(name)
    errors = [d for d in report.diagnostics if d.severity == "error"]
    if errors:
        raise ValueError("Unrenderable graph: " + "; ".join(f"[{d.code}] {d.property}: {d.message}" for d in errors))
    if not math.isfinite(settings.dpi) or not 10 <= settings.dpi <= 2400:
        raise ValueError("DPI must be between 10 and 2400.")
    renderer = IgorMatplotlibRenderer()
    size = settings.size_inches or renderer.export_size(document.graph(name))
    if len(size) != 2 or not all(math.isfinite(v) and 0 < v <= 100 for v in size):
        raise ValueError("Width and height must be positive and no greater than 100 inches.")
    if settings.format in RASTER and size[0] * size[1] * settings.dpi**2 > 80_000_000:
        raise ValueError("Raster export exceeds 80 million pixels; reduce size or DPI.")
    if settings.transparent and settings.format in {"jpg", "bmp", "eps"}:
        raise ValueError("This format does not support transparent backgrounds.")
    figure = Figure(figsize=size, dpi=settings.dpi)
    FigureCanvasAgg(figure)
    rendered = document
    if not settings.color:
        state = document.current.clone()
        state.graphs = tuple(_monochrome(graph) for graph in state.graphs)
        state.layout = _monochrome(state.layout)
        rendered = replace(document, current=state)
    renderer.render(figure, rendered, name)
    if not settings.color:
        for item in figure.findobj():
            artist: Any = item
            if hasattr(artist, "get_cmap"):
                cmap = artist.get_cmap()
                rgba = cmap(np.linspace(0, 1, 256))
                rgba[:, :3] = (rgba[:, :3] @ [0.2126, 0.7152, 0.0722])[:, None]
                artist.set_cmap(colors.ListedColormap(rgba))
            # Frame-owned rich Igor text is not a normal Axes child.
            targets = [artist, artist.text] if hasattr(artist, "text") and not isinstance(artist.text, str) else [artist]
            for target in targets:
                if hasattr(target, "raw"):
                    def monochrome(match: re.Match[str]) -> str:
                        grey = round(sum(int(v) * w for v, w in zip(match.groups(), (0.2126, 0.7152, 0.0722), strict=True)))
                        return f"\\K({grey},{grey},{grey})"
                    target.raw = re.sub(r"\\K\((\d+),(\d+),(\d+)\)", monochrome, target.raw)
                for attribute in ("color", "facecolor", "edgecolor", "markerfacecolor", "markeredgecolor"):
                    getter, setter = getattr(target, f"get_{attribute}", None), getattr(target, f"set_{attribute}", None)
                    if getter is None or setter is None:
                        continue
                    try:
                        rgba = colors.to_rgba_array(getter())
                        if len(rgba) == 0:
                            continue  # Hollow/none must remain none, not an empty RGB array.
                        rgba[:, :3] = (rgba[:, :3] @ [0.2126, 0.7152, 0.0722])[:, None]
                        setter(tuple(rgba[0]) if len(rgba) == 1 else rgba)
                    except (TypeError, ValueError):
                        continue
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=f".{settings.format}", dir=path.parent)
    os.close(descriptor)
    try:
        if settings.format == "bmp":
            buffer = io.BytesIO()
            figure.savefig(buffer, format="png", dpi=settings.dpi)
            with Image.open(buffer) as bitmap:
                bitmap.convert("RGB").save(temporary, format="BMP")
        else:
            figure.savefig(temporary, format=settings.format, dpi=settings.dpi, transparent=settings.transparent)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class SaveGraphicsDialog(QDialog):
    """Reproduce Save Graphics File's size/format/file workflow, without Igor execution."""

    def __init__(self, parent: QWidget, document: EditableGraphDocument, name: str) -> None:
        super().__init__(parent)
        self.document, self.name = document, name
        self.setWindowTitle("Save Graphics File")
        self.setObjectName("SaveGraphicsDialog")
        self.resize(670, 660)
        outer = QVBoxLayout(self)
        size_box = QGroupBox("Size")
        size_form = QFormLayout(size_box)
        self.same = QRadioButton("Same (original graph size)")
        self.custom = QRadioButton("Custom")
        self.same.setChecked(True)
        size_form.addRow(self.same)
        row = QHBoxLayout()
        self.width_input, self.height_input = QLineEdit(), QLineEdit()
        self.units = QComboBox()
        self.units.addItems(list(UNITS))
        native = IgorMatplotlibRenderer().export_size(document.graph(name))
        self.width_input.setText(f"{native[0] * 2.54:.5g}")
        self.height_input.setText(f"{native[1] * 2.54:.5g}")
        row.addWidget(self.custom)
        for title, widget in (("Width", self.width_input), ("Height", self.height_input)):
            row.addWidget(QLabel(title))
            row.addWidget(widget)
        row.addWidget(self.units)
        size_form.addRow(row)
        outer.addWidget(size_box)
        format_box = QGroupBox("Format")
        format_layout = QVBoxLayout(format_box)
        self.recommended = QCheckBox("Show only recommended formats")
        self.recommended.setChecked(True)
        format_layout.addWidget(self.recommended)
        format_row = QHBoxLayout()
        self.formats = QListWidget()
        for title, extension in FORMATS.items():
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, extension)
            self.formats.addItem(item)
            if extension == "emf":
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                item.setToolTip("EMF is unavailable in this renderer. SVG/PDF preserve vectors.")
        format_row.addWidget(self.formats, 1)
        options = QFormLayout()
        self.color = QCheckBox("Color")
        self.color.setChecked(True)
        self.transparent = QCheckBox("Transparent background")
        self.dpi = QLineEdit("300")
        options.addRow(self.color)
        options.addRow(self.transparent)
        options.addRow("Raster DPI", self.dpi)
        format_row.addLayout(options)
        format_layout.addLayout(format_row)
        outer.addWidget(format_box)
        file_box = QGroupBox("File")
        file_form = QFormLayout(file_box)
        self.filename = QLineEdit(f"{name}.svg")
        self.directory = QLineEdit()
        self.directory.setPlaceholderText("_Use Dialog_ — choose destination after Do It")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.choose_directory)
        path_row = QHBoxLayout()
        path_row.addWidget(self.directory, 1)
        path_row.addWidget(browse)
        self.overwrite = QCheckBox("Force Overwrite")
        file_form.addRow("Name", self.filename)
        file_form.addRow("Path", path_row)
        file_form.addRow(self.overwrite)
        outer.addWidget(file_box)
        outer.addWidget(QLabel("Export summary (MDS rendering; no Igor commands are executed)"))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMaximumHeight(70)
        outer.addWidget(self.preview)
        buttons = QHBoxLayout()
        self.save_button = QPushButton("Do It")
        self.save_button.clicked.connect(self.save)
        command_button = QPushButton("To Cmd Line")
        command_button.setEnabled(False)
        command_button.setToolTip("MDS does not have an Igor command line and never executes imported Igor commands.")
        copy_button = QPushButton("To Clip")
        copy_button.setToolTip("Copy the export settings summary, as in Igor's To Clip command.")
        copy_button.clicked.connect(lambda: QApplication.clipboard().setText(self.preview.toPlainText()))
        help_button = QPushButton("Help")
        help_button.clicked.connect(lambda: QMessageBox.information(self, "Save Graphics", "Same keeps the imported graph's native size. Custom sets physical width/height.\nSVG/PDF/EPS are vector formats; images remain raster. DPI controls bitmap/image resolution.\nEMF and Igor command-line execution are unavailable. To Clip copies this export summary.\nSource PXP files and waves are never overwritten by this operation."))
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        for button in (self.save_button, command_button, copy_button):
            buttons.addWidget(button)
        buttons.addStretch()
        buttons.addWidget(help_button)
        buttons.addWidget(cancel)
        outer.addLayout(buttons)
        self.formats.currentRowChanged.connect(self.format_changed)
        self.recommended.toggled.connect(self.filter_formats)
        self.custom.toggled.connect(self.update_summary)
        for entry in (self.width_input, self.height_input, self.dpi, self.filename, self.directory):
            entry.textChanged.connect(self.update_summary)
        self.units.currentTextChanged.connect(self.update_summary)
        self.color.toggled.connect(self.update_summary)
        self.transparent.toggled.connect(self.update_summary)
        self.formats.setCurrentRow(7)
        self.filter_formats()

    def filter_formats(self) -> None:
        for index in range(self.formats.count()):
            item = self.formats.item(index)
            item.setHidden(self.recommended.isChecked() and item.data(Qt.ItemDataRole.UserRole) in {"bmp", "eps", "emf"})
        if self.formats.currentItem().isHidden():
            self.formats.setCurrentRow(7)

    def format_changed(self) -> None:
        extension = self.formats.currentItem().data(Qt.ItemDataRole.UserRole)
        path = Path(self.filename.text())
        self.filename.setText(str(path.with_suffix("." + extension)))
        self.dpi.setEnabled(extension in RASTER)
        self.transparent.setEnabled(extension not in {"bmp", "jpg", "eps", "emf"})
        if not self.transparent.isEnabled():
            self.transparent.setChecked(False)
        self.update_summary()

    def update_summary(self) -> None:
        for widget in (self.width_input, self.height_input, self.units):
            widget.setEnabled(self.custom.isChecked())
        extension = self.formats.currentItem().data(Qt.ItemDataRole.UserRole) if self.formats.currentItem() else "svg"
        size = f"{self.width_input.text()} × {self.height_input.text()} {self.units.currentText()}" if self.custom.isChecked() else "original graph size"
        self.preview.setPlainText(f"{self.filename.text()} | {extension.upper()} | {size}\nColor: {self.color.isChecked()} | Transparent: {self.transparent.isChecked()} | Raster DPI: {self.dpi.text()}")
        self.preview.appendPlainText(self.document.compatibility(self.name).summary)

    def choose_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Export Directory", self.directory.text())
        if directory:
            self.directory.setText(directory)

    def settings(self) -> GraphicsSettings:
        size = (float(self.width_input.text()) * UNITS[self.units.currentText()], float(self.height_input.text()) * UNITS[self.units.currentText()]) if self.custom.isChecked() else None
        return GraphicsSettings(self.formats.currentItem().data(Qt.ItemDataRole.UserRole), size, float(self.dpi.text()), self.color.isChecked(), self.transparent.isChecked())

    def save(self) -> None:
        try:
            settings = self.settings()
            name = self.filename.text().strip()
            if not name or Path(name).name != name:
                raise ValueError("Name must be a filename only; select its directory using Path.")
            path = Path(self.directory.text().strip()) / name if self.directory.text().strip() else None
            if path is None:
                filename, _ = QFileDialog.getSaveFileName(self, "Save Graphics File", name, f"{settings.format.upper()} (*.{settings.format})", options=QFileDialog.Option.DontConfirmOverwrite)
                if not filename:
                    return
                path = Path(filename)
            path = path.with_suffix("." + settings.format)
            if path.exists() and not self.overwrite.isChecked() and QMessageBox.question(self, "Overwrite Graphics", f"Replace {path.name}?") != QMessageBox.StandardButton.Yes:
                return
            from materials_data_studio.ui.tasks import blocking_task
            blocking_task(self, "Export graph figure", lambda: save_graphics(path, self.document, self.name, settings), allow_cancel=False)
            self.accept()
        except Exception as exc:
            QMessageBox.warning(self, "Graphics Export Error", str(exc))


def data_columns(document: EditableGraphDocument, name: str, selected: list[str],
                 displayed: bool = True) -> list[tuple[str, np.ndarray[Any, Any]]]:
    """Wide XY pairs, errors and long-form image coordinates; no interpolation."""
    graph = document.graph(name)
    source = document.render_source()
    columns: list[tuple[str, np.ndarray[Any, Any]]] = []
    for trace in graph.traces:
        if "trace:" + trace.name not in selected:
            continue
        if trace.box or trace.violin:
            distribution = trace.box or trace.violin
            assert distribution is not None
            for ref in distribution.waves:
                dataset = source.wave(ref)
                if dataset is None:
                    raise ValueError(f"Missing Box Plot dataset: {ref}")
                if dataset.data.ndim == 1:
                    columns.append((f"{trace.name}:{dataset.name}", dataset.data))
                else:
                    for i in range(dataset.shape[1]):
                        columns.append((f"{trace.name}:{dataset.name}[{i}]", dataset.data[:, i]))
            continue
        wave = source.wave(trace.y_wave)
        if wave is None or wave.data.ndim != 1 or wave.data.dtype.kind not in "biuf":
            raise ValueError(f"{trace.name}: numerical 1D waves are required for XY export.")
        xwave = source.wave(trace.x_wave) if trace.x_wave else None
        x = cast(np.ndarray[Any, Any], xwave.data if xwave is not None else wave.coordinate(0))
        y = cast(np.ndarray[Any, Any], wave.data)
        if x.ndim != 1 or len(x) != len(y):
            raise ValueError(f"{trace.name}: X/Y lengths do not match.")
        if trace.point_range and displayed:
            selection = slice(trace.point_range[0], trace.point_range[1]+1)
            x, y = x[selection], y[selection]
        if displayed and x.dtype.kind in "SU":
            x = np.arange(len(x), dtype=float)
        if displayed:
            x, y = x * (trace.multiplier[0] or 1) + trace.offset[0], y * (trace.multiplier[1] or 1) + trace.offset[1]
            if graph.swap_xy:
                x, y = y, x
        columns.extend(((f"{trace.name}:x", x), (f"{trace.name}:y", y)))
        for label, ref in [("zColor", trace.z_color[0] if trace.z_color else None), ("zSize", trace.z_size[0] if trace.z_size else None), ("zMarker", trace.z_marker)]:
            channel = source.wave(ref) if ref else None
            if channel is not None:
                if channel.data.ndim == 1:
                    columns.append((f"{trace.name}:{label}", channel.data[trace.point_range[0]:trace.point_range[1]+1] if displayed and trace.point_range else channel.data))
                else:
                    for i in range(channel.shape[1]):
                        columns.append((f"{trace.name}:{label}[{i}]", channel.data[trace.point_range[0]:trace.point_range[1]+1, i] if displayed and trace.point_range else channel.data[:, i]))
        for suffix, reference in (("error_low", trace.error_low), ("error_high", trace.error_high), ("x_error_low", trace.x_error_low), ("x_error_high", trace.x_error_high)):
            error = source.wave(reference) if reference else None
            if error is not None:
                data = cast(np.ndarray[Any, Any], error.data)
                if displayed and trace.point_range:
                    data = data[trace.point_range[0]:trace.point_range[1]+1]
                if data.ndim != 1 or len(data) != len(y):
                    raise ValueError(f"{trace.name}: error-wave length does not match Y.")
                scale = trace.multiplier[0 if suffix.startswith("x_") or trace.error_axis.upper() == "X" else 1] or 1
                values = data * abs(scale) if displayed else data
                # Negative scaling reverses the asymmetric upper/lower errors.
                label = {"error_low": "error_high", "error_high": "error_low", "x_error_low": "x_error_high", "x_error_high": "x_error_low"}[suffix] if displayed and scale < 0 else suffix
                axis = "X" if suffix.startswith("x_") else trace.error_axis.upper()
                if displayed and graph.swap_xy:
                    axis = "Y" if axis == "X" else "X"
                if axis == "X" and not label.startswith("x_"):
                    label = "x_" + label
                elif axis == "Y" and label.startswith("x_"):
                    label = label[2:]
                columns.append((f"{trace.name}:{label}", values))
    for spec in graph.images:
        if "image:" + spec.wave not in selected:
            continue
        wave = source.wave(spec.wave)
        if wave is None or (wave.data.ndim != 2 and not (wave.data.ndim == 3 and wave.shape[2] in {3,4})) or wave.data.dtype.kind not in "biuf":
            raise ValueError(f"{spec.wave}: numeric matrix or RGB/RGBA image required.")
        coordinates = [wave.coordinate(0), wave.coordinate(1)]
        if displayed:
            for dimension, ref in enumerate((spec.x_wave, spec.y_wave)):
                edges = source.wave(ref) if ref else None
                if edges is not None:
                    values = np.asarray(edges.data, dtype=float)
                    coordinates[dimension] = (values[:-1] + values[1:])/2
        x, y = np.meshgrid(*coordinates, indexing="ij")
        if displayed and graph.swap_xy:
            x, y = y, x
        columns.extend(((f"{spec.wave}:x", x.ravel()), (f"{spec.wave}:y", y.ravel())))
        if wave.data.ndim == 3:
            columns.extend((f"{spec.wave}:{('red','green','blue','alpha')[channel]}", wave.data[:,:,channel].ravel()) for channel in range(wave.shape[2]))
        else:
            columns.append((f"{spec.wave}:value", wave.data.ravel()))
    return columns


def save_data(path: Path, columns: list[tuple[str, np.ndarray[Any, Any]]], delimiter: str) -> None:
    if not columns:
        raise ValueError("Select at least one trace/image.")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, delimiter=delimiter)
            writer.writerow([name for name, _ in columns])
            for index in range(max(len(values) for _, values in columns)):
                writer.writerow([(str(values[index]) if values.dtype.kind in "biu" else format(float(values[index]), ".17g")) if index < len(values) else "" for _, values in columns])
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class GraphDataDialog(QDialog):
    def __init__(self, parent: QWidget, document: EditableGraphDocument, name: str) -> None:
        super().__init__(parent)
        self.document, self.name = document, name
        self.setWindowTitle("Export Graph Data")
        self.resize(530, 450)
        outer = QVBoxLayout(self)
        outer.addWidget(QLabel("Select traces/images. Each trace exports its own X/Y columns.\nImages export X/Y/value columns; different lengths are padded with empty cells."))
        self.items = QListWidget()
        graph = document.graph(name)
        view = document.current.layout.get(name, {}).get("view", {})
        for identifier, label, checked in [("trace:" + t.name, t.name, view.get("traces", {}).get(t.name, {}).get("visible", True)) for t in graph.traces] + [("image:" + i.wave, "Image: " + i.wave, view.get("images", {}).get(i.wave, {}).get("visible", not i.hidden)) for i in graph.images]:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, identifier)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            self.items.addItem(item)
        outer.addWidget(self.items)
        self.displayed = QCheckBox("Displayed coordinates (include offsets, multipliers and Swap X/Y)")
        self.displayed.setChecked(True)
        outer.addWidget(self.displayed)
        self.format = QComboBox()
        self.format.addItems(["CSV (*.csv)", "TSV (*.tsv)"])
        outer.addWidget(self.format)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def save(self) -> None:
        try:
            selected = [self.items.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.items.count()) if self.items.item(i).checkState() == Qt.CheckState.Checked]
            columns = data_columns(self.document, self.name, selected, self.displayed.isChecked())
            if not columns:
                raise ValueError("Select at least one trace/image.")
            filename, _ = QFileDialog.getSaveFileName(self, "Export Graph Data", self.name, self.format.currentText(), options=QFileDialog.Option.DontConfirmOverwrite)
            if filename:
                extension = ".csv" if self.format.currentIndex() == 0 else ".tsv"
                path = Path(filename).with_suffix(extension)
                if path.exists() and QMessageBox.question(self, "Overwrite Data", f"Replace {path.name}?") != QMessageBox.StandardButton.Yes:
                    return
                from materials_data_studio.ui.tasks import blocking_task
                blocking_task(self, "Export graph data", lambda: save_data(path, columns, "," if extension == ".csv" else "\t"), allow_cancel=False)
                self.accept()
        except Exception as exc:
            QMessageBox.warning(self, "Data Export Error", str(exc))
