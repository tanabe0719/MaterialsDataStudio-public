"""Reviewed modal workflows and pending-edit safeguards; generated data only."""
from dataclasses import replace

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QWidget,
)

from materials_data_studio.core.models import Dataset, ModuleType
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.plotting.plot_spec import CurveSpec, PlotSpec
from materials_data_studio.plotting.pyqtgraph_backend import create_plot_widget, render_plot
from materials_data_studio.tools.graph_waves import empty_document, make_wave
from materials_data_studio.tools.graph_window import GraphEditorWindow
from materials_data_studio.tools.graph_workflows import from_plot_spec
from materials_data_studio.tools.pxp_model import PxpGraph, PxpImage
from materials_data_studio.ui import diagnostic_review, graph_workflow_dialogs
from materials_data_studio.ui.dataset_list import DatasetList
from materials_data_studio.ui.measurement_overview import measurement_overview


@pytest.fixture
def viewer(qtbot):
    window = GraphEditorWindow()
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    window._discard_approved = True
    qtbot.addWidget(window)
    document = empty_document()
    wave = replace(make_wave("image", np.ones((3, 4))), dimension_scaling=((0., 2.), (0., 3.)))
    document.current.waves = (wave,)
    document.current.graphs = (PxpGraph("Map", images=(PxpImage("root:image"),)),)
    window.bind_graph_document(document)
    yield window
    window.close()


def test_diagnostics_preview_exact_payload_and_reject(qtbot, monkeypatch):
    owner = QWidget()
    qtbot.addWidget(owner)
    monkeypatch.setattr(diagnostic_review, "diagnostic_logs", lambda *, redact_paths: {"log.txt": b"[redacted]" if redact_paths else b"Synthetic path"})
    def accept(dialog):
        assert "[redacted]" in dialog.findChild(QPlainTextEdit).toPlainText()
        dialog.findChild(QCheckBox).setChecked(False)
        assert "Synthetic path" in dialog.findChild(QPlainTextEdit).toPlainText()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog, "exec", accept)
    assert diagnostic_review.review_diagnostics(owner) == {"log.txt": b"Synthetic path"}
    monkeypatch.setattr(QDialog, "exec", lambda _: QDialog.DialogCode.Rejected)
    assert diagnostic_review.review_diagnostics(owner) is None


def test_roi_dialog_applies_physical_integral_and_undo(viewer, monkeypatch):
    assert viewer.editor is not None
    def apply(dialog):
        combo = next(w for w in dialog.findChildren(QComboBox) if w.findData("integral") >= 0)
        combo.setCurrentIndex(combo.findData("integral"))
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).click()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog, "exec", apply)
    graph_workflow_dialogs.roi_dialog(viewer)
    result = viewer.editor.document.render_source().wave("roi_result")
    assert result is not None and result.data[0] == pytest.approx(36.)
    viewer.editor.history.undo()
    assert viewer.editor.document.render_source().wave("roi_result") is None


def test_sharing_review_and_save_excludes_history(viewer, monkeypatch, tmp_path):
    path = tmp_path / "shared.mdsg"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(path), ""))
    def save(dialog):
        review = dialog.findChild(QPlainTextEdit).toPlainText()
        assert "root:image" in review
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog, "exec", save)
    graph_workflow_dialogs.share_graphs(viewer)
    assert path.is_file()


def test_history_review_only_and_compatibility(viewer, monkeypatch, qapp):
    assert viewer.editor is not None
    viewer.editor.document.current.environment["command_recipe"] = [{"source": "Make/N=3 example", "replayable": True, "interpreter": "MDS command subset"}]
    def inspect(dialog):
        if "Processing history" in dialog.windowTitle():
            dialog.findChild(QPushButton).click()
            assert qapp.clipboard().text() == "Make/N=3 example"
            assert viewer.editor is not None and viewer.editor.document.render_source().wave("example") is None
        else:
            assert "No recorded" in dialog.findChild(QPlainTextEdit).toPlainText()
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(QDialog, "exec", inspect)
    graph_workflow_dialogs.history_dialog(viewer)
    graph_workflow_dialogs.compatibility_dialog(viewer)


def test_pending_properties_block_replace_until_discard(viewer, monkeypatch):
    viewer.property_widgets = {"test": {"widgets": {"value": (QLineEdit("new"), str)}, "initial": {"value": "old"}}}
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.No)
    assert not viewer.confirm_close()
    monkeypatch.setattr(QMessageBox, "question", lambda _parent, title, *_: QMessageBox.StandardButton.Yes if title == "Unapplied property edits" else QMessageBox.StandardButton.Discard)
    assert viewer.editor is not None
    viewer.editor.history.setClean()
    assert viewer.confirm_close()
    viewer.bind_graph_document(empty_document())
    assert not viewer.property_widgets


def test_measurement_overview_preserves_order(qtbot, monkeypatch):
    dataset = Dataset("synthetic", ModuleType.MPMS, {"temperature_k": np.array([2., 100., 300., 200., 2.]), "field_oe": np.ones(5)*100})
    widget = DatasetList(ProjectState([dataset]), ModuleType.MPMS)
    qtbot.addWidget(widget)
    widget.list_widget.setCurrentRow(0)
    monkeypatch.setattr(QDialog, "exec", lambda dialog: QDialog.DialogCode.Rejected)
    measurement_overview(widget)
    np.testing.assert_array_equal(dataset.raw_data["temperature_k"], [2, 100, 300, 200, 2])


def test_log_switch_and_physical_limits_no_overflow(qtbot):
    widget = create_plot_widget()
    qtbot.addWidget(widget)
    spec = PlotSpec("x", "y", [CurveSpec(np.arange(1., 4.), np.array([1., 1e3, 1e5]), "signal")])
    render_plot(widget, spec)
    widget.getViewBox().setRange(yRange=(0, 120000), padding=0)
    with np.errstate(over="raise", invalid="raise"):
        render_plot(widget, replace(spec, y_log_scale=True, y_range=(1., 1e5)))
    np.testing.assert_allclose(widget.getViewBox().viewRange()[1], [0, 5])
    render_plot(widget, replace(spec, y_log_scale=True, y_range=(10., 1e4)))
    np.testing.assert_allclose(widget.getViewBox().viewRange()[1], [1, 4])


def test_native_graph_duplicate_labels_have_distinct_trace_ids():
    spec = PlotSpec("x", "y", [CurveSpec(np.arange(3.), np.arange(3.), "Same") for _ in range(2)], title="Synthetic title")
    graph = from_plot_spec(spec).graph("MDS_Plot")
    assert len({t.name for t in graph.traces}) == 2
    assert graph.annotations[0].raw == "Synthetic title"
