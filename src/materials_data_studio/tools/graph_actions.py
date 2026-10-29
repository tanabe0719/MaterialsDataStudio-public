"""Editor-level Data/Graph commands; all mutations use the local command stack."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMessageBox
from shiboken6 import isValid

from materials_data_studio.tools.graph_annotation_dialog import AnnotationDialog
from materials_data_studio.tools.graph_fit_dialog import QuickFitDialog
from materials_data_studio.tools.graph_trace_dialogs import (
    NewGraphDialog,
    ReorderTracesDialog,
    ReplaceWaveDialog,
)
from materials_data_studio.tools.graph_wave_dialogs import (
    ScalingDialog,
    TextImportDialog,
    WaveBrowserDialog,
    WaveEditorDialog,
    choose_text,
)
from materials_data_studio.tools.graph_waves import (
    delete_wave,
    empty_document,
    make_wave,
    put_waves,
    rename_wave,
    resize_wave,
    wave_references,
)

if TYPE_CHECKING:
    from materials_data_studio.tools.graph_window import GraphEditorWindow


def ensure_document(window: GraphEditorWindow) -> None:
    if window.editor is None:
        window.bind_graph_document(empty_document())


def import_text(window: GraphEditorWindow, path: Path | None = None) -> None:
    if getattr(window, "_text_import_in_progress", False):
        return
    # Nested progress/dialog loops must not start a second import transaction.
    window._text_import_in_progress = True
    try:
        _import_text(window, path)
    finally:
        window._text_import_in_progress = False


def _import_text(window: GraphEditorWindow, path: Path | None = None) -> None:
    path = path or choose_text(window)
    if path is None:
        return
    dialog: TextImportDialog | None = None
    try:
        dialog = TextImportDialog(window, path)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        ensure_document(window)
        assert window.editor is not None
        document = window.editor.document
        overwrite = dialog.overwrite.isChecked()
        existing = [w.igor_path for w in dialog.waves if document.render_source().wave(w.igor_path)]
        if overwrite and existing and QMessageBox.question(window, "Overwrite Working Waves", "Replace these working waves? Original PXP is preserved.\n" + "\n".join(existing)) != QMessageBox.StandardButton.Yes:
            return
        after = put_waves(document, dialog.waves, overwrite=overwrite)
        window.editor.commit(document.current, after, "Load general text waves")
    except InterruptedError:
        return
    except Exception as exc:
        if isValid(window):
            QMessageBox.warning(window, "Load General Text", str(exc))
    finally:
        # The editor is a long-lived parent. Retaining one dialog per import
        # would also retain its entire decoded file and parsed cell strings.
        if dialog is not None and isValid(dialog):
            dialog.deleteLater()


def selected_wave(window: GraphEditorWindow, title: str, path: str = "") -> Any:
    if window.editor is None:
        return None
    source = window.editor.document.render_source()
    if path:
        return source.wave(path)
    if window._selection[0] == "wave":
        return next((w for w in source.waves if w.id == window._selection[1]), None)
    dialog = WaveBrowserDialog(window, title, source.waves)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return source.wave(dialog.selected_path())
    return None


def wave_action(window: GraphEditorWindow, operation: str, path: str = "") -> None:
    try:
        if operation == "load":
            import_text(window)
            return
        ensure_document(window)
        assert window.editor is not None
        document = window.editor.document
        if operation == "make":
            name, ok = QInputDialog.getText(window, "Make Wave", "Wave name", text="wave0")
            if not ok:
                return
            count, ok = QInputDialog.getInt(window, "Make Wave", "Number of points", 100, 1, 10000000)
            if not ok:
                return
            kind, ok = QInputDialog.getItem(window, "Make Wave", "Type", ["Double precision", "Text"], 0, False)
            if not ok:
                return
            wave = make_wave(name, np.zeros(count, dtype=np.float64) if kind == "Double precision" else np.full(count, "", dtype="U1"))
            after = put_waves(document, [wave])
        else:
            wave = selected_wave(window, operation.title() + " Wave", path)
            if wave is None:
                return
            if operation == "edit":
                dialog = WaveEditorDialog(window, wave)
                if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.model.edits:
                    return
                assert dialog.result_wave is not None
                after = put_waves(document, [dialog.result_wave], overwrite=True)
            elif operation == "scaling":
                dialog = ScalingDialog(window, wave)
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                assert dialog.result_wave is not None
                after = put_waves(document, [dialog.result_wave], overwrite=True)
            elif operation in {"duplicate", "rename"}:
                name, ok = QInputDialog.getText(window, operation.title() + " Wave", "New name", text=wave.igor_path + "_copy" if operation == "duplicate" else wave.igor_path)
                if not ok:
                    return
                after = put_waves(document, [make_wave(name, wave.data)]) if operation == "duplicate" else rename_wave(document, wave.igor_path, name)
                if operation == "duplicate":
                    duplicated = after.waves[-1] if after.waves else wave
                    after.waves = tuple(replace(w, dimension_scaling=wave.dimension_scaling, dimension_units=wave.dimension_units,
                                                data_units=wave.data_units, wave_note=wave.wave_note) if w is duplicated else w for w in after.waves or ())
            elif operation == "delete":
                references = wave_references(document, wave.igor_path)
                message = f"Delete working wave {wave.igor_path}?"
                if references:
                    message += "\nDependent traces/images will also be removed from: " + ", ".join(references)
                if QMessageBox.question(window, "Delete Wave", message + "\nOriginal PXP is preserved; this can be undone.") != QMessageBox.StandardButton.Yes:
                    return
                after = delete_wave(document, wave.igor_path, cascade=True)
            elif operation in {"redimension", "insert", "delete points"}:
                count, ok = QInputDialog.getInt(window, operation.title(), "New length" if operation == "redimension" else "Number of points", 1, 1, 10000000)
                if not ok:
                    return
                index = None
                if operation != "redimension":
                    index, ok = QInputDialog.getInt(window, operation.title(), "First point (0-based)", 0, 0, wave.shape[0])
                    if not ok:
                        return
                after = put_waves(document, [resize_wave(wave, count, index, operation == "delete points")], overwrite=True)
            elif operation in {"rotate", "unwrap"}:
                if wave.data.ndim != 1 or (operation == "unwrap" and wave.data.dtype.kind not in "biuf"):
                    raise ValueError("This operation requires a 1D numeric wave.")
                if operation == "rotate":
                    shift, ok = QInputDialog.getInt(window, "Rotate Wave", "Point shift", 1, -wave.shape[0], wave.shape[0])
                    if not ok:
                        return
                    from materials_data_studio.tools.igor_discovery_analysis import rotate_wave
                    result = rotate_wave(wave, shift)
                else:
                    period, ok = QInputDialog.getDouble(window, "Unwrap Wave", "Period (radians: 2π)", 2 * np.pi, 1e-12, 1e12, 8)
                    if not ok:
                        return
                    from materials_data_studio.tools.igor_discovery_analysis import unwrap_wave
                    result = unwrap_wave(wave, period)
                after = put_waves(document, [result], overwrite=True)
            elif operation == "save":
                filename, _ = QFileDialog.getSaveFileName(window, "Save Wave", wave.name + ".tsv", "TSV (*.tsv);;CSV (*.csv)")
                if filename:
                    import csv
                    with Path(filename).open("w", encoding="utf-8-sig", newline="") as stream:
                        writer = csv.writer(stream, delimiter="," if Path(filename).suffix.lower() == ".csv" else "\t")
                        writer.writerow([wave.igor_path] if wave.data.ndim == 1 else [f"{wave.igor_path}[{i}]" for i in range(wave.shape[1])])
                        for row in wave.data:
                            writer.writerow([row] if wave.data.ndim == 1 else row)
                return
            else:
                raise ValueError("Unknown wave operation.")
        window.editor.commit(document.current, after, operation.title() + " wave")
    except Exception as exc:
        QMessageBox.warning(window, "Wave Operation", str(exc))


def graph_action(window: GraphEditorWindow, operation: str, trace_name: str = "") -> None:
    ensure_document(window)
    assert window.editor is not None
    graph = window.graph()
    if operation != "new" and graph is None:
        QMessageBox.information(window, "Graph Operation", "Select a graph in the explorer first.")
        return
    document = window.editor.document
    if operation in {"new", "append"}:
        dialog: Any = NewGraphDialog(window, document, graph if operation == "append" else None)
    elif operation == "reorder":
        assert graph is not None
        dialog = ReorderTracesDialog(window, graph)
    elif operation == "replace":
        assert graph is not None
        dialog = ReplaceWaveDialog(window, document, graph, trace_name)
    elif operation == "remove":
        assert graph is not None
        choices = [t.name for t in graph.traces]
        name, ok = QInputDialog.getItem(window, "Remove Trace", "Trace", choices, 0, False)
        if ok:
            window.editor.edit_graph(replace(graph, traces=tuple(t for t in graph.traces if t.name != name), cursors=tuple(c for c in graph.cursors if c.trace != name), annotations=tuple(a for a in graph.annotations if a.trace != name)), "Remove trace")
        return
    else:
        return
    if dialog.exec() == QDialog.DialogCode.Accepted and dialog.result_graph is not None:
        window._selection = "graph", dialog.result_graph.name
        window.view_state["selected"] = window._selection
        window.editor.edit_graph(dialog.result_graph, operation.title() + " graph / traces")
        window._state_changed()


def edit_annotation(window: GraphEditorWindow, kind: str, identifier: str) -> None:
    if window.editor is None or window.graph() is None:
        return
    graph = window.graph()
    assert graph is not None
    dialog = AnnotationDialog(window, window.editor.document, graph.name, identifier, kind == "colorbar")
    if dialog.exec() == QDialog.DialogCode.Accepted:
        window.editor.edit_graph(dialog.result_graph, "Edit annotation", dialog.result_layout)


def run_quick_fit(window: GraphEditorWindow, trace_name: str = "", model: str = "line", *, preview: bool = False) -> None:
    if window.editor is None or window.graph() is None:
        return
    graph = window.graph()
    assert graph is not None
    if not graph.traces:
        QMessageBox.information(window, "Quick Fit", "Quick Fit supports real 1D XY traces. Select an XY graph.")
        return
    from materials_data_studio.tools.graph_fit import cursor_limits, quick_fit, trace_values
    limits = None
    if not trace_name:
        if len(graph.traces) == 1:
            trace_name = graph.traces[0].name
        else:
            trace_name, accepted = QInputDialog.getItem(window, "Quick Fit", "Data trace", [t.name for t in graph.traces], 0, False)
            if not accepted:
                return
    if getattr(window, "fit_between_cursors", True):
        try:
            limits = cursor_limits(window.editor.document, graph.name, trace_name)
        except ValueError as exc:
            QMessageBox.warning(window, "Quick Fit", str(exc))
            return
    if limits is None and window.region and getattr(window, "fit_between_cursors", True):
        coords = window.region["ax"].transData.inverted().transform([window.region["start"], window.region["end"]])
        limits = float(coords[0, 0]), float(coords[1, 0])
    if not preview:
        from materials_data_studio.tools.graph_fit_dialog import fit_state
        from materials_data_studio.ui.tasks import blocking_task
        try:
            x, y, sigma = trace_values(window.editor.document, graph.name, trace_name)
            if not getattr(window, "fit_weighted", True):
                sigma = None
            result = blocking_task(window, "Quick Fit "+model, lambda: quick_fit(x, y, model, *(limits or (None, None)), sigma), allow_cancel=False)
            after = fit_state(window.editor.document, graph.name, trace_name, result, getattr(window, "fit_textbox", False))
            window.editor.commit(window.editor.document.current, after, "Quick Fit "+model)
            window.command_workspace.output.insertPlainText(result.summary()+"\n")
        except (ValueError, RuntimeError) as exc:
            QMessageBox.warning(window, "Quick Fit", str(exc))
        return
    dialog = QuickFitDialog(window, window.editor.document, graph.name, trace_name, model, limits,
                            getattr(window, "fit_weighted", True))
    if dialog.exec() == QDialog.DialogCode.Accepted and dialog.result_state is not None:
        window.editor.commit(window.editor.document.current, dialog.result_state, "Quick Fit " + model)


def install_menus(window: GraphEditorWindow) -> None:
    data = window.menuBar().addMenu("Data")
    window.data_menu = data
    def calculate() -> None:
        from materials_data_studio.tools.graph_calculation import WaveCalculationDialog
        ensure_document(window)
        assert window.editor is not None
        dialog = WaveCalculationDialog(window, window.editor.document)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.result_state is not None:
            window.editor.commit(window.editor.document.current, dialog.result_state, "Calculate wave")
    data.addAction("Calculate Wave…", calculate)
    from materials_data_studio.tools.graph_scientific_dialogs import scientific_wave_action
    for operation in ("Concatenate", "SplitWave", "Note", "SetDimLabel"):
        data.addAction(operation + "…", lambda _checked=False, op=operation: scientific_wave_action(window, op))
    from materials_data_studio.tools.graph_analysis_dialogs import analysis_action
    from materials_data_studio.tools.graph_analysis_forms import show_analysis
    from materials_data_studio.tools.graph_native_analysis_dialog import native_analysis
    from materials_data_studio.tools.graph_statistics import STATISTICS
    analysis = window.menuBar().addMenu("Analysis")
    statistics_menu = window.menuBar().addMenu("Statistics")
    window.analysis_menu = analysis
    window.statistics_menu = statistics_menu
    analysis.addAction("Curve Fitting…", lambda: run_quick_fit(window, preview=True))
    from materials_data_studio.tools.graph_menus import add_quick_fit
    add_quick_fit(analysis, window)
    transforms = analysis.addMenu("Transforms")
    transforms.addAction("FFT…", lambda: native_analysis(window, "FFT"))
    transforms.addAction("Inverse FFT…", lambda: show_analysis(window, "ifft", statistical=False))
    from materials_data_studio.tools.graph_spectral_analysis import SPECTRAL
    for key, (title, _methods) in SPECTRAL.items():
        transforms.addAction(title+"…", lambda _checked=False, kind=key: show_analysis(window, kind, statistical=False))
    for key, title in (("convolve", "Convolve"), ("correlate", "Correlate"), ("interpolate", "Interpolate"), ("filter", "Filter")):
        analysis.addAction(title+"…", lambda _checked=False, kind=key: show_analysis(window, kind, statistical=False))
    for operation in ("Differentiate", "Integrate", "Smooth", "Sort", "Histogram", "FindLevel"):
        analysis.addAction(operation+"…", lambda _checked=False, op=operation: native_analysis(window, op))
    analysis.addAction("Resample…", lambda: scientific_wave_action(window, "Resample"))
    analysis.addAction("Compose Expression…", calculate)
    analysis.addAction("Image Threshold / ROI Mask…", lambda: analysis_action(window, "ImageThreshold"))
    statistics_menu.addAction("Wave Stats…", lambda: show_analysis(window, "summary"))
    statistics_menu.addAction("T-Test…", lambda: analysis_action(window, "StatsTTest"))
    for key, (title, _methods) in STATISTICS.items():
        if key == "summary":
            continue
        statistics_menu.addAction(title+"…", lambda _checked=False, kind=key: show_analysis(window, kind))
    from materials_data_studio.tools.graph_anova_power import show_power
    statistics_menu.addAction("ANOVA Power…", lambda: show_power(window))
    graph = window.menuBar().addMenu("Graph")
    for title, operation in (("Load General Text…", "load"), ("Make Wave…", "make"), ("Edit Wave…", "edit"),
        ("Duplicate Wave…", "duplicate"), ("Rename Wave…", "rename"), ("Delete Wave…", "delete"),
        ("Change Wave Scaling…", "scaling"), ("Redimension Wave…", "redimension"), ("Insert Points…", "insert"),
        ("Delete Points…", "delete points"), ("Rotate Wave…", "rotate"), ("Unwrap Wave…", "unwrap"), ("Save Wave…", "save")):
        action = data.addAction(title)
        action.triggered.connect(lambda _checked=False, op=operation: wave_action(window, op))
    for title, operation in (("New Graph…", "new"), ("Append Traces / Images…", "append"), ("Reorder Traces…", "reorder"),
                              ("Replace X / Y Waves…", "replace"), ("Remove Trace…", "remove")):
        action = graph.addAction(title)
        action.triggered.connect(lambda _checked=False, op=operation: graph_action(window, op))
    from materials_data_studio.tools.graph_extension_dialogs import extension_action
    for title in ("Cursor", "Tag", "XY Error Bars", "Image Coordinates", "Date Axis", "Contour", "Layout", "Subwindow"):
        graph.addAction(title + "…", lambda _checked=False, op=title: extension_action(window, op))
    data.addAction("Rectangular ROI…", lambda: extension_action(window, "Rectangular ROI"))
    data.addAction("Table Format…", lambda: extension_action(window, "Table Format"))
    graph.addAction("Add Annotation…", window.add_text)
    window.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    def context(point: Any) -> None:
        from PySide6.QtWidgets import QMenu
        item = window.tree.itemAt(point)
        record = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if isinstance(record, tuple) and record[0] == "wave" and window.editor is not None:
            wave = next((w for w in window.editor.document.render_source().waves if w.id == record[1]), None)
            if wave:
                menu = QMenu(window)
                for title in ("edit", "duplicate", "rename", "delete", "scaling", "save"):
                    action = menu.addAction(title.title() + " Wave…")
                    action.triggered.connect(lambda _checked=False, op=title, path=wave.igor_path: wave_action(window, op, path))
                window.context_menu = menu
                menu.popup(window.tree.viewport().mapToGlobal(point))
        elif isinstance(record, tuple) and record[0] == "table" and window.editor is not None:
            from materials_data_studio.tools.graph_tables import table_action
            menu = QMenu(window)
            for title, operation in (("Edit cells…", "edit"), ("Modify columns…", "modify"), ("Delete table…", "delete")):
                menu.addAction(title).triggered.connect(lambda _checked=False, op=operation, name=record[1]: table_action(window, op, name))
            window.context_menu = menu
            menu.popup(window.tree.viewport().mapToGlobal(point))
    window.tree.customContextMenuRequested.connect(context)
