"""Fast completion, GUI-thread delivery and bounded general-text import work."""

from __future__ import annotations

from pathlib import Path
from threading import Event, get_ident
from typing import Any

import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QThreadPool, QTimer
from PySide6.QtWidgets import QComboBox, QDialog, QMessageBox, QProgressDialog, QWidget
from shiboken6 import isValid

from materials_data_studio.tools import graph_wave_dialogs
from materials_data_studio.tools.graph_actions import import_text
from materials_data_studio.tools.graph_waves import (
    detect_start,
    import_columns,
    read_text,
    text_rows,
)
from materials_data_studio.ui import tasks


def test_blocking_task_handles_completion_before_start_returns(qtbot: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Completion must not be lost between starting the worker and exec()."""
    owner = QWidget()
    qtbot.addWidget(owner)

    class ImmediatePool:
        def start(self, worker: Any) -> None:
            worker.run()

    monkeypatch.setattr(QThreadPool, "globalInstance", lambda: ImmediatePool())
    # An independent watchdog would release the old implementation's stuck
    # nested loop. The corrected implementation never needs that timeout.
    watchdog_fired: list[bool] = []
    loops: list[QEventLoop] = []

    def make_loop() -> QEventLoop:
        loop = QEventLoop()
        loops.append(loop)
        return loop

    def release_stuck_loop() -> None:
        watchdog_fired.append(True)
        loops[-1].quit()

    monkeypatch.setattr(tasks, "QEventLoop", make_loop)
    timer = QTimer(owner)
    timer.setSingleShot(True)
    timer.timeout.connect(release_stuck_loop)
    timer.start(200)
    assert tasks.blocking_task(owner, "Immediate read", lambda: "ready") == "ready"
    timer.stop()
    assert not watchdog_fired
    with pytest.raises(RuntimeError, match="synthetic failure"):
        tasks.blocking_task(owner, "Immediate error", lambda: (_ for _ in ()).throw(ValueError("synthetic failure")))


def test_task_results_are_delivered_on_gui_thread(qtbot: Any) -> None:
    owner = QWidget()
    qtbot.addWidget(owner)
    gui_thread = get_ident()
    workers: list[int] = []
    deliveries: list[tuple[int, int]] = []

    def work() -> int:
        workers.append(get_ident())
        return 42

    tasks.run_task(owner, "Thread delivery", work, lambda value: deliveries.append((get_ident(), value)))
    qtbot.waitUntil(lambda: bool(deliveries))
    assert workers[0] != gui_thread
    assert deliveries == [(gui_thread, 42)]
    assert tasks.blocking_task(owner, "Background value", lambda: (get_ident(), None))[0] != gui_thread


def test_task_failure_after_owner_destruction_is_safe(qtbot: Any) -> None:
    owner = QWidget()
    gate = Event()

    def fail() -> None:
        gate.wait(2)
        raise ValueError("Owner already closed")

    task = tasks.run_task(owner, "Close during import", fail, lambda _value: None)
    owner.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gate.set()
    qtbot.waitUntil(lambda: task not in tasks._LIVE)


def test_blocking_owner_destruction_requests_cooperative_stop(qtbot: Any) -> None:
    owner = QWidget()
    stop = Event()
    stopped: list[bool] = []
    QTimer.singleShot(10, owner.deleteLater)

    def work() -> None:
        stopped.append(stop.wait(1))

    with pytest.raises(InterruptedError):
        tasks.blocking_task(owner, "Parent closed", work, cancelled=stop)
    assert stopped == [True]


def test_blocking_task_cancels_cooperative_work(qtbot: Any) -> None:
    owner = QWidget()
    qtbot.addWidget(owner)
    stop = Event()
    timer = QTimer(owner)
    timer.setSingleShot(True)

    def cancel() -> None:
        dialog = owner.findChild(QProgressDialog)
        assert dialog is not None
        # cancel() does not emit canceled; emulate pressing Stop explicitly.
        dialog.canceled.emit()

    timer.timeout.connect(cancel)
    timer.start(10)

    def work() -> None:
        assert stop.wait(2), "The GUI must keep processing the Stop button."
        raise InterruptedError("Stopped")

    with pytest.raises(InterruptedError, match="stopped"):
        tasks.blocking_task(owner, "Cancelable parse", work, cancelled=stop)


def test_text_import_detection_runs_in_worker(qtbot: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "columns.csv"
    path.write_text("Time,Signal\n1,2\n2,3\n", encoding="utf-8")
    gui_thread = get_ident()
    original = graph_wave_dialogs.detect_start
    calls: list[int] = []

    def detect(*args: Any, **kwargs: Any) -> int:
        calls.append(get_ident())
        return original(*args, **kwargs)

    monkeypatch.setattr(graph_wave_dialogs, "detect_start", detect)
    dialog = graph_wave_dialogs.TextImportDialog(None, path)
    qtbot.addWidget(dialog)
    assert calls and all(thread != gui_thread for thread in calls)
    assert dialog.start.value() == 2
    dialog.load()
    np.testing.assert_array_equal(dialog.waves[1].data, [2, 3])


def test_mixed_column_detection_does_not_copy_every_remaining_row() -> None:
    class NoTailCopies(list[list[str]]):
        def __getitem__(self, key: Any) -> Any:
            assert not isinstance(key, slice), "Do not copy the remaining file per candidate."
            return super().__getitem__(key)

    # Each row is a candidate and its next row changes the numeric column.
    # The previous implementation copied 1.25 billion references here.
    rows = NoTailCopies([["1", "text"] if i % 2 else ["text", "1"] for i in range(50_000)])
    assert detect_start(rows) == 1


def test_large_sparse_quantum_design_file_and_multiline_text(tmp_path: Path) -> None:
    path = tmp_path / "generated.dat"
    records = 30_000
    path.write_text("[Header]\nSynthetic,123\n[Data]\nComment,Temperature,Signal,Unused\n" +
                    "".join(f",{i / 10},1.25D+0,\n" for i in range(records)), encoding="utf-8")
    rows = text_rows(read_text(path))
    assert detect_start(rows) == 4
    waves = import_columns(rows, 4, ["Comment", "Temperature", "Signal", "Unused"], ["Text", "Number", "Number", "Number"])
    assert all(wave.shape == (records,) for wave in waves)
    assert waves[1].data[-1] == (records - 1) / 10
    assert np.isnan(waves[-1].data).all()
    assert text_rows('x,comment\n1,"first\nsecond"\n2,end\n')[1] == ["1", "first\nsecond"]
    stop = Event()
    stop.set()
    for operation in (
        lambda: read_text(path, cancelled=stop),
        lambda: text_rows("1,2", cancelled=stop),
        lambda: detect_start(rows, cancelled=stop),
        lambda: import_columns(rows, 4, ["x"], ["Number"], cancelled=stop),
    ):
        with pytest.raises(InterruptedError):
            operation()


def test_blank_lines_do_not_restart_scan_for_every_measurement(monkeypatch: pytest.MonkeyPatch) -> None:
    from materials_data_studio.tools import graph_waves

    original = graph_waves._numeric
    calls = 0

    def numeric(value: str) -> bool:
        nonlocal calls
        calls += 1
        return original(value)

    monkeypatch.setattr(graph_waves, "_numeric", numeric)
    rows = [["Time", "Signal"], *[row for _ in range(20_000) for row in (["1", "2"], [])]]
    assert detect_start(rows) == 1
    assert calls < len(rows) * 8


def test_parsing_stops_between_batches() -> None:
    class StopAfterChecks(Event):
        checks = 0

        def is_set(self) -> bool:
            self.checks += 1
            return self.checks > 3

    with pytest.raises(InterruptedError):
        text_rows("1,2\n" * 20_000, "Comma", cancelled=StopAfterChecks())


def test_canceled_reparse_keeps_previous_delimiter_and_preview(qtbot: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "columns.csv"
    path.write_text("Time,Signal\n1,2\n2,3\n", encoding="utf-8")
    dialog = graph_wave_dialogs.TextImportDialog(None, path)
    qtbot.addWidget(dialog)
    original = dialog.rows

    def stopped(*_args: Any, **_kwargs: Any) -> None:
        raise InterruptedError("Parse stopped")

    monkeypatch.setattr(graph_wave_dialogs, "blocking_task", stopped)
    dialog.delimiter.setCurrentText("Tab")
    assert dialog.delimiter.currentText() == "Auto"
    assert dialog.rows is original
    assert dialog.columns.item(1, 1).text() == "Signal"


def test_parse_and_invalid_number_errors_are_reported_without_mutation(qtbot: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "columns.csv"
    path.write_text("Time,Signal\n1,2\n2,not-a-number\n", encoding="utf-8")
    dialog = graph_wave_dialogs.TextImportDialog(None, path)
    qtbot.addWidget(dialog)
    warnings: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, _title, text: warnings.append(text))
    kind = dialog.columns.cellWidget(1, 2)
    assert isinstance(kind, QComboBox)
    kind.setCurrentText("Number")
    dialog.load()
    assert len(warnings) == 1 and "not a number" in warnings[0]
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.waves == []
    original = dialog.rows
    first, header = dialog.start.value(), dialog.header.isChecked()

    def failed(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("Malformed input")

    monkeypatch.setattr(graph_wave_dialogs, "blocking_task", failed)
    dialog.delimiter.setCurrentText("Tab")
    assert dialog.delimiter.currentText() == "Auto"
    assert dialog.rows is original
    assert (dialog.start.value(), dialog.header.isChecked()) == (first, header)
    assert warnings[-1] == "Malformed input"


def test_finished_and_failed_import_dialogs_are_released(qtbot: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parent: Any = QWidget()
    qtbot.addWidget(parent)
    path = tmp_path / "columns.csv"
    path.write_text("Time,Signal\n1,2\n2,3\n", encoding="utf-8")
    dialogs: list[graph_wave_dialogs.TextImportDialog] = []

    def reject(dialog: graph_wave_dialogs.TextImportDialog) -> QDialog.DialogCode:
        dialogs.append(dialog)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(graph_wave_dialogs.TextImportDialog, "exec", reject)
    import_text(parent, path)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert dialogs and not isValid(dialogs[0])

    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("Initial parse failed")

    monkeypatch.setattr(graph_wave_dialogs, "blocking_task", fail)
    with pytest.raises(RuntimeError, match="Initial parse failed"):
        graph_wave_dialogs.TextImportDialog(parent, path)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert parent.findChildren(graph_wave_dialogs.TextImportDialog) == []


def test_closing_import_owner_does_not_access_deleted_dialog(qtbot: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parent: Any = QWidget()
    qtbot.addWidget(parent)
    path = tmp_path / "columns.csv"
    path.write_text("x,y\n1,2\n", encoding="utf-8")
    warnings: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, _title, text: warnings.append(text))

    def wait_until_closed(_path: Path, *, cancelled: Event | None = None) -> str:
        assert cancelled is not None and cancelled.wait(2)
        raise InterruptedError("Owner closed")

    monkeypatch.setattr(graph_wave_dialogs, "read_text", wait_until_closed)
    QTimer.singleShot(10, parent.deleteLater)
    import_text(parent, path)
    assert not isValid(parent) and not warnings


def test_auto_detection_preserves_data_after_wide_comma_metadata() -> None:
    metadata = ",".join(["# Generated metadata", *["tag"] * 2000])
    rows = text_rows(metadata + "\n" + metadata + "\nX,Y\n" + "\n".join(f"{i},{i * 2}" for i in range(20)))
    assert rows[-1] == ["19", "38"] and detect_start(rows) == 3


def test_safety_limits_and_bounded_preview_do_not_truncate_import_data(qtbot: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from materials_data_studio.tools import graph_waves
    with pytest.raises(ValueError, match="4096 fields"):
        text_rows(",".join("1" for _ in range(4097)), "Comma")
    with pytest.raises(ValueError, match="4 MiB"):
        text_rows("x" * (graph_waves.MAX_TEXT_LINE + 1), "Tab")
    with pytest.raises(ValueError, match="4 MiB"):
        text_rows("x" * (graph_waves.MAX_TEXT_LINE + 1), "Auto")
    original_limit = graph_waves.MAX_TEXT_CELLS
    monkeypatch.setattr(graph_waves, "MAX_TEXT_CELLS", 3)
    with pytest.raises(ValueError, match="million-cell"):
        text_rows("1,2\n3,4", "Comma")
    monkeypatch.setattr(graph_waves, "MAX_TEXT_CELLS", original_limit)
    path = tmp_path / "many_columns.csv"
    path.write_text(",".join(f"col{i}" for i in range(64)) + "\n" + ",".join(str(i) for i in range(64)), encoding="utf-8")
    dialog = graph_wave_dialogs.TextImportDialog(None, path)
    qtbot.addWidget(dialog)
    assert dialog.preview.columnCount() == 32 and dialog.columns.rowCount() == 64
    dialog.load()
    assert len(dialog.waves) == 64 and dialog.waves[-1].data.tolist() == [63]


def test_nested_import_does_not_open_a_second_dialog(qtbot: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parent: Any = QWidget()
    qtbot.addWidget(parent)
    path = tmp_path / "generated.csv"
    path.write_text("X,Y\n1,2\n", encoding="utf-8")
    opened: list[bool] = []
    def reenter(dialog: graph_wave_dialogs.TextImportDialog) -> QDialog.DialogCode:
        opened.append(True)
        import_text(parent, path)
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(graph_wave_dialogs.TextImportDialog, "exec", reenter)
    import_text(parent, path)
    assert opened == [True] and not parent._text_import_in_progress


def test_columns_appearing_after_preview_are_imported_and_late_caps_are_enforced(qtbot: Any, tmp_path: Path) -> None:
    path = tmp_path / "late_columns.csv"
    path.write_text("A,B\n" + "1,2\n" * 120 + "3,4,999\n", encoding="utf-8")
    dialog = graph_wave_dialogs.TextImportDialog(None, path)
    qtbot.addWidget(dialog)
    assert dialog.columns.rowCount() == 3
    dialog.load()
    assert len(dialog.waves) == 3
    assert np.isnan(dialog.waves[2].data[0]) and dialog.waves[2].data[-1] == 999
    path.write_text("A,B\n" + "1,2\n" * 120 + ",".join("1" for _ in range(513)), encoding="utf-8")
    with pytest.raises(RuntimeError, match="512 data columns"):
        graph_wave_dialogs.TextImportDialog(None, path)
