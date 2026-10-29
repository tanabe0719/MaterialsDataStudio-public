"""Trusted, modeless dialogs for explicit requests from isolated Igor routines.

The worker supplies descriptions, never arbitrary GUI callbacks or filesystem
operations. Only an actual user's selection is read and snapshotted as text.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from threading import Event
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
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
    QVBoxLayout,
)
from shiboken6 import isValid

from materials_data_studio.tools.igor_input_resources import read_input_files
from materials_data_studio.ui.tasks import blocking_task


def file_filter(source: str) -> str:
    """Translate Igor's description:extensions; syntax to bounded Qt filters."""
    extensions = list(dict.fromkeys(extension.lstrip("*").casefold()
                                   for extension in re.findall(r"\*?\.[A-Za-z0-9_+-]+", source)))
    patterns = ["*" + extension for extension in extensions[:64]]
    if not patterns:
        patterns = ["*.dat", "*.txt", "*.prf", "*.csv", "*.tsv", "*.xy"]
    return "Requested text files (" + " ".join(patterns) + ");;All files (*)"


class IgorPromptDialog(QDialog):
    """One DoPrompt request, with numeric, string and popup controls."""
    def __init__(self, payload: dict[str, Any], parent: Any) -> None:
        super().__init__(parent)
        self.setWindowTitle(payload["title"])
        self.setModal(False)
        self.fields = payload["fields"]
        self.controls: dict[str, QLineEdit | QComboBox] = {}
        self.values: dict[str, Any] = {}
        layout = QVBoxLayout(self)
        if payload.get("help"):
            help_text = QLabel(payload["help"])
            help_text.setTextFormat(Qt.TextFormat.PlainText)
            help_text.setWordWrap(True)
            layout.addWidget(help_text)
        form = QFormLayout()
        for field in self.fields:
            if field["type"] == "choice":
                control: QLineEdit | QComboBox = QComboBox()
                control.addItems(field["choices"])
                control.setCurrentIndex(field["value"])
            else:
                control = QLineEdit(str(field["value"]))
                control.setMaxLength(65536 if field["type"] == "text" else 128)
            control.setAccessibleName(field["label"])
            label = QLabel(field["label"])
            label.setTextFormat(Qt.TextFormat.PlainText)
            form.addRow(label, control)
            self.controls[field["name"]] = control
        layout.addLayout(form)
        self.error = QLabel()
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(440, self.sizeHint().height())

    def accept(self) -> None:
        values = {}
        control: QLineEdit | QComboBox | None = None
        try:
            for field in self.fields:
                control = self.controls[field["name"]]
                if isinstance(control, QComboBox):
                    value: Any = control.currentIndex()
                elif field["type"] == "number":
                    value = float(control.text())
                    if not math.isfinite(value):
                        raise ValueError("Enter a finite number for " + field["label"])
                else:
                    value = control.text()
                values[field["name"]] = value
        except (ValueError, OverflowError) as exc:
            self.error.setText(str(exc))
            if control is not None:
                control.setFocus()
            return
        self.values = values
        super().accept()


class DirectoryInputsDialog(QDialog):
    """Make the directory snapshot explicit; no recursive/background access."""
    def __init__(self, paths: list[Path], parent: Any) -> None:
        super().__init__(parent)
        self.setWindowTitle("Choose folder input text files")
        self.paths = paths
        layout = QVBoxLayout(self)
        note = QLabel("Only the checked text files will be read. Subfolders are not scanned, and the isolated macro receives no access to the folder itself.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.files = QListWidget()
        for path in paths:
            item = QListWidgetItem(path.name)
            item.setToolTip(str(path))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.files.addItem(item)
        layout.addWidget(self.files)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(540, 420)

    def selected_paths(self) -> list[Path]:
        return [path for index, path in enumerate(self.paths) if self.files.item(index).checkState() == Qt.CheckState.Checked]


def directory_inputs(directory: Path, source_filter: str, cancelled: Event) -> list[Path]:
    """Bound directory enumeration before presenting the explicit file list."""
    extensions = {part.lstrip("*").casefold() for part in re.findall(r"\*?\.[A-Za-z0-9_+-]+", source_filter)}
    extensions = extensions or {".dat", ".txt", ".prf", ".csv", ".tsv", ".xy"}
    paths = []
    for index, path in enumerate(directory.iterdir()):
        if cancelled.is_set():
            raise InterruptedError("Folder input selection stopped")
        if index >= 10_000:
            raise ValueError("This folder has too many entries; choose a smaller input folder")
        if path.suffix.casefold() in extensions and path.is_file() and not path.is_symlink():
            paths.append(path)
            if len(paths) > 1000:
                raise ValueError("Select at most 1000 folder input text files")
    return sorted(paths, key=lambda path: path.name.casefold())


class IgorInteractionController:
    """One sequential session; closing or stopping invalidates all callbacks."""
    def __init__(self, workspace: Any, session: Any, resources: list[dict[str, str]]) -> None:
        self.workspace, self.session = workspace, session
        self.resources = [dict(item) for item in resources]
        self.dialog: QDialog | None = None
        self.request: dict[str, Any] | None = None
        self.closed = False
        self.read_cancelled: Event | None = None

    def poll(self) -> None:
        if self.closed or self.request is not None:
            return
        try:
            request = self.session.poll()
            if request is None:
                return
            self.request = request
            self.workspace.write("Igor macro is waiting for your response: " + request["payload"]["title"])
            kind, payload = request["kind"], request["payload"]
            if kind in ("file_open", "directory"):
                self._file_dialog(kind, payload)
            elif kind == "prompt":
                dialog = IgorPromptDialog(payload, self.workspace.window)
                self._show(dialog, lambda: self._reply("ok", {"values": dialog.values}))
            elif kind == "message":
                dialog = QMessageBox(self.workspace.window)
                dialog.setWindowTitle(payload["title"])
                dialog.setTextFormat(Qt.TextFormat.PlainText)
                dialog.setText(payload["text"])
                controls = [dialog.addButton(text, QMessageBox.ButtonRole.ActionRole) for text in payload["buttons"]]
                self._show(dialog, lambda: self._reply("ok", {"button": controls.index(dialog.clickedButton())}) if dialog.clickedButton() in controls else self._reply("cancelled", {}), message=True)
        except Exception as exc:
            self.workspace.write("Invalid isolated macro interaction: " + str(exc), "error")
            self.workspace.stop()

    def _show(self, dialog: QDialog, accepted: Any, *, message: bool = False) -> None:
        self.dialog = dialog
        request = self.request
        def live() -> bool:
            return not self.closed and self.request is request and self.dialog is dialog
        dialog.setModal(False)
        # Accepted handlers may enter the read/progress event loop. Automatic
        # close deletion can otherwise destroy QFileDialog while accept() is
        # still executing in C++, including when Stop re-enters this handler.
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        completing = 0
        def finish(callback):
            nonlocal completing
            completing += 1
            try:
                if live():
                    callback()
            finally:
                completing -= 1
                if completing == 0 and isValid(dialog):
                    dialog.deleteLater()
        if message:
            dialog.finished.connect(lambda _result: finish(accepted))
        else:
            dialog.accepted.connect(lambda: finish(accepted))
            dialog.rejected.connect(lambda: finish(lambda: self._reply("cancelled", {})))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _file_dialog(self, kind: str, payload: dict[str, Any]) -> None:
        dialog = QFileDialog(self.workspace.window, payload["title"])
        # Native dialogs can block OLE drops / owner events on Windows. The Qt
        # modeless dialog also makes Stop and the parent graph remain usable.
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
        if kind == "directory":
            dialog.setFileMode(QFileDialog.FileMode.Directory)
            dialog.setOption(QFileDialog.Option.ShowDirsOnly, True)
        else:
            dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
            dialog.setNameFilter(file_filter(payload.get("filter", "")))
            if payload.get("default_name"):
                dialog.selectFile(payload["default_name"])
        self._show(dialog, lambda: self._selected(kind, dialog.selectedFiles(), payload))

    def _selected(self, kind: str, selected: list[str], payload: dict[str, Any]) -> None:
        if not selected:
            self._reply("cancelled", {})
            return
        path = Path(selected[0])
        if kind == "directory":
            cancelled = Event()
            self.read_cancelled = cancelled
            try:
                paths = blocking_task(self.workspace.window, "List selected folder text files", lambda: directory_inputs(path, payload.get("filter", ""), cancelled), cancelled=cancelled)
                if self.closed:
                    return
                dialog = DirectoryInputsDialog(paths, self.workspace.window)
                self._show(dialog, lambda: self._read_files(dialog.selected_paths(), str(path)))
            except Exception as exc:
                self._failure(exc)
            finally:
                self.read_cancelled = None
        else:
            self._read_files([path], str(path))

    def _read_files(self, paths: list[Path], selected: str) -> None:
        cancelled = Event()
        self.read_cancelled = cancelled
        try:
            resources = blocking_task(self.workspace.window, "Read selected macro input text", lambda: read_input_files(paths, self.resources, cancelled=cancelled), cancelled=cancelled)
            if self.closed:
                return
            self.resources = resources
            selected_paths = {str(path.absolute()).casefold() for path in paths}
            selected_resources = [item for item in resources if item["path"].casefold() in selected_paths]
            self._reply("ok", {"resources": selected_resources, "selected": selected})
        except Exception as exc:
            self._failure(exc)
        finally:
            self.read_cancelled = None

    def _failure(self, error: Exception) -> None:
        if self.closed:
            return
        if isinstance(error, InterruptedError):
            self._reply("cancelled", {})
        else:
            self.workspace.write("Cannot read selected macro input: " + str(error), "error")
            self._reply("error", {"message": str(error)[:4096]})

    def _reply(self, status: str, payload: dict[str, Any]) -> None:
        if self.closed or self.request is None:
            return
        request = self.request
        self.request = None
        self.dialog = None
        try:
            self.session.respond(request, {"status": status, "payload": payload})
        except Exception as exc:
            self.workspace.write("Cannot send isolated macro response: " + str(exc), "error")
            self.workspace.stop()

    def close(self) -> None:
        self.closed = True
        if self.read_cancelled is not None:
            self.read_cancelled.set()
        if self.dialog is not None and isValid(self.dialog):
            self.dialog.close()
        self.dialog = None
        self.request = None
        self.session.close()
