"""Synthetic v0.10.0 workflow, numeric safety and docking regressions."""

from dataclasses import replace

import numpy as np
import pytest
from PySide6.QtCore import Qt

from materials_data_studio.core.memory import retained_bytes
from materials_data_studio.core.models import Dataset, ModuleType
from materials_data_studio.core.processing import invalidate_results, recalculate_columns
from materials_data_studio.plotting.plot_spec import CurveSpec, PlotSpec
from materials_data_studio.tools.graph_calculation import calculate_wave
from materials_data_studio.tools.graph_export import data_columns
from materials_data_studio.tools.graph_fit import trace_values
from materials_data_studio.tools.graph_waves import empty_document, make_wave
from materials_data_studio.tools.graph_window import GraphEditorWindow
from materials_data_studio.tools.graph_workflows import from_plot_spec, image_roi, sharing_copy
from materials_data_studio.tools.pxp_model import PxpGraph, PxpTrace


def document():
    result = empty_document()
    result.current.waves = tuple(make_wave(n, a) for n, a in (("x", [1., 2., 3.]), ("y", [2., 5., 8.]), ("error", [.1, .2, .3])))
    result.current.graphs = (PxpGraph("G", (PxpTrace("y", "root:y", "root:x", error_low="root:error", error_high="root:error", error_axis="X", multiplier=(2, 3)),)),)
    return result


def test_error_axis_weights_and_export():
    doc = document()
    assert trace_values(doc, "G", "y")[2] is None
    exported = dict(data_columns(doc, "G", ["trace:y"]))
    np.testing.assert_allclose(exported["y:x_error_low"], [.2, .4, .6])
    doc.current.graphs = (replace(doc.graph("G"), swap_xy=True),)
    np.testing.assert_allclose(trace_values(doc, "G", "y")[2], [.2, .4, .6])
    np.testing.assert_allclose(dict(data_columns(doc, "G", ["trace:y"]))["y:error_low"], [.2, .4, .6])


def test_coordinate_alignment_explicit():
    doc = document()
    assert doc.current.waves is not None
    doc.current.waves = (doc.current.waves[0], replace(doc.current.waves[1], dimension_scaling=((1., 2.),)), doc.current.waves[2])
    with pytest.raises(ValueError, match="coordinates or units"):
        calculate_wave(doc, "difference", "x-y")
    indexed = calculate_wave(doc, "difference", "x-y", alignment="index")
    assert indexed.waves is not None and indexed.waves[-1].shape == (3,)
    state = calculate_wave(doc, "difference", "x-y", alignment="interpolate")
    assert state.waves is not None
    assert np.isnan(state.waves[-1].data[0])


def test_derived_dependency_invalidation_atomic_refresh():
    ds = Dataset("test", ModuleType.PPMS, {"raw": np.arange(3.)}, metadata={"sample_moles": 2., "generic_columns": [
        {"key": "raw", "symbol": "a", "kind": "numeric", "origin": "imported"},
        {"key": "c", "symbol": "c", "name": "c", "kind": "numeric", "origin": "calculated", "expression": "b*2"},
        {"key": "b", "symbol": "b", "name": "b", "kind": "numeric", "origin": "calculated", "expression": "a/sample_moles"}]},
        analysis_results={"fit": {"value": 9}})
    invalidate_results(ds, "Mass changed")
    assert not ds.analysis_results and ds.metadata["stale_analysis"]["results"]["fit"]["value"] == 9
    recalculate_columns(ds)
    np.testing.assert_allclose(ds.processed_data["c"], [0, 1, 2])


def test_retained_budget_counts_original_bytes_and_shared_arrays():
    shared = np.arange(100.)
    old_bytes = b"x" * 10000
    assert retained_bytes({"a": shared}, [{"a": shared, "source": old_bytes}]) >= 10000
    assert retained_bytes(shared, [shared]) < 1000


def test_native_transfer_and_sharing_strips_source():
    doc = from_plot_spec(PlotSpec("Temperature (K)", "Signal", [CurveSpec(np.arange(3.), np.arange(3.), "A")]))
    assert doc.current.waves is not None
    doc.source = replace(doc.source, source_bytes=b"private original", procedures=("private code",), command_history=("private history",))
    doc.current.waves += (make_wave("deleted_private_wave", [123]),)
    doc.current.layout["MDS_Plot"]["private_source_path"] = "private source"
    doc.current.layout["MDS_Plot"]["view"]["traces"]["removed_private_trace"] = {"color": "#ffffff"}
    shared = sharing_copy(doc, ["MDS_Plot"])
    assert not shared.source.source_bytes and not shared.source.procedures and not shared.source.command_history
    assert shared.render_source().wave("deleted_private_wave") is None
    assert len(shared.render_source().waves) == 2
    assert "private" not in str(shared.current.layout)


def test_image_roi_profile_and_physical_integral():
    doc = empty_document()
    doc.current.waves = (make_wave("map", np.ones((3, 4))),)
    result = image_roi(doc, "map", (0, 2, 0, 3), "integral", "area")
    assert result.waves[-1].data[0] == pytest.approx(6)
    result = image_roi(doc, "map", (0, 2, 1, 2), "profile_x", "profile")
    np.testing.assert_allclose(result.waves[-1].data, [1, 1, 1])


def test_dock_back_discards_secondary_screen_geometry(qtbot):
    window = GraphEditorWindow()
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    window._discard_approved = True
    qtbot.addWidget(window)
    window.show()
    workspace = window.command_workspace
    for dock in (workspace.console_dock, workspace.procedure_dock):
        workspace.float_dock(dock)
        dock.setGeometry(-12000, 7000, 880, 650)
        workspace.dock_back(dock)
        qtbot.wait(150)
        assert not dock.isFloating() and not dock.isWindow() and dock.isVisible()
        assert dock.parentWidget() is window
        assert window.dockWidgetArea(dock) != Qt.DockWidgetArea.NoDockWidgetArea
        assert window.rect().intersects(dock.geometry())
    window.close()
