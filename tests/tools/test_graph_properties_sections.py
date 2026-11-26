"""Pinned inspector, atomic multi-axis edits and user-tick wave selection."""
from dataclasses import replace

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QScrollArea
from tests.tools.test_graph_property_lifecycle import editor_window, source  # noqa: F401

from materials_data_studio.tools.graph_document import load_graph_document, save_graph_document
from materials_data_studio.tools.graph_waves import make_wave, put_waves
from materials_data_studio.tools.pxp_model import PxpAxis, PxpAxisStyle
from materials_data_studio.tools.pxp_snapshot import decode_document, encode_document


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


def test_computed_manual_ticks_apply_reedit_render_and_roundtrip(window, tmp_path):
    window.apply_properties("axis", "bottom", {"tick_mode": "auto"})
    record = window.property_widgets["axis"]
    record["selector"].select_axes(("bottom",))
    mode = record["widgets"]["tick_mode"][0]
    mode.setCurrentIndex(mode.findData("manual"))
    record["apply"]()
    window.canvas.draw()
    axis = next(a for a in window.graph().axes if a.name == "bottom")
    assert axis.style.manual_ticks == (0., 1., 0, 0)
    window.apply_properties("axis", "bottom", {"major_interval": .25, "minor_count": 3})
    window.canvas.draw()
    assert next(a for a in window.graph().axes if a.name == "bottom").style.manual_ticks == (0., .25, 0, 2)
    path = tmp_path / "computed.mdsg"
    save_graph_document(path, window.editor.document)
    restored = load_graph_document(path)
    assert restored.graph(window.graph().name).axes == window.graph().axes
    window.editor.history.undo()
    window.canvas.draw()
    assert next(a for a in window.graph().axes if a.name == "bottom").style.manual_ticks == (0., 1., 0, 0)
    window.editor.history.redo()
    window.apply_properties("axis", "bottom", {"tick_mode": "auto"})
    assert next(a for a in window.graph().axes if a.name == "bottom").style.manual_ticks is None


def test_legacy_three_part_manual_ticks_restore_axis_and_defaults(window):
    metadata, payload = encode_document(window.editor.document.render_source())
    graph = metadata["graphs"][0]
    graph["style"]["axis_defaults"]["manual_ticks"] = [0, .5, 0]
    graph["axes"][0]["style"]["manual_ticks"] = [0, 2, 0]
    restored = decode_document(metadata, payload)
    assert restored.graphs[0].style.axis_defaults.manual_ticks == (0, .5, 0, 0)
    assert restored.graphs[0].axes[0].style.manual_ticks == (0, 2, 0, 0)
    window.bind_document(restored)
    window.canvas.draw()
    name = restored.graphs[0].axes[0].name
    window.apply_properties("axis", name, {"major_interval": .125})
    assert next(a for a in window.graph().axes if a.name == name).style.manual_ticks == (0, .125, 0, 3)


def test_manual_tick_options_commit_without_apply(window, qtbot):
    window.apply_properties("axis", "bottom", {"tick_mode": "auto"})
    before = window.editor.history.count()
    record = window.property_widgets["axis"]
    record["selector"].select_axes(("bottom",))
    widgets = record["widgets"]
    mode = widgets["tick_mode"][0]
    mode.setCurrentIndex(mode.findData("manual"))
    for key, value in (("canonical_tick", "2"), ("manual_exponent", "1"),
                       ("manual_digits", "3"), ("minor_emphasis", "5")):
        control = widgets[key][0]
        control.setText(value)
        qtbot.keyClick(control, Qt.Key.Key_Return)
    widgets["tick_thousands"][0].setChecked(True)
    style = next(a.style for a in window.graph().axes if a.name == "bottom")
    assert style.manual_ticks == (2., 1., 1, 3)
    assert style.manual_minor == (4, 5)
    assert style.tick_thousands
    assert window.editor.history.count() == before + 6

def test_axis_grid_and_label_options_render_and_save(window, tmp_path):
    window.apply_properties("axis", "bottom", {
        "label": "Distance", "grid": 2, "grid_from": 20, "grid_to": 80,
        "grid_color": "#225588", "grid_hair": 1, "no_label": 1, "prescale_exponent": 2,
    })
    window.canvas.draw()
    xaxis = window.figure.axes[0].xaxis
    assert xaxis.label.get_visible()
    assert not any(t.label1.get_visible() for t in xaxis.get_major_ticks())
    assert all(g.get_visible() for g in xaxis.get_gridlines())
    assert all(g.get_clip_box() is not None for g in xaxis.get_gridlines())
    graph = window.graph()
    path = tmp_path / "grid.mdsg"
    save_graph_document(path, window.editor.document)
    restored = load_graph_document(path)
    assert restored.graph(graph.name) == graph
    window.apply_properties("axis", "bottom", {"grid": 0, "no_label": 2})
    window.canvas.draw()
    xaxis = window.figure.axes[0].xaxis
    assert not xaxis.label.get_visible()
    assert not any(g.get_visible() for g in xaxis.get_gridlines())


def test_add_and_convert_annotation_keep_trace_attachment(window):
    from materials_data_studio.tools.graph_annotation_dialog import AnnotationTypeDialog

    doc = window.editor.document
    graph = window.graph()
    dialog = AnnotationTypeDialog(window, doc, graph.name)
    dialog.kind.setCurrentText("Tag")
    tag_editor = dialog.editor
    assert tag_editor is not None
    tag_editor.raw.setPlainText("Point")
    arrow_style = tag_editor.fields["arrow"][0]
    arrow_style.setCurrentIndex(arrow_style.findData("2"))
    tag_editor.fields["tag_head_length"][0].setText("9")
    tag_editor.fields["tag_standoff"][0].setText("2")
    tag_editor.fields["x_percent"][0].setText("8")
    tag_editor.fields["y_percent"][0].setText("10")
    modified, layout = tag_editor.updated()
    tag = modified.annotations[-1]
    assert tag.trace == graph.traces[0].name and tag.point == 0 and tag.kind == "Tag"
    assert tag.arrow == 2 and tag.style.tag_head_length == 9 and tag.style.tag_standoff == 2
    assert doc.graph(graph.name) == graph
    window.editor.edit_graph(modified, "Synthetic Tag", layout)
    window.canvas.draw()
    arrow = next(a for ax in window.figure.axes for a in ax.texts if a.get_gid() == "tag-arrow:" + tag.name)
    assert len(arrow.arrow_patch.get_path().vertices) > 3
    edit = AnnotationTypeDialog(window, doc, graph.name, tag.name)
    edit.kind.setCurrentText("TextBox")
    assert edit.editor is not None
    converted, _ = edit.editor.updated()
    assert not converted.annotations[-1].trace
    assert converted.annotations[-1].raw == "Point"
    assert len(converted.annotations) == len(modified.annotations)


def test_rename_folder_updates_references_without_copying_arrays(window):
    from materials_data_studio.tools.graph_waves import edit_data_folder, rename_wave

    document = window.editor.document
    document.current = rename_wave(document, "root:y", "root:folder:nested:y")
    wave = document.render_source().wave("root:folder:nested:y")
    assert wave is not None
    document.current = edit_data_folder(document, "root:folder:nested", "select")
    with pytest.raises(ValueError, match="Move or delete"):
        edit_data_folder(document, "root:folder", "delete")
    document.current = edit_data_folder(document, "root:folder", "rename", "renamed")
    renamed = document.render_source().wave("root:renamed:nested:y")
    assert renamed is not None and renamed.data is wave.data and renamed.id == wave.id
    assert next(t for t in document.graph("XY").traces if t.y_wave == renamed.igor_path)
    assert document.current.environment["current_folder"] == "root:renamed:nested"


def test_inspector_new_auto_ticks_ignores_old_manual_ticks(window):
    from PySide6.QtWidgets import QPushButton

    window.apply_properties("axis", "bottom", {"tick_mode": "manual", "canonical_tick": 0.2, "major_interval": 0.7})
    record = window.property_widgets["axis"]
    record["selector"].select_axes(("bottom",))
    control = record["widgets"]["tick_mode"][0]
    control.setCurrentIndex(control.findData("waves"))
    button = next(b for b in record["page"].findChildren(QPushButton) if b.text() == "New From Auto Ticks")
    assert button.isEnabled()
    button.click()
    axis = next(axis for axis in window.graph().axes if axis.name == "bottom")
    assert axis.style.manual_ticks is None
    positions = window.editor.document.render_source().wave(axis.style.user_ticks[0])
    assert not np.any(np.isclose(positions.data, .2))


def test_geometry_units_edit_in_inches_and_keep_igor_points_in_model(window):
    window.apply_properties("geometry", window.graph().name, {"dimension_unit": "in", "width_points": 4.5})
    assert window.graph().style.width_points == 324.
    fields = {key: value for key, _, value, _ in window._geometry_fields(window.graph().name)}
    assert fields["width_points"] == 4.5
    window.apply_properties("geometry", window.graph().name, {"dimension_unit": "cm"})
    fields = {key: value for key, _, value, _ in window._geometry_fields(window.graph().name)}
    assert fields["width_points"] == pytest.approx(11.43)
    assert window.graph().style.width_points == 324.
