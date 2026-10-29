"""Reviewed sharing, compatibility reports and native-coordinate image ROIs."""
import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
)

from materials_data_studio.tools.graph_document import save_graph_document
from materials_data_studio.tools.graph_workflows import image_roi, sharing_copy
from materials_data_studio.ui.tasks import blocking_task


def history_dialog(window):
    """Review/export persisted records; never execute imported or recorded text."""
    if window.editor is None:
        return
    state = window.editor.document.current
    dialog = QDialog(window)
    dialog.setWindowTitle("Processing history / command recipe")
    dialog.resize(900, 650)
    layout = QVBoxLayout(dialog)
    note = QLabel("GUI history includes descriptive entries and verified replay scripts. Replayable scripts have been executed against a temporary state and compared with the edit. Copy them into Command Window to review and run explicitly; imported Igor history and procedures are never executed automatically.")
    note.setWordWrap(True)
    layout.addWidget(note)
    tabs = QTabWidget()
    for title, key in (("GUI operation history", "operation_history"), ("MDS command recipe", "command_recipe")):
        text = QPlainTextEdit(json.dumps(state.environment.get(key, []), indent=2, ensure_ascii=False))
        text.setReadOnly(True)
        tabs.addTab(text, title)
    layout.addWidget(tabs, 1)
    copy_button = QPushButton("Copy MDS commands for explicit replay")
    commands = "\n".join(str(record["source"]) for record in state.environment.get("command_recipe", []) if record.get("replayable") and record.get("interpreter") == "MDS command subset")
    copy_button.setEnabled(bool(commands))
    copy_button.clicked.connect(lambda: QApplication.clipboard().setText(commands))
    layout.addWidget(copy_button)
    gui_commands = "\n".join(str(record["script"]) for record in state.environment.get("operation_history", []) if record.get("replayable") and isinstance(record.get("script"), str))
    gui_copy = QPushButton("Copy verified GUI scripts for explicit replay")
    gui_copy.setEnabled(bool(gui_commands))
    gui_copy.clicked.connect(lambda: QApplication.clipboard().setText(gui_commands))
    layout.addWidget(gui_copy)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.rejected.connect(dialog.close)
    layout.addWidget(buttons)
    dialog.exec()
    dialog.deleteLater()


def share_graphs(window):
    if window.editor is None:
        return
    dialog = QDialog(window)
    dialog.setWindowTitle("Create reviewed sharing copy")
    dialog.resize(760, 600)
    layout = QVBoxLayout(dialog)
    note = QLabel("Includes checked current graphs and required waves only. Original PXP, deleted waves, wave notes, tables, procedures, history and source paths are omitted. Graph text, wave names and dimension labels remain: review sample identifiers below.")
    note.setWordWrap(True)
    layout.addWidget(note)
    items = QListWidget()
    active = window.graph()
    for graph in window.editor.document.current.graphs:
        item = QListWidgetItem(graph.name)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked if active and graph.name == active.name else Qt.CheckState.Unchecked)
        items.addItem(item)
    layout.addWidget(items)
    contents = QPlainTextEdit()
    contents.setReadOnly(True)
    layout.addWidget(contents)
    prepared = []
    def review():
        try:
            value = sharing_copy(window.editor.document, [items.item(i).text() for i in range(items.count()) if items.item(i).checkState() == Qt.CheckState.Checked])
            prepared[:] = [value]
            contents.setPlainText("Included waves:\n" + "\n".join(f"{w.igor_path} {w.shape}" for w in value.render_source().waves) + "\n\nGraph text and visual overrides:\n" + "\n".join(g.legend_raw + "\n" + "\n".join(a.raw for a in g.annotations) + "\n" + "\n".join(d.text for d in g.drawings) + "\n" + "\n".join(a.label_raw for a in g.axes) + "\n" + "\n".join(i.colorbar_label_raw for i in g.images) for g in value.current.graphs) + "\n" + json.dumps(value.current.layout, indent=2, ensure_ascii=False))
        except Exception as exc:
            prepared.clear()
            contents.setPlainText(str(exc))
    items.itemChanged.connect(review)
    review()
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
    def save():
        if not prepared:
            return
        filename, _ = QFileDialog.getSaveFileName(dialog, "Save sharing copy", "Shared.mdsg", "MDS Graph (*.mdsg)")
        if filename:
            try:
                blocking_task(dialog, "Save sharing copy", lambda: save_graph_document(Path(filename).with_suffix(".mdsg"), prepared[0]), allow_cancel=False)
                dialog.accept()
            except Exception as exc:
                QMessageBox.warning(dialog, "Sharing copy", str(exc))
    buttons.accepted.connect(save)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.exec()
    dialog.deleteLater()


def roi_dialog(window):
    if window.editor is None:
        return
    document = window.editor.document
    waves = [w for w in document.render_source().waves if w.data.ndim == 2 and w.data.dtype.kind in "biuf"]
    if not waves:
        QMessageBox.information(window, "Image ROI", "Import a numeric 2D image wave first.")
        return
    dialog = QDialog(window)
    dialog.setWindowTitle("Image ROI → derived wave")
    dialog.resize(680, 480)
    layout = QVBoxLayout(dialog)
    note = QLabel("Use native wave coordinates, independent of Swap X/Y. Profiles use finite-sample means; Integral includes physical spacing; Sum is a sample sum. No extrapolation.")
    note.setWordWrap(True)
    layout.addWidget(note)
    form = QFormLayout()
    selector = QComboBox()
    selector.addItems([w.igor_path for w in waves])
    form.addRow("Image wave", selector)
    bounds = [QLineEdit() for _ in range(4)]
    for label, edit in zip(("X minimum", "X maximum", "Y minimum", "Y maximum"), bounds, strict=True):
        form.addRow(label, edit)
    operation = QComboBox()
    for label, value in (("X profile (mean over Y)", "profile_x"), ("Y profile (mean over X)", "profile_y"), ("ROI mean", "mean"), ("ROI sum", "sum"), ("Physical ROI integral", "integral")):
        operation.addItem(label, value)
    form.addRow("Operation", operation)
    name = QLineEdit("roi_result")
    form.addRow("New wave name", name)
    def update():
        wave = waves[selector.currentIndex()]
        for edit, value in zip(bounds, [wave.coordinate(0).min(), wave.coordinate(0).max(), wave.coordinate(1).min(), wave.coordinate(1).max()], strict=True):
            edit.setText(f"{value:.12g}")
    selector.currentIndexChanged.connect(update)
    update()
    layout.addLayout(form)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    def apply():
        try:
            x0, x1, y0, y1 = (float(e.text()) for e in bounds)
            state = image_roi(document, selector.currentText(), (x0, x1, y0, y1), str(operation.currentData()), name.text())
            window.editor.commit(document.current, state, "Image ROI " + str(operation.currentData()))
            dialog.accept()
        except Exception as exc:
            QMessageBox.warning(dialog, "Image ROI", str(exc))
    buttons.accepted.connect(apply)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.exec()
    dialog.deleteLater()


def compatibility_dialog(window):
    if window.editor is None:
        return
    dialog = QDialog(window)
    dialog.setWindowTitle("Igor compatibility — inspect before exporting")
    dialog.resize(900, 650)
    layout = QVBoxLayout(dialog)
    text = QPlainTextEdit()
    text.setReadOnly(True)
    records = [f"{issue.scope}: {issue.message}" for issue in window.editor.document.source.issues]
    for graph in window.editor.document.current.graphs:
        records.extend(f"{graph.name} / {issue.scope}: {issue.message}" for issue in graph.issues)
    text.setPlainText("\n".join(records) or "No recorded compatibility issues. This does not guarantee pixel-identical Igor rendering.")
    layout.addWidget(text)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.rejected.connect(dialog.close)
    layout.addWidget(buttons)
    dialog.exec()
    dialog.deleteLater()
