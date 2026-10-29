"""Real-pool import and drop-handler regressions, using generated text only."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from threading import Event
from typing import Any

import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt, QTimer, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QDialog, QWidget

from materials_data_studio.tools import graph_wave_dialogs, graph_window
from materials_data_studio.tools.graph_waves import read_text, text_rows
from materials_data_studio.ui import tasks


def _drop(paths: list[Path]) -> tuple[QMimeData, QDropEvent]:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    event = QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction, mime,
                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    return mime, event


@pytest.mark.parametrize("platform", ["offscreen", pytest.param("windows", marks=pytest.mark.skipif(sys.platform != "win32", reason="Windows native Qt"))])
def test_real_background_workers_survive_repeated_immediate_jobs(tmp_path: Path, platform: str) -> None:
    """Isolate native crashes; use actual worker threads, not ImmediatePool."""
    script = """
import faulthandler
from threading import get_ident
from PySide6.QtCore import QCoreApplication,QThreadPool,QTimer
from PySide6.QtWidgets import QApplication,QWidget
from materials_data_studio.ui.tasks import blocking_task
faulthandler.enable()
app=QApplication([])
app.setQuitOnLastWindowClosed(False)
owner=QWidget()
timer=QTimer(owner)
timer.setSingleShot(True)
timer.timeout.connect(lambda: QCoreApplication.exit(99))
gui_thread=get_ident()
for index in range(500):
    timer.start(3000)
    result=blocking_task(owner,'Real-worker text stage',lambda n=index:(get_ident(),n))
    timer.stop()
    assert result[0]!=gui_thread and result[1]==index
    if index%100==0:
        print('completed',index,flush=True)
QThreadPool.globalInstance().waitForDone(3000)
print('all background jobs completed',flush=True)
"""
    for index in range(3):
        result = subprocess.run([sys.executable, "-X", "faulthandler", "-c", script],
                                capture_output=True, text=True, encoding="utf-8", timeout=20,
                                env={**os.environ, "QT_QPA_PLATFORM": platform,
                                     "MDS_SETTINGS_DIRECTORY": str(tmp_path / f"settings-{index}")})
        assert result.returncode == 0, result.stdout + result.stderr
        assert "all background jobs completed" in result.stdout


@pytest.mark.parametrize("surface", ["window", "canvas"])
def test_text_drop_returns_before_starting_real_read_and_parse(
    qtbot: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, surface: str,
) -> None:
    path = tmp_path / "generated.csv"
    path.write_text("X,Y\n1,2\n3,4\n", encoding="utf-8")
    window = graph_window.GraphEditorWindow()
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    qtbot.addWidget(window)
    inside_drop = True
    started: list[bool] = []
    completed: list[list[list[str]]] = []

    def import_generated(owner: QWidget, selected: Path) -> None:
        started.append(inside_drop)
        payload = tasks.blocking_task(owner, "Read general text", lambda: read_text(selected))
        completed.append(tasks.blocking_task(owner, "Parse text columns", lambda: text_rows(payload)))

    monkeypatch.setattr(graph_window, "import_text", import_generated)
    _mime, event = _drop([path])
    try:
        if surface == "window":
            window.dropEvent(event)
        else:
            assert window.file_drop_filter.eventFilter(window.canvas, event)
        inside_drop = False
        assert event.isAccepted()
        assert not started, "Native drop handlers must return before a modal/nested import loop."
        qtbot.waitUntil(lambda: bool(completed), timeout=5000)
        assert started == [False]
        assert completed == [[["X", "Y"], ["1", "2"], ["3", "4"]]]
    finally:
        window._discard_approved = True
        window.close()


def test_another_drop_during_read_is_queued_not_reentered(
    qtbot: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = [tmp_path / f"generated_{index}.csv" for index in range(2)]
    for path in paths:
        path.write_text("X,Y\n1,2\n3,4\n", encoding="utf-8")
    window = graph_window.GraphEditorWindow()
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    qtbot.addWidget(window)
    active = 0
    maximum_active = 0
    finished: list[Path] = []
    held_events: list[Any] = []

    def drop_second() -> None:
        mime, event = _drop([paths[1]])
        held_events.extend((mime, event))
        window.dropEvent(event)
        assert event.isAccepted()

    def import_generated(owner: QWidget, path: Path) -> None:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        gate = Event()
        if path == paths[0]:
            QTimer.singleShot(0, drop_second)
        QTimer.singleShot(30, gate.set)
        try:
            assert tasks.blocking_task(owner, "Read general text", lambda: gate.wait(2))
            finished.append(path)
        finally:
            active -= 1

    monkeypatch.setattr(graph_window, "import_text", import_generated)
    mime, event = _drop([paths[0]])
    held_events.extend((mime, event))
    try:
        window.dropEvent(event)
        qtbot.waitUntil(lambda: len(finished) == 2, timeout=5000)
        assert finished == paths
        assert maximum_active == 1, "A nested event loop must not start another import transaction."
    finally:
        window._discard_approved = True
        window.close()


def test_delimiter_change_during_real_worker_never_commits_stale_rows(
    qtbot: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "generated.csv"
    path.write_text("X,Y\n1,2\n3,4\n", encoding="utf-8")
    dialog = graph_wave_dialogs.TextImportDialog(None, path)
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: bool(dialog.rows), timeout=5000)
    original = graph_wave_dialogs.text_rows
    first_started = Event()
    release = Event()

    def delayed_parse(source: str, delimiter: str = "Auto", **kwargs: Any) -> list[list[str]]:
        if delimiter == "Tab":
            first_started.set()
            assert release.wait(2)
        return original(source, delimiter, **kwargs)

    monkeypatch.setattr(graph_wave_dialogs, "text_rows", delayed_parse)
    QTimer.singleShot(10, lambda: dialog.delimiter.setCurrentText("Comma"))
    QTimer.singleShot(30, release.set)
    dialog.delimiter.setCurrentText("Tab")
    try:
        qtbot.waitUntil(lambda: first_started.is_set(), timeout=5000)
        qtbot.waitUntil(lambda: not dialog.findChildren(QDialog), timeout=5000)
        assert dialog.rows == original(dialog.text, dialog.delimiter.currentText())
        assert dialog._parsed_delimiter == dialog.delimiter.currentText()
    finally:
        release.set()
        dialog.reject()


def test_auto_delimiter_does_not_promote_wide_metadata_into_data_columns() -> None:
    # Delimiter characters in arbitrary metadata must not turn an ordinary
    # two-column measurement into thousands of GUI column controls.
    metadata = ",".join(["# Generated metadata", *["tag"] * 2000])
    payload = metadata + "\n" + metadata + "\nX Y\n" + "\n".join(f"{i} {i * 2}" for i in range(20))
    rows = text_rows(payload)
    assert rows[-1] == ["19", "38"]
