"""Reflection indices stay editable text in both graph transfer routes."""
import re
from dataclasses import replace

import h5py
import numpy as np
import pytest
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from materials_data_studio.core.models import PlotStyle
from materials_data_studio.plotting.igor_export import graph_export_state_from_plot_spec
from materials_data_studio.plotting.igor_h5xp import IgorH5xpExporter
from materials_data_studio.plotting.plot_spec import CurveSpec, PlotSpec
from materials_data_studio.tools.graph_document import load_graph_document, save_graph_document
from materials_data_studio.tools.graph_h5xp import load_h5xp, save_h5xp
from materials_data_studio.tools.graph_render import IgorMatplotlibRenderer
from materials_data_studio.tools.graph_workflows import from_plot_spec


def spec():
    return PlotSpec("2theta", "Intensity", [CurveSpec(np.array([30., 40.]), np.array([100., 60.]), "Phase markers",
                    PlotStyle(marker="none", line_style="none", marker_edge_width=.4),
                    point_labels=("NaCl, 002", "NaCl, 110"), label_font="Arial", label_size=12,
                    label_color="#123456", label_angle=90)], y_range=(0, 150))


def test_graph_editor_labels_edit_render_and_roundtrip(tmp_path):
    doc = from_plot_spec(spec())
    graph = doc.graph("MDS_Plot")
    labels = [a for a in graph.annotations if a.trace]
    assert [a.raw for a in labels] == ["NaCl, 002", "NaCl, 110"]
    assert labels[0].point == 0 and labels[0].trace == graph.traces[0].name
    assert labels[0].style.rotation == 90 and labels[0].style.anchor == "MB"
    assert graph.traces[0].line_width == 0 and graph.traces[0].marker_thickness == .4
    figure = Figure()
    FigureCanvasAgg(figure)
    mapping = IgorMatplotlibRenderer().render(figure, doc, graph.name)
    figure.canvas.draw()
    frames = [artist for artist, (kind, name) in mapping.items() if kind == "annotation" and name == labels[0].name]
    assert len(frames) == 1
    text = frames[0].text
    box = text.get_window_extent(figure.canvas.get_renderer())
    anchor = text.get_transform().transform(text.get_position())
    assert (box.x0 + box.x1) / 2 == pytest.approx(anchor[0], abs=1.)
    assert box.y0 == pytest.approx(anchor[1], abs=1.)
    changed = replace(labels[0], raw="Edited 002")
    doc.current.graphs = (replace(graph, annotations=tuple(changed if a is labels[0] else a for a in graph.annotations)),)
    for extension, save, load in (("mdsg", save_graph_document, load_graph_document), ("h5xp", save_h5xp, load_h5xp)):
        path = tmp_path / ("labels." + extension)
        save(path, doc)
        restored = load(path)
        assert restored.graph(graph.name).annotations[0].raw == "Edited 002"
    with h5py.File(tmp_path / "labels.h5xp") as handle:
        commands = handle["Recreation/Recreation Procedures"].asstr()[()]
    assert "Tag/N=MDSIndex0/A=MB" in commands and "/O=90" in commands and "/G=(4626,13364,22102)" in commands
    match = re.search(r"mode\(([^)]+)\)", commands)
    assert match is not None
    native = match[1]
    assert f" {native},0," in commands and " curve1,0," not in commands
    assert "\\\\s(curve1)" not in commands


def test_renamed_repeated_traces_keep_tag_and_legend_references(tmp_path):
    doc = from_plot_spec(spec())
    graph = doc.graph("MDS_Plot")
    duplicate = replace(graph.traces[0], name="repeat")
    label = replace(graph.annotations[0], name="DuplicateIndex", trace="repeat", point=1)
    doc.current.graphs = (replace(graph, traces=(*graph.traces, duplicate), annotations=(*graph.annotations, label)),)
    path = tmp_path / "repeat.h5xp"
    save_h5xp(path, doc)
    with h5py.File(path) as handle:
        commands = handle["Recreation/Recreation Procedures"].asstr()[()]
    match = re.search(r"mode\(([^)]+)\)", commands)
    assert match is not None
    native = match[1]
    assert f" {native}#1,1," in commands


def test_editor_rotation_keeps_the_same_text_start_and_native_anchor(tmp_path):
    from materials_data_studio.plotting.igor_export import label_anchor
    doc = from_plot_spec(spec())
    graph = doc.graph("MDS_Plot")
    starts = []
    for angle in (0., 45., 90., 135., -45.):
        doc.current.layout[graph.name]["annotations"]["MDSIndex0"]["rotation"] = angle
        figure = Figure(figsize=(6, 4), dpi=100)
        FigureCanvasAgg(figure)
        mapping = IgorMatplotlibRenderer().render(figure, doc, graph.name)
        figure.canvas.draw()
        text = next(artist.text for artist, (kind, name) in mapping.items() if kind == "annotation" and name == "MDSIndex0")
        anchor = text.get_transform().transform(text.get_position())
        delta = np.asarray(text._pieces(figure.canvas.get_renderer())[0].get_position()) - anchor
        rad = np.deg2rad(angle)
        starts.append([delta[0] * np.cos(rad) + delta[1] * np.sin(rad), -delta[0] * np.sin(rad) + delta[1] * np.cos(rad)])
        path = tmp_path / (str(angle) + ".h5xp")
        save_h5xp(path, doc)
        with h5py.File(path) as handle:
            commands = handle["Recreation/Recreation Procedures"].asstr()[()]
        assert f"Tag/N=MDSIndex0/A={label_anchor(angle)}" in commands
    assert np.asarray(starts) == pytest.approx(np.tile(starts[0], (len(starts), 1)), abs=1e-6)


@pytest.mark.parametrize("color", ["#123456", "red"])
def test_direct_igor_export_has_literal_trace_attached_labels(tmp_path, color):
    source = spec()
    source.curves[0].label_color = color
    state = graph_export_state_from_plot_spec(source, "XRD")
    assert state.texts[0].text == "NaCl, 002" and state.texts[0].point == 0
    path = tmp_path / "direct.h5xp"
    IgorH5xpExporter().export_graph(state, path)
    with h5py.File(path) as handle:
        commands = handle["Recreation/Recreation Procedures"].asstr()[()]
    assert commands.count("Tag/N=MDSIndex") == 2
    assert "/A=MB/F=0/B=1/L=0/O=90" in commands
    assert "NaCl, 002" in commands and "Arial" in commands
    assert "lSize(Phase_markers_Y)=0" in commands
