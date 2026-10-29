"""Synthetic axis color and tick-label enable range editing/storage regressions."""
from __future__ import annotations

import math
from dataclasses import replace

import pytest
from PySide6.QtWidgets import QMessageBox
from tests.tools.test_graph_document import source as source_fixture
from tests.tools.test_graph_interaction import editor_window

from materials_data_studio.tools.graph_document import load_graph_document, save_graph_document
from materials_data_studio.tools.graph_window import GraphEditorWindow
from materials_data_studio.ui.style_inputs import ColorEdit

source = source_fixture
window = editor_window


def axis_style(window: GraphEditorWindow):
    return next(axis.style for axis in window.graph().axes if axis.name == "bottom")


def test_axis_form_exposes_palettes_and_unbounded_tick_range(window):
    record = window.property_widgets["axis"]
    record["selector"].setCurrentText("bottom")
    widgets = record["widgets"]
    for key in ("axis_color", "label_color", "major_tick_color", "minor_tick_color", "tick_label_color"):
        assert isinstance(widgets[key][0], ColorEdit)
    assert widgets["tick_enable_min"][0].text() == "-inf"
    assert widgets["tick_enable_max"][0].text() == "inf"


def test_gui_axis_colors_and_range_apply_as_one_undo_and_record_commands(window, tmp_path):
    record = window.property_widgets["axis"]
    record["selector"].setCurrentText("bottom")
    widgets = record["widgets"]
    count = window.editor.history.count()
    changes = {"axis_color": "#123456", "label_color": "#336699", "major_tick_color": "#ff0000",
               "minor_tick_color": "#00ff00", "tick_label_color": "#0000ff",
               "tick_enable_min": "0.5", "tick_enable_max": "4"}
    for key, value in changes.items():
        widgets[key][0].setText(value)
    record["apply"]()
    style = axis_style(window)
    assert style.axis_color == (0x12 * 257, 0x34 * 257, 0x56 * 257)
    assert style.label_color == (0x33 * 257, 0x66 * 257, 0x99 * 257)
    assert style.major_tick_color == (65535, 0, 0)
    assert style.minor_tick_color == (0, 65535, 0)
    assert style.tick_label_color == (0, 0, 65535)
    assert style.tick_enable == (.5, 4)
    assert window.editor.history.count() == count + 1
    log = window.command_workspace.output.toPlainText()
    assert "axRGB(bottom)" in log and "alblRGB(bottom)" in log and "tlblRGB(bottom)" in log
    assert "tickEnab(bottom)={0.5,4.0}" in log
    assert "MDS-only major_tick_color" in log and "MDS-only minor_tick_color" in log
    window.editor.history.undo()
    assert axis_style(window).axis_color is None and axis_style(window).tick_enable is None
    window.editor.history.redo()
    assert axis_style(window) == style
    path = tmp_path / "synthetic_axis_colors.mdsg"
    save_graph_document(path, window.editor.document)
    restored = load_graph_document(path)
    restored_style = next(axis.style for axis in restored.graph("XY").axes if axis.name == "bottom")
    assert restored_style == style


@pytest.mark.parametrize("bounds", [(-math.inf, 3), (2, math.inf), (-math.inf, math.inf), (2, 2)])
def test_finite_and_infinite_tick_ranges_round_trip(window, tmp_path, bounds):
    limits = next(axis.limits for axis in window.graph().axes if axis.name == "bottom")
    window.apply_properties("axis", "bottom", {"tick_enable_min": bounds[0], "tick_enable_max": bounds[1]})
    assert axis_style(window).tick_enable == bounds
    assert next(axis.limits for axis in window.graph().axes if axis.name == "bottom") == limits
    path = tmp_path / "synthetic_tick_enable.mdsg"
    save_graph_document(path, window.editor.document)
    restored = load_graph_document(path)
    assert next(axis.style.tick_enable for axis in restored.graph("XY").axes if axis.name == "bottom") == bounds


def test_full_range_can_override_graph_default_and_blank_endpoints_are_unbounded(window):
    graph = window.graph()
    graph = replace(graph, style=replace(graph.style, axis_defaults=replace(graph.style.axis_defaults, tick_enable=(1., 3.))))
    window.editor.edit_graph(graph, "Synthetic restricted tick default")
    window.apply_properties("axis", "bottom", {"tick_enable_min": None, "tick_enable_max": None})
    assert axis_style(window).tick_enable == (-math.inf, math.inf)
    window.apply_properties("axis", "bottom", {"tick_enable_max": 5})
    assert axis_style(window).tick_enable == (-math.inf, 5)


@pytest.mark.parametrize("changes", [{"tick_enable_min": math.nan}, {"tick_enable_max": math.nan},
    {"tick_enable_min": 4, "tick_enable_max": 2}, {"major_tick_color": "not a color"},
    {"axis_color": "#80123456"}, {"minor_tick_color": (-1, 0, 0)},
    {"tick_label_color": (65536, 0, 0)}, {"font_size": math.inf}])
def test_invalid_color_or_bounds_are_atomic(window, changes):
    before, count = window.editor.document.current, window.editor.history.count()
    with pytest.raises(ValueError):
        window.apply_properties("axis", "bottom", changes)
    assert window.editor.document.current == before
    assert window.editor.history.count() == count


def test_native_tuple_colors_and_clearing_inherits_without_affecting_other_settings(window):
    window.apply_properties("axis", "bottom", {"axis_color": (1000, 2000, 3000), "major_tick_color": "red"})
    assert axis_style(window).axis_color == (1000, 2000, 3000)
    window.apply_properties("axis", "bottom", {"major_tick_color": ""})
    assert axis_style(window).major_tick_color is None
    assert axis_style(window).axis_color == (1000, 2000, 3000)


def test_gui_nan_reports_error_without_closing_form(window, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, _title, message: warnings.append(message))
    record = window.property_widgets["axis"]
    record["selector"].setCurrentText("bottom")
    record["widgets"]["tick_enable_min"][0].setText("nan")
    record["apply"]()
    assert warnings and "NaN" in warnings[0]
    assert axis_style(window).tick_enable is None
