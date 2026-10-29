"""Import, inspect and verify separately installed reference data."""
from __future__ import annotations

from pathlib import Path
from threading import Event

from PySide6.QtCore import QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from materials_data_studio.modules.xrd.mp_database import NOTICES, MPDatabaseManager
from materials_data_studio.modules.xrd.qualx_database import (
    DATABASE_NOTICES,
    RECORD_URL,
    ReferenceDatabaseManager,
)
from materials_data_studio.ui.localization import StatusLabel, tr
from materials_data_studio.ui.tasks import run_task


class ReferenceDatabaseDialog(QDialog):
    progress = Signal(str)

    def __init__(self, manager: ReferenceDatabaseManager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.mp_manager = MPDatabaseManager(manager.store)
        self.stop = Event()
        self.busy = False
        self.setWindowTitle("Reference Databases")
        self.resize(820, 660)
        layout = QVBoxLayout(self)
        self.status = StatusLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addWidget(QLabel("Import the official COD Inorganic ZIP, or use an existing QualX database without copying it."))
        self.controls = []
        for label, action in (("Install / Import ZIP…", self.import_zip), ("Use existing .sq database…", self.import_file),
                              ("Use database folder…", self.import_folder), ("Open official download page", lambda: QDesktopServices.openUrl(QUrl(RECORD_URL)))):
            button = QPushButton(label)
            button.clicked.connect(action)
            self.controls.append(button)
            layout.addWidget(button)
        self.controls[0].setProperty("primary", True)
        row = QHBoxLayout()
        for label, action in (("Verify", self.verify), ("Open Database Folder", self.open_folder), ("Remove registration", self.remove)):
            button = QPushButton(label)
            if action == self.remove:
                button.setProperty("danger", True)
            button.clicked.connect(action)
            self.controls.append(button)
            row.addWidget(button)
        layout.addLayout(row)
        self.mp_status = StatusLabel()
        self.mp_status.setWordWrap(True)
        layout.addWidget(self.mp_status)
        mp_row = QHBoxLayout()
        for label, action in (("Import MP Index…", self.import_mp), ("Verify MP", self.verify_mp), ("Remove MP registration", self.remove_mp),
                              ("MP index build guide", lambda: QDesktopServices.openUrl(QUrl("https://github.com/tanabe0719/MaterialsDataStudio-public/blob/main/docs/phase_search/materials_project.md")))):
            button = QPushButton(label)
            if action == self.remove_mp:
                button.setProperty("danger", True)
            button.clicked.connect(action)
            self.controls.append(button)
            mp_row.addWidget(button)
        layout.addLayout(mp_row)
        self.cancel_button = QPushButton("Stop")
        self.cancel_button.setProperty("danger", False)
        self.cancel_button.clicked.connect(self.stop.set)
        self.cancel_button.setEnabled(False)
        layout.addWidget(self.cancel_button)
        self.information = QPlainTextEdit()
        self.information.setReadOnly(True)
        layout.addWidget(self.information, 1)
        layout.addWidget(QLabel("User CIFs are managed in the Phase Search > Local library tab. Remove registration keeps database files on disk."))
        self.progress.connect(self.message)
        self.refresh()

    @Slot(str)
    def message(self, value: str) -> None:
        self.status.setText(value)

    def refresh(self) -> None:
        info = self.manager.configuration()
        if not info:
            self.status.setText("COD Inorganic — Not installed")
        elif not Path(info["path"]).is_file() or not Path(info["index_path"]).is_file():
            self.status.setText("COD Inorganic — Error: database/index missing; import or verify again")
        else:
            self.status.setText(f"COD Inorganic — Ready: {info['phase_count']:,} records, {info.get('searchable_phase_count', 0):,} with reference peaks; snapshot {info['snapshot']}")
        summary = ""
        if info:
            summary = (f"Database location: {Path(info['path']).parent}\n"
                       f"Snapshot: {info['snapshot']}\nRecords: {info['phase_count']:,}\n"
                       f"With reference peaks: {info.get('searchable_phase_count', 0):,}\n"
                       f"IDs without stored peaks: {', '.join(info.get('unsearchable_phase_ids', [])) or 'None'}\n"
                       f"Content SHA-256: {info['content_hash']}\n"
                       f"Official ZIP checksum verified: {'Yes' if info.get('official_archive_verified') else 'Folder/custom import; use Verify'}\n\n")
        self.information.setPlainText(summary + DATABASE_NOTICES)
        mp = self.mp_manager.configuration()
        self.mp_status.setText(f"Materials Project — {mp['phase_count']:,} DFT structures; {mp['snapshot']}; {mp['license']}\n{mp['path']}" if mp else "Materials Project — Not installed")
        self.information.appendPlainText("\n" + NOTICES)

    def import_mp(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import Materials Project index", "", "MP reference index (*.sqlite *.zip)")
        if path:
            self.job(lambda stop, report: self.mp_manager.import_archive(Path(path), stop, report) if Path(path).suffix.lower() == ".zip" else self.mp_manager.register(Path(path), stop, report))

    def verify_mp(self) -> None:
        self.job(self.mp_manager.verify)

    def remove_mp(self) -> None:
        if self._confirm_remove("Materials Project"):
            self.mp_manager.remove()
            self.refresh()

    def job(self, action) -> None:
        if self.busy:
            return
        self.busy = True
        self.stop = Event()
        self.cancel_button.clicked.disconnect()
        self.cancel_button.clicked.connect(self.stop.set)
        for control in self.controls:
            control.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self._stop_style(True)
        task = run_task(self, "Reference database", lambda: action(self.stop, self.progress.emit), lambda _value: self.refresh(),
                        cancelled=self.stop, on_error=self.message)
        task.finished.connect(self.finished_job)

    def finished_job(self) -> None:
        self.busy = False
        for control in self.controls:
            control.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self._stop_style(False)
        if self.stop.is_set():
            self.status.setText("Stopped; the previous database registration was retained.")

    def _stop_style(self, active):
        self.cancel_button.setProperty("danger", active)
        self.cancel_button.style().unpolish(self.cancel_button)
        self.cancel_button.style().polish(self.cancel_button)

    def import_zip(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import QualX reference database", "", "ZIP (*.zip)")
        if path:
            self.job(lambda stop, report: self.manager.import_zip(Path(path), stop, report))

    def import_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Use existing QualX database", str(Path.home() / "QualXDB"), "QualX (*.sq)")
        if path:
            self.job(lambda stop, report: self.manager.register(Path(path), stop, report))

    def import_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Use QualX database folder", str(Path.home() / "QualXDB"))
        if path:
            self.job(lambda stop, report: self.manager.register(Path(path), stop, report))

    def verify(self) -> None:
        self.job(self.manager.verify)

    def open_folder(self) -> None:
        info = self.manager.configuration()
        if info:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(info["path"]).parent)))

    def remove(self) -> None:
        if self._confirm_remove("COD"):
            self.manager.remove()
            self.refresh()

    def _confirm_remove(self, source: str) -> bool:
        return QMessageBox.question(self, tr("Remove registration"),
                                    tr("Remove the {source} registration? Database files will remain on disk.").format(source=source),
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def closeEvent(self, event) -> None:
        self.stop.set()
        super().closeEvent(event)
