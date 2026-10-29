"""Validate private local PXP samples without printing scientific values."""

from __future__ import annotations

import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from materials_data_studio.tools.graph_document import (
    EditableGraphDocument,
    load_graph_document,
    save_graph_document,
)
from materials_data_studio.tools.graph_render import IgorMatplotlibRenderer
from materials_data_studio.tools.pxp_backend import Igor2Backend
from materials_data_studio.tools.pxp_render import render_graph


def validate(path: Path) -> None:
    document = Igor2Backend().open(path)
    graph_issues = sum(len(graph.issues) for graph in document.graphs)
    for graph in document.graphs:
        figure = Figure()
        FigureCanvasAgg(figure)
        render_graph(figure, document, graph)
        figure.canvas.draw()
    print(f"{path.name}: status={document.status}; waves={len(document.waves)}; "
          f"graphs={len(document.graphs)}; full={sum(graph.status == 'Fully supported' for graph in document.graphs)}; "
          f"partial={sum(graph.status == 'Partially supported' for graph in document.graphs)}; "
          f"images={sum(len(graph.images) for graph in document.graphs)}; "
          f"tables={len(document.tables)}; procedures={len(document.procedures)}; "
          f"unsupported_commands={graph_issues}; warnings={len(document.issues)}")
    if document.status == "OPEN_FAILED":
        raise RuntimeError(f"Could not read {path.name}")
    editable = EditableGraphDocument.from_pxp(document)
    # Exercise semantic edits, logical positions and storage using the real
    # local examples; no scientific values or screenshots are published.
    edited = []
    for graph in editable.current.graphs:
        axes = tuple(replace(axis, style=replace(axis.style, font_size=16, major_length=5,
                                                 manual_ticks=(0, 2, 0, 2) if not axis.style.log else axis.style.manual_ticks))
                     for axis in graph.axes)
        traces = tuple(replace(trace, line_width=2) for trace in graph.traces)
        images = tuple(replace(image, color_table="magma", interpolation=True, colorbar_visible=True)
                       for image in graph.images)
        edited.append(replace(graph, axes=axes, traces=traces, images=images))
        editable.current.layout[graph.name] = {"labels": {"bottom": {"coordinate_system": "axis_label", "offset_points": 34, "lateral": 0.45}}}
    editable.current.graphs = tuple(edited)
    with tempfile.TemporaryDirectory(prefix="mds-private-graph-", dir=Path(".test-tmp")) as directory:
        saved = Path(directory) / "validation.mdsg"
        save_graph_document(saved, editable)
        restored = load_graph_document(saved)
        if restored.current != editable.current or restored.source.source_bytes != document.source_bytes:
            raise RuntimeError("Graph document roundtrip changed canonical state")
        for graph in restored.current.graphs:
            figure = Figure()
            FigureCanvasAgg(figure)
            IgorMatplotlibRenderer().render(figure, restored, graph.name)
            figure.canvas.draw()
    print("  MDSG edited styles, logical label positions, embedded source and all graph renders: OK")


if __name__ == "__main__":
    for filename in sys.argv[1:]:
        validate(Path(filename))
