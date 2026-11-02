"""Pinned inspector, atomic multi-axis edits and user-tick wave selection."""
from dataclasses import replace

import numpy as np
import pytest
from PySide6.QtWidgets import QScrollArea
from tests.tools.test_graph_property_lifecycle import editor_window, source  # noqa: F401

from materials_data_studio.tools.graph_document import load_graph_document, save_graph_document
from materials_data_studio.tools.graph_waves import make_wave, put_waves
from materials_data_studio.tools.pxp_model import PxpAxis, PxpAxisStyle


def test_object_selector_is_outside_scrollers_and_sections_are_below(window):
    record = window.property_widgets["axis"]
    ancestor = record["selector"].parentWidget()
    while ancestor is not None and ancestor is not record["page"]:
        assert not isinstance(ancestor, QScrollArea)
        ancestor = ancestor.parentWidget()
    assert ancestor is record["page"]
    assert record["sections"].count() >= 4


def test_style_changes_reuse_inspector_and_preserve_pending_input(window, qtbot):
    record = window.property_widgets["axis"]
    edit = record["widgets"]["major_length"][0]
    edit.setText("12.5")
    page = record["page"]
    graph = window.graph()
    window.editor.edit_graph(replace(graph, traces=(replace(graph.traces[0], line_width=2.1), *graph.traces[1:])), "Synthetic line width")
    qtbot.wait(1)
    assert window.property_widgets["axis"]["page"] is page
    assert window.property_widgets["axis"]["widgets"]["major_length"][0] is edit
    assert edit.text() == "12.5"


def test_japanese_axis_selection_retains_identifiers(window):
    from materials_data_studio.ui.localization import set_language
    set_language("ja")
    selector = window.property_widgets["axis"]["selector"]
    assert selector.itemText(len(selector.names)+1) == "すべての軸を選択"
    selector.select_axes(("left", "bottom"))
    assert set(selector.selected_axes) == {"left", "bottom"}
    assert "Waveから目盛を設定" in [window.property_widgets["axis"]["widgets"]["tick_mode"][0].itemText(i) for i in range(3)]
    set_language("en")


def test_multi_axis_applies_only_changed_fields_and_one_undo(window):
    graph = window.graph()
    original = replace(graph, axes=(PxpAxis("left", style=PxpAxisStyle(log=1)), PxpAxis("bottom", style=PxpAxisStyle(log=0))))
    window.editor.edit_graph(original, "Synthetic axes")
    record = window.property_widgets["axis"]
    record["selector"].select_axes(("left", "bottom"))
    assert record["widgets"]["log"][0].currentIndex() == -1
    before_count = window.editor.history.count()
    record["widgets"]["major_length"][0].setText("9")
    record["apply"]()
    changed = {a.name: a for a in window.graph().axes}
    assert changed["left"].style.log == 1 and changed["bottom"].style.log == 0
    assert changed["left"].style.major_length == changed["bottom"].style.major_length == 9
    assert window.editor.history.count() == before_count + 1
    assert len(window.property_widgets["axis"]["selector"].selected_axes) == 2
    window.editor.history.undo()
    assert window.graph() == original


def test_user_ticks_require_paired_numeric_and_text_waves(window, tmp_path):
    document = window.editor.document
    waves = [make_wave("TickPositions", [0., 1., 2.]), make_wave("TickLabels", ["A", "B", "C"]), make_wave("BadLabels", ["A"])]
    window.editor.commit(document.current.clone(), put_waves(document, waves), "Synthetic waves")
    before = window.graph()
    with pytest.raises(ValueError, match="same number"):
        window.apply_properties("axis", "bottom", {"tick_mode": "waves", "tick_locations": "root:TickPositions", "tick_labels": "root:BadLabels"})
    assert window.graph() == before
    window.apply_properties("axis", ("left", "bottom"), {"tick_mode": "waves", "tick_locations": "root:TickPositions", "tick_labels": "root:TickLabels"})
    path = tmp_path / "ticks.mdsg"
    save_graph_document(path, document)
    restored = load_graph_document(path)
    assert all(a.style.user_ticks == ("root:TickPositions", "root:TickLabels") for a in restored.graph(before.name).axes if a.name in {"left", "bottom"})
    np.testing.assert_array_equal(restored.render_source().wave("root:TickLabels").data, ["A", "B", "C"])
    window.apply_properties("axis", "bottom", {"tick_mode": "auto"})
    assert next(a for a in window.graph().axes if a.name == "bottom").style.user_ticks is None


def test_new_from_auto_ticks_creates_two_waves_and_one_command(window):
    before = window.editor.history.count()
    window.create_user_ticks(("bottom",))
    assert window.editor.history.count() == before + 1
    axis = next(a for a in window.graph().axes if a.name == "bottom")
    numeric, text = [window.editor.document.render_source().wave(ref) for ref in axis.style.user_ticks]
    assert numeric.data.dtype.kind == "f" and text.data.dtype.kind == "U"
    assert len(numeric.data) == len(text.data) > 0

