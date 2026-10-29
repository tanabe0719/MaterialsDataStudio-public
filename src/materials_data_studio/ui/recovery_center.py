"""Explicit retention and recovery for MDSS and independent graph documents."""
from __future__ import annotations

import json
import os
import re
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from materials_data_studio.core.processes import process_is_running, process_started_at
from materials_data_studio.core.recovery import find_recovery_candidates, recovery_directory
from materials_data_studio.tools.graph_document import load_graph_document
from materials_data_studio.ui.tasks import blocking_task


def graph_candidates():
    result = []
    for directory in (recovery_directory(), Path(tempfile.gettempdir()) / "MaterialsDataStudio-graph-recovery"):
        for path in directory.glob("*.mdsg") if directory.is_dir() else []:
            match = re.fullmatch(r"graph-recovery-(\d+)-[0-9a-f]+\.mdsg", path.name)
            if match:
                pid = int(match.group(1))
                modified = path.stat().st_mtime
                started = process_started_at(pid) if process_is_running(pid) else None
                if pid == os.getpid() or (process_is_running(pid) and (started is None or started <= modified)):
                    continue
            result.append(path)
    return sorted(result, key=lambda path: path.stat().st_mtime, reverse=True)


def recovery_summary(path: Path):
    try:
        with zipfile.ZipFile(path) as archive:
            name = "session.json" if path.suffix == ".mdss" else "manifest.json"
            if archive.getinfo(name).file_size > 2 * 1024**2:
                return "Metadata too large to preview"
            value = json.loads(archive.read(name))
            if path.suffix == ".mdss":
                return ", ".join(d.get("display_name", "") for d in value.get("datasets", [])[:6])
            return f"Graph document {value.get('document_id', '')} · version {value.get('application_version', value.get('format_version', 'unknown'))}"
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        return "Unreadable metadata — retained; inspect before deleting"


class RecoveryCenter(QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setWindowTitle("Recovery Center — retained workspaces")
        self.resize(1050, 620)
        layout = QVBoxLayout(self)
        note = QLabel("No automatic expiry. Active owners are excluded where identifiable. Legacy graph copies have unknown ownership. Recover opens an unsaved copy; Keep is the default. Deletion requires explicit confirmation.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Type", "Modified (local)", "Bytes", "Contents / version", "Recovery filename"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        for text, callback in (("Refresh", self.refresh), ("Recover selected", self.recover), ("Delete selected…", self.delete)):
            button = QPushButton(text)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self):
        self.entries = [("Session", entry.path) for entry in find_recovery_candidates()] + [("Graph", path) for path in graph_candidates()]
        self.table.setRowCount(len(self.entries))
        for row, (kind, path) in enumerate(self.entries):
            info = path.stat()
            for col, text in enumerate((kind, datetime.fromtimestamp(info.st_mtime).isoformat(timespec="seconds"), str(info.st_size), recovery_summary(path), path.name)):
                self.table.setItem(row, col, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()

    def recover(self):
        index = self.table.currentRow()
        if index < 0:
            return
        kind, path = self.entries[index]
        try:
            if kind == "Session":
                if not hasattr(self.owner, "open_session"):
                    raise ValueError("Open Recovery Center from the main window to recover a session")
                self.owner.open_session(path)
                if self.owner.state.path == path:
                    self.owner.state.path = None
                    self.owner.state.recovery_source = path
                    self.owner.state.modified = True
                    self.owner.undo_history.reset(clean=False)
            else:
                document = blocking_task(self, "Read graph recovery", lambda: load_graph_document(path))
                if hasattr(self.owner, "open_native_graph"):
                    self.owner.open_native_graph(document)
                elif self.owner.confirm_close():
                    self.owner.bind_graph_document(document)
        except Exception as exc:
            QMessageBox.warning(self, "Recovery failed — copy retained", str(exc))

    def delete(self):
        index = self.table.currentRow()
        if index < 0:
            return
        _kind, path = self.entries[index]
        allowed = {recovery_directory().resolve(), (Path(tempfile.gettempdir()) / "MaterialsDataStudio-graph-recovery").resolve()}
        if path.resolve().parent not in allowed:
            raise ValueError("Recovery target is outside the application-owned directory")
        if QMessageBox.question(self, "Delete recovery copy?", f"Permanently delete only this recovery file?\n{path.name}\nThis does not delete the original measurement file.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            path.unlink(missing_ok=True)
            self.refresh()
