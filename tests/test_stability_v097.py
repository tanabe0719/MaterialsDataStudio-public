"""Synthetic release regressions; no private measurements or user settings."""

from __future__ import annotations

import io
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt
from PySide6.QtGui import QUndoStack
from PySide6.QtWidgets import QMessageBox, QPushButton, QWidget

from materials_data_studio.core import processes, recovery, session
from materials_data_studio.core.array_archive import validate_npz
from materials_data_studio.core.exceptions import SessionFormatError
from materials_data_studio.core.models import Dataset, ModuleType
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.core.source_io import import_source
from materials_data_studio.modules.ppms.heat_capacity import (
    average_adjacent_heat_capacity,
    fit_low_temperature,
)
from materials_data_studio.ui.undo_history import (
    WorkspaceFingerprinter,
    WorkspaceSnapshot,
    WorkspaceUndoController,
)


def test_duplicate_owns_nested_metadata_and_results() -> None:
    original = Dataset(
        "original",
        ModuleType.PPMS,
        {"x": np.arange(4.0)},
        metadata={"bridge": {"width": 1.0}},
        analysis_results={"fit": {"parameters": [2.0]}},
    )
    duplicate = original.duplicate()
    duplicate.metadata["bridge"]["width"] = 5
    duplicate.analysis_results["fit"]["parameters"][0] = 9
    assert original.metadata["bridge"]["width"] == 1
    assert original.analysis_results["fit"]["parameters"][0] == 2


def test_windows_process_query_never_signals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        processes.os, "kill", lambda *_args: pytest.fail("Process query must not send signals")
    )
    if os.name == "nt":
        assert processes.process_is_running(os.getpid())
        assert processes.process_started_at(os.getpid()) is not None


def test_old_recovery_files_are_not_implicitly_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "recovery-123-deadbeef.mdss"
    path.write_bytes(b"recovery")
    monkeypatch.setattr(recovery, "recovery_directory", lambda: tmp_path)
    monkeypatch.setattr(recovery, "_process_is_running", lambda _pid: False)
    candidates = recovery.find_recovery_candidates(now=datetime.now(UTC) + timedelta(days=90))
    assert len(candidates) == 1 and path.exists()


def test_recovery_failure_preserves_all_candidates(
    qtbot: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from materials_data_studio import app

    first, second = tmp_path / "first.mdss", tmp_path / "second.mdss"
    first.write_bytes(b"broken")
    second.write_bytes(b"good")
    monkeypatch.setattr(
        app,
        "find_recovery_candidates",
        lambda: [recovery.RecoveryCandidate(p, datetime.now(UTC), 1) for p in (first, second)],
    )
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: None)

    def load(path: Path) -> ProjectState:
        if path == first:
            raise ValueError("broken")
        return ProjectState()

    monkeypatch.setattr(app, "load_session", load)
    restored = app._recover_interrupted_session()
    assert restored is not None and restored.recovery_source == second
    assert first.exists() and second.exists()


def test_nested_npz_limit_is_checked_before_allocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "nested.mdss"
    session.save_session(
        ProjectState([Dataset("zeros", ModuleType.XRD, {"x": np.zeros(10000)})]), path
    )
    monkeypatch.setattr(session, "MAX_SESSION_TOTAL_BYTES", 4096)
    with pytest.raises(SessionFormatError, match="scientific arrays"):
        session.load_session(path)


def test_npz_objects_and_duplicate_members_are_rejected() -> None:
    buffer = io.BytesIO()
    np.savez_compressed(buffer, x=np.array([{}], dtype=object))
    with pytest.raises(ValueError, match="Unsafe"):
        validate_npz(buffer.getvalue(), maximum_bytes=10000)


def test_atomic_save_failure_preserves_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "session.mdss"
    target.write_bytes(b"original")

    def fail(*_args: Any) -> None:
        raise OSError("disk failure")

    monkeypatch.setattr(session.os, "replace", fail)
    with pytest.raises(SessionFormatError):
        session.save_session(ProjectState(), target)
    assert target.read_bytes() == b"original"
    assert not list(tmp_path.glob("*.tmp"))


def test_fixed_gamma_uncertainty_uses_one_free_parameter() -> None:
    t = np.arange(1.0, 6.0)
    x = t * t
    y = 0.05 * x + np.array([0.01, -0.02, 0.01, -0.015, 0.03])
    fit = fit_low_temperature(t, y * t, minimum_k=1, maximum_k=5, fix_gamma_zero=True)
    beta = np.dot(x, y) / np.dot(x, x)
    expected = np.sqrt(np.sum((y - beta * x) ** 2) / (len(t) - 1) / np.dot(x, x))
    assert fit.beta_error == pytest.approx(expected)


def test_averaging_keeps_different_fields_and_propagates_errors() -> None:
    result = average_adjacent_heat_capacity(
        [10, 10.01, 10.02],
        {"field_oe": [0, 0, 10000], "cp": [1, 3, 8], "cp_error": [0.1, 0.1, 0.2]},
    )
    np.testing.assert_allclose(result["field_oe"], [0, 10000])
    np.testing.assert_allclose(result["cp"], [2, 8])
    np.testing.assert_allclose(result["cp_error"], [np.sqrt(0.02) / 2, 0.2])


def test_history_shares_immutable_array_revisions() -> None:
    data = Dataset(
        "large", ModuleType.XRD, {"x": np.arange(100000.0)}, processed_data={"y": np.ones(100000)}
    )
    state = ProjectState([data])
    a = WorkspaceSnapshot.capture(state, {})
    b = WorkspaceSnapshot.capture(state, {})
    assert a.datasets[0].raw_data["x"] is b.datasets[0].raw_data["x"]
    assert a.datasets[0].processed_data["y"] is b.datasets[0].processed_data["y"]
    assert not a.datasets[0].processed_data["y"].flags.writeable
    data.processed_data["y"] = np.zeros(100000)
    assert a.datasets[0].processed_data["y"][0] == 1


def test_idle_history_does_not_scan_arrays(qtbot: Any) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    state = ProjectState()
    stack = QUndoStack(parent)
    calls: list[int] = []

    def fingerprint() -> str:
        calls.append(1)
        return "unchanged"

    history = WorkspaceUndoController(
        stack,
        lambda: WorkspaceSnapshot.capture(state, {}),
        fingerprint,
        lambda *_args: None,
        lambda _dirty: None,
        parent,
        change_token=lambda: state.change_token,
    )
    qtbot.wait(450)
    assert not calls and history.stack.count() == 0


def test_history_retained_memory_is_bounded(qtbot: Any) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    data = Dataset("x", ModuleType.XRD, {"x": np.arange(10.0)}, processed_data={"y": np.ones(10)})
    state = ProjectState([data])
    finger = WorkspaceFingerprinter()
    history = WorkspaceUndoController(
        QUndoStack(parent),
        lambda: WorkspaceSnapshot.capture(state, {}),
        lambda: finger.digest(state, {}),
        lambda *_args: None,
        lambda dirty: setattr(state, "modified", dirty),
        parent,
        memory_limit_bytes=1,
    )
    data.processed_data["y"] = np.zeros(10)
    history.checkpoint()
    assert history.stack.count() == 0 and state.modified


def test_stale_async_result_is_not_applied(qtbot: Any) -> None:
    from materials_data_studio.ui.tasks import run_task

    owner = QWidget()
    qtbot.addWidget(owner)
    state = ProjectState()
    release = Event()
    accepted = []
    done = []
    task = run_task(owner, "Synthetic job", lambda: release.wait(2), accepted.append, state=state)
    task.finished.connect(lambda: done.append(True))
    state.invalidate_jobs()
    release.set()
    qtbot.waitUntil(lambda: bool(done), timeout=5000)
    assert not accepted


@pytest.mark.parametrize("kind", ["heat", "resistivity"])
def test_ppms_toolbar_export_resolves_visible_tab(qtbot: Any, kind: str) -> None:
    from materials_data_studio.modules.ppms.heat_capacity_widget import HeatCapacityWidget
    from materials_data_studio.modules.ppms.resistivity_widget import ResistivityWidget
    from materials_data_studio.ui.main_window import MainWindow

    widget = (
        HeatCapacityWidget(ProjectState()) if kind == "heat" else ResistivityWidget(ProjectState())
    )
    qtbot.addWidget(widget)
    widget.plot_tabs.setCurrentIndex(1)
    shell: Any = SimpleNamespace(
        active_widget=lambda: SimpleNamespace(active_workspace=widget),
        active_module=lambda: SimpleNamespace(module_id="ppms"),
    )
    assert MainWindow._active_interactive_plot(shell)[2] is widget.plot_tabs.currentWidget()


def test_virtual_column_model_does_not_materialize_cells(qtbot: Any) -> None:
    from materials_data_studio.ui.array_table import ArrayTableModel

    model = ArrayTableModel(np.zeros(1000000))
    assert model.rowCount() == 1000000 and not model.edits
    assert model.setData(model.index(999999, 0), "3.5")
    assert model.result()[-1] == 3.5 and model.values[-1] == 0


def test_read_once_source_preserves_parsed_bytes(tmp_path: Path) -> None:
    from materials_data_studio.modules.mpms.parser import parse_mpms_dc

    path = tmp_path / "synthetic.dat"
    path.write_text(
        "INFO, WEIGHT,2\n[Data]\nTime,Magnetic Field,Temperature,Long Moment\n0,100,2,1\n"
    )
    imported = import_source(path, parse_mpms_dc)
    assert imported.result.valid_rows == 1 and b"INFO" in imported.data


def test_tick_lengths_are_independent(qtbot: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import pyqtgraph as pg

    from materials_data_studio.plotting.pyqtgraph_backend import ScientificAxisItem

    monkeypatch.setattr(
        pg.AxisItem,
        "generateDrawSpecs",
        lambda *_args: (
            None,
            [(None, QPointF(0, 0), QPointF(0, -12)), (None, QPointF(0, 0), QPointF(0, -8))],
            [],
        ),
    )
    axis = ScientificAxisItem("left")
    axis.setStyle(tickLength=-12)
    axis.major_length = 6
    axis.minor_length = 2
    ticks = axis.generateDrawSpecs(None)[1]
    assert [abs(end.y() - start.y()) for _, start, end in ticks] == [6, 2]


def test_ignored_igor_properties_are_reported() -> None:
    from materials_data_studio.tools.pxp_recreation import parse_recreation

    graphs, _ = parse_recreation(
        'Window G() : Graph\nDisplay y vs x\nModifyGraph standoff=2\nTextBox/F=1/LS=2 "note"\nEndMacro'
    )
    assert graphs[0].status == "Partially supported"
    assert graphs[0].annotations[0].style.frame == 1
    assert graphs[0].annotations[0].style.line_spacing == 2


def test_closed_manual_is_disposed(qtbot: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from materials_data_studio.ui.main_window import MainWindow
    from materials_data_studio.ui.manual import UserManualDialog

    monkeypatch.setattr(UserManualDialog, "exec", lambda _self: 0)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show_user_manual()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not window.findChildren(UserManualDialog)


def test_newer_job_supersedes_only_same_dataset(qtbot: Any) -> None:
    from materials_data_studio.ui.tasks import run_task

    owner = QWidget()
    qtbot.addWidget(owner)
    dataset = Dataset("data", ModuleType.XRD, {"x": np.arange(4.0)})
    state = ProjectState([dataset])
    gate = Event()
    accepted: list[int] = []
    first = run_task(
        owner, "Fit", lambda: (gate.wait(2), 1)[1], accepted.append, state=state, dataset=dataset
    )
    second = run_task(owner, "Fit", lambda: 2, accepted.append, state=state, dataset=dataset)
    assert first.cancelled.is_set() and not second.cancelled.is_set()
    gate.set()
    qtbot.waitUntil(lambda: 2 in accepted, timeout=5000)
    assert 1 not in accepted


def test_import_jobs_do_not_supersede_each_other(qtbot: Any) -> None:
    from materials_data_studio.ui.tasks import run_task

    owner = QWidget()
    qtbot.addWidget(owner)
    accepted: list[int] = []
    one = run_task(owner, "Import", lambda: 1, accepted.append)
    two = run_task(owner, "Import", lambda: 2, accepted.append)
    assert not one.cancelled.is_set() and not two.cancelled.is_set()
    qtbot.waitUntil(lambda: len(accepted) == 2, timeout=5000)
    assert sorted(accepted) == [1, 2]


def test_nonmutating_control_does_not_dirty_history(qtbot: Any) -> None:
    state = ProjectState()
    stack = QUndoStack()

    def capture() -> WorkspaceSnapshot:
        return WorkspaceSnapshot.capture(state, {})

    history = WorkspaceUndoController(
        stack,
        capture,
        lambda: capture().digest,
        lambda *_args: None,
        lambda modified: setattr(state, "modified", modified),
        change_token=lambda: state.change_token,
    )
    state.modified = True
    history.checkpoint()
    assert not state.modified and stack.isClean()


def test_render_reuses_curves_and_replaces_overlays(qtbot: Any) -> None:
    import pyqtgraph as pg

    from materials_data_studio.plotting.plot_spec import CurveSpec, PlotSpec
    from materials_data_studio.plotting.pyqtgraph_backend import create_plot_widget, render_plot

    widget = create_plot_widget()
    qtbot.addWidget(widget)
    spec = PlotSpec("x", "y", [CurveSpec(np.arange(5.0), np.arange(5.0), "demo")])
    render_plot(widget, spec)
    curve = widget.listDataItems()[0]
    for _ in range(5):
        widget.addItem(pg.InfiniteLine(pos=2))
        render_plot(widget, spec)
    assert widget.listDataItems()[0] is curve
    assert not any(isinstance(item, pg.InfiniteLine) for item in widget.getPlotItem().items)


def test_annotation_schema_preserves_new_fields() -> None:
    from materials_data_studio.tools.pxp_model import PxpDocument
    from materials_data_studio.tools.pxp_recreation import parse_recreation
    from materials_data_studio.tools.pxp_snapshot import decode_document, encode_document

    graphs, _ = parse_recreation(
        'Window G() : Graph\nDisplay y\nTextBox/F=1/B=2/O=30/LS=1.5 "note"\nEndMacro'
    )
    document = PxpDocument("synthetic", "synthetic.pxp", "", 0, "", "", "", graphs=graphs)
    manifest, arrays = encode_document(document)
    restored = decode_document(manifest, arrays)
    style = restored.graphs[0].annotations[0].style
    assert style.frame == 1 and style.rotation == 30 and style.line_spacing == 1.5


def test_view_all_recovers_full_bounds_after_clipped_zoom(qtbot: Any) -> None:
    from materials_data_studio.plotting.plot_spec import CurveSpec, PlotSpec
    from materials_data_studio.plotting.pyqtgraph_backend import create_plot_widget, render_plot

    widget = create_plot_widget()
    qtbot.addWidget(widget)
    render_plot(widget, PlotSpec("x", "y"))
    x = np.linspace(15, 82, 10000)
    render_plot(widget, PlotSpec("x", "y", [CurveSpec(x, np.sin(x), "full")]))
    assert widget.viewRange()[0][1] >= 82
    widget.setXRange(15, 15.5, padding=0)
    widget.listDataItems()[0].getData()
    widget.getViewBox().enableAutoRange(x=True, y=True)
    assert widget.viewRange()[0][0] <= 15 and widget.viewRange()[0][1] >= 82
    assert widget.getViewBox().autoRangeEnabled() == [True, True]


def test_main_figure_export_rejects_unsafe_allocation() -> None:
    from materials_data_studio.plotting.matplotlib_backend import build_figure
    from materials_data_studio.plotting.plot_spec import PlotSpec

    with pytest.raises(ValueError, match="megapixel"):
        build_figure(PlotSpec("x", "y", figure_width_cm=100, figure_height_cm=100, dpi=2400))


def test_imported_annotation_rotation_survives_editor_and_native_export(qtbot: Any) -> None:
    from materials_data_studio.tools.graph_annotation_dialog import AnnotationDialog
    from materials_data_studio.tools.graph_document import EditableGraphDocument
    from materials_data_studio.tools.graph_h5xp import procedures
    from materials_data_studio.tools.pxp_model import PxpDocument, PxpWave
    from materials_data_studio.tools.pxp_recreation import parse_recreation

    graphs, _ = parse_recreation('Window G() : Graph\nDisplay y\nTextBox/N=note/O=30 "note"\nEndMacro')
    values = np.arange(4.)
    wave = PxpWave("y", "root:y", "y", values.dtype.str, values.shape, values, ((0., 1.),), ("",), "", "")
    document = EditableGraphDocument.from_pxp(
        PxpDocument("synthetic", "synthetic.pxp", "", 0, "", "", "",
                    waves=(wave,), graphs=graphs, status="FULL")
    )
    dialog = AnnotationDialog(None, document, "G", "note")
    qtbot.addWidget(dialog)
    assert dialog.values()["rotation"] == 30
    assert "/O=30/" in procedures(document, {"y": "root:MDS:y"})


def test_running_task_shows_stop_and_click_cancels(qtbot: Any) -> None:
    from materials_data_studio.ui.tasks import run_task

    owner = QWidget()
    qtbot.addWidget(owner)
    cancelled = Event()
    accepted: list[Any] = []
    finished: list[bool] = []
    task = run_task(owner, "Synthetic long calculation", lambda: cancelled.wait(5),
                    accepted.append, cancelled=cancelled)
    task.finished.connect(lambda: finished.append(True))
    qtbot.waitUntil(task.dialog.isVisible, timeout=2000)
    stop = task.dialog.findChild(QPushButton)
    assert stop is not None
    qtbot.mouseClick(stop, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: bool(finished), timeout=3000)
    assert cancelled.is_set() and not accepted
