"""Native numerical tick ranges and explicit styles, with generated waves only."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Qt

from materials_data_studio.tools.graph_document import (
    EditableGraphDocument,
    load_graph_document,
    save_graph_document,
)
from materials_data_studio.tools.graph_render import IgorMatplotlibRenderer
from materials_data_studio.tools.graph_waves import make_wave
from materials_data_studio.tools.pxp_model import PxpDocument
from materials_data_studio.tools.pxp_recreation import parse_recreation
from materials_data_studio.tools.pxp_render import _style
from materials_data_studio.tools.pxp_snapshot import decode_document, encode_document


def document(commands: str, *, image: bool = False) -> EditableGraphDocument:
    graphs, _tables = parse_recreation("Window Synthetic() : Graph\n" +
                                      ("AppendImage image\n" if image else "Display y vs x\n") +
                                      commands + "\nEndMacro")
    waves = [make_wave("x", np.arange(11.)), make_wave("y", np.arange(11.) - 5),
             make_wave("image", np.arange(121.).reshape(11, 11)),
             make_wave("tickPositions", [0., 2., 3., 4., 6., 8.]),
             make_wave("tickLabels", [["zero", "Major"], ["two", "Major"], ["three", "Minor"],
                                      ["four", "Major"], ["six", "Major"], ["eight", "Major"]])]
    source = PxpDocument("generated", "synthetic.mdsg", "", 0, "10", "synthetic", "",
                         waves=tuple(waves), graphs=graphs, status="FULL")
    return EditableGraphDocument.from_pxp(source)


def render(current: EditableGraphDocument) -> tuple[Figure, Any]:
    figure = Figure(figsize=(6, 4), dpi=100)
    FigureCanvasAgg(figure)
    IgorMatplotlibRenderer().render(figure, current, "Synthetic")
    figure.canvas.draw()
    return figure, figure.axes[0]


def test_supported_native_axis_options_are_saved_and_invalid_values_are_reported(tmp_path: Path) -> None:
    current = document("ModifyGraph standoff=0,zero=2,gridHair=1,lowTrip=0.01,highTrip=10000,notation=1,ZisZ=1\n"
                       "ModifyGraph tickEnab(bottom)={0,inf},gfSize=18")
    graph = current.graph("Synthetic")
    assert not graph.issues
    style = _style(graph, "bottom")
    assert style.standoff is False and style.zero_line == 2 and style.grid_hair == 1
    assert style.low_trip == .01 and style.high_trip == 10000 and style.notation == 1
    assert style.zero_is_zero and style.tick_enable == (0, np.inf)
    assert graph.style.font_size == 18
    target = tmp_path / "styles.mdsg"
    save_graph_document(target, current)
    assert load_graph_document(target).current.graphs == current.current.graphs
    metadata, arrays = encode_document(current.render_source())
    assert '"inf"' in json.dumps(metadata, allow_nan=False)
    assert decode_document(metadata, arrays).graphs == current.current.graphs
    for option in ("standoff=2", "zero=19", "gridHair=4", "lowTrip=-1", "notation=2", "ZisZ=2",
                   "tickEnab={4,2}", "tickEnab={nan,4}", "gfSize=2"):
        invalid = document("ModifyGraph " + option)
        assert invalid.graph("Synthetic").issues, option


def test_tick_range_and_individual_colors_do_not_clip_data_or_change_limits() -> None:
    current = document("SetAxis bottom 0,10\nSetAxis left -5,5\n"
                       "ModifyGraph manTick(bottom)={0,2,0,0},manMinor(bottom)={1,0},minor(bottom)=1,mirror(bottom)=3\n"
                       "ModifyGraph tickEnab(bottom)={2,6},tickRGB(bottom)=(1000,1000,1000),tlblRGB(bottom)=(0,40000,0)")
    graph = current.graph("Synthetic")
    axes = tuple(replace(axis, style=replace(axis.style, major_tick_color=(65535, 0, 0),
                                           minor_tick_color=(0, 0, 65535))) if axis.name == "bottom" else axis for axis in graph.axes)
    current.current.graphs = (replace(graph, axes=axes),)
    _figure, ax = render(current)
    assert ax.get_xlim() == (0, 10) and ax.get_ylim() == (-5, 5)
    np.testing.assert_array_equal(ax.xaxis.get_majorticklocs(), [2, 4, 6])
    np.testing.assert_array_equal(ax.xaxis.get_minorticklocs(), [3, 5])
    line = next(line for line in ax.lines if str(line.get_gid()).startswith("trace:"))
    np.testing.assert_array_equal(line.get_xdata(), np.arange(11.))
    for tick in ax.xaxis.get_major_ticks():
        assert tick.tick1line.get_color() == (1., 0., 0.)
        assert tick.tick2line.get_color() == (1., 0., 0.)
        assert tick.label1.get_color() == pytest.approx((0, 40000 / 65535, 0))
    assert ax.xaxis.get_minor_ticks()[0].tick1line.get_color() == (0., 0., 1.)
    ax.set_xlim(4, 10)
    ax.figure.canvas.draw()
    assert all(2 <= value <= 6 for value in ax.xaxis.get_majorticklocs())


@pytest.mark.parametrize("image", [False, True])
def test_user_ticks_keep_their_matching_text_inside_range(image: bool) -> None:
    current = document("SetAxis bottom 0,10\n"
                       "ModifyGraph userticks(bottom)={tickPositions,tickLabels},tickEnab(bottom)={2,6}", image=image)
    _figure, ax = render(current)
    np.testing.assert_array_equal(ax.xaxis.get_majorticklocs(), [2, 4, 6])
    np.testing.assert_array_equal(ax.xaxis.get_minorticklocs(), [3])
    assert [label.get_text() for label in ax.get_xticklabels()] == ["two", "four", "six"]
    assert ax.get_xlim() == (0, 10)


def test_log_reversed_multiple_axes_use_numerical_not_fractional_tick_ranges() -> None:
    current = document("AppendToGraph/R y vs x\nSetAxis bottom 1,1000\nSetAxis left -5,5\n"
                       "ModifyGraph log(bottom)=1,reverse(bottom)=1,tickEnab(bottom)={10,100},tickEnab(right)={-2,2}")
    figure, ax = render(current)
    assert ax.get_xlim() == (1000, 1)
    assert ax.get_xscale() == "log"
    assert len(figure.axes) == 2
    for artist in figure.axes:
        assert all(10 <= value <= 100 for value in artist.xaxis.get_majorticklocs())
    right = next(artist for artist in figure.axes if artist.get_gid() == "plotaxis:right")
    assert all(-2 <= value <= 2 for value in right.yaxis.get_majorticklocs())


def test_global_tick_range_can_be_overridden_with_unbounded_axis_range(tmp_path: Path) -> None:
    current = document("SetAxis bottom 0,10\nSetAxis left -5,5\nModifyGraph tickEnab={2,4}\n"
                       "ModifyGraph tickEnab(left)={-inf,inf}")
    target = tmp_path / "unbounded.mdsg"
    save_graph_document(target, current)
    restored = load_graph_document(target)
    _figure, ax = render(restored)
    assert all(2 <= value <= 4 for value in ax.xaxis.get_majorticklocs())
    assert any(value < 2 for value in ax.yaxis.get_majorticklocs())


@pytest.mark.parametrize("log", [False, True])
def test_tick_enable_does_not_change_automatic_limits_after_relim(log: bool) -> None:
    settings = "ModifyGraph log(bottom)=1\n" if log else ""
    _figure, reference = render(document(settings))
    _filtered, filtered = render(document(settings + "ModifyGraph tickEnab(bottom)={2,4}"))
    for artist in (reference, filtered):
        artist.relim()
        artist.autoscale_view()
        artist.figure.canvas.draw()
    assert filtered.get_xlim() == reference.get_xlim()
    assert filtered.get_ylim() == reference.get_ylim()


@pytest.mark.parametrize("bounds", [(.5, 2.5), (.5, 1.5)])
def test_automatic_minor_ticks_use_unfiltered_major_distribution(bounds: tuple[float, float]) -> None:
    settings = "SetAxis bottom 0,10\nModifyGraph nticks(bottom)=5,minor(bottom)=1,manMinor(bottom)={4,0}\n"
    _figure, reference = render(document(settings))
    _filtered, filtered = render(document(settings + f"ModifyGraph tickEnab(bottom)={{{bounds[0]},{bounds[1]}}}"))
    original = reference.xaxis.get_minorticklocs()
    expected = original[(original >= bounds[0]) & (original <= bounds[1])]
    assert len(filtered.xaxis.get_majorticklocs()) <= 1
    assert len(expected) > 0
    np.testing.assert_allclose(filtered.xaxis.get_minorticklocs(), expected)
    assert filtered.get_xlim() == (0, 10)


def test_grid_hair_zero_line_notation_and_zero_label_have_visible_effects() -> None:
    current = document("SetAxis bottom 0,10\nSetAxis left -5,5\n"
                       "ModifyGraph axThick=2,grid=1,gridHair=1,minor=1,zero=1\n"
                       "ModifyGraph manTick(bottom)={0,2,0,2},ZisZ(bottom)=1")
    _figure, ax = render(current)
    assert next(tick for tick in ax.xaxis.get_major_ticks() if tick.get_loc() == 0).label1.get_text() == "0"
    assert next(tick for tick in ax.xaxis.get_major_ticks() if tick.get_loc() == 2).label1.get_text() == "2.00"
    assert ax.xaxis.get_major_ticks()[0].gridline.get_linewidth() == 1
    assert ax.xaxis.get_minor_ticks()[0].gridline.get_linewidth() == .5
    assert {line.get_gid() for line in ax.lines} >= {"zeroline:bottom", "zeroline:left"}
    scientific = document("SetAxis bottom 0,0.03\nModifyGraph lowTrip(bottom)=0.1,notation(bottom)=1")
    _figure, sci_ax = render(scientific)
    assert "10^{-2}" in sci_ax.xaxis.get_major_formatter()(.02)
    engineering = document("SetAxis bottom 0,0.03\nModifyGraph lowTrip(bottom)=0.1,notation(bottom)=0")
    _figure, eng_ax = render(engineering)
    assert "10^{-3}" in eng_ax.xaxis.get_major_formatter()(.02)


def test_graph_default_font_changes_automatic_text_but_not_explicit_axis_font() -> None:
    current = document('TextBox/N=automatic "Automatic annotation"\nModifyGraph gfSize=18,fSize(left)=11')
    figure, ax = render(current)
    assert ax.xaxis.label.get_fontsize() == 18
    assert ax.yaxis.label.get_fontsize() == 11
    annotation = next(artist for artist in figure.findobj() if artist.get_gid() == "annotation:automatic")
    assert annotation.text.get_fontsize() == 18
    quoted = document('AppendImage image\nModifyImage image ctab={0,100,\'Rainbow\',0}', image=True)
    assert quoted.graph("Synthetic").images[-1].color_table == "Rainbow"


def test_native_settings_survive_h5xp_roundtrip(tmp_path: Path) -> None:
    from materials_data_studio.tools.graph_h5xp import load_h5xp, save_h5xp

    current = document("ModifyGraph gfSize=16,zero=2,gridHair=3,lowTrip=0.001,notation=1\n"
                       "ModifyGraph tickEnab(bottom)={-inf,4}")
    current.source = replace(current.source, waves=tuple(wave for wave in current.source.waves if wave.data.dtype.kind != "U"))
    path = tmp_path / "settings.h5xp"
    save_h5xp(path, current)
    restored = load_h5xp(path)
    assert restored.current.graphs == current.current.graphs


def test_explicit_annotation_font_edit_does_not_inherit_graph_default(qtbot: Any) -> None:
    from materials_data_studio.tools.graph_annotation_dialog import AnnotationDialog
    from materials_data_studio.tools.graph_window import GraphEditorWindow

    current = document('TextBox/N=automatic "Text"\nModifyGraph gfSize=18')
    window = GraphEditorWindow()
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    qtbot.addWidget(window)
    window.bind_graph_document(current)
    dialog = AnnotationDialog(window, current, "Synthetic", "automatic")
    qtbot.addWidget(dialog)
    try:
        # The editor retains the semantic inherited sentinel rather than
        # replacing it with the currently resolved graph size.
        assert float(dialog.fields["font_size"][0].text()) == 0
        graph, _layout = dialog.updated()
        assert graph.annotations[0].style.inherit_graph_font
        figure, _ax = render(current)
        annotation = next(artist for artist in figure.findobj() if artist.get_gid() == "annotation:automatic")
        assert annotation.text.get_fontsize() == 18
        window.apply_properties("annotation", "automatic", {"font_size": 9})
        assert not current.graph("Synthetic").annotations[0].style.inherit_graph_font
        figure, _ax = render(current)
        annotation = next(artist for artist in figure.findobj() if artist.get_gid() == "annotation:automatic")
        assert annotation.text.get_fontsize() == 9
    finally:
        dialog.reject()
        window._discard_approved = True
        window.close()
