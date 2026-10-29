"""Synthetic-only editor/storage regressions; research fixtures never enter Git."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from materials_data_studio.tools.graph_document import (
    EditableGraphDocument,
    GraphEditorController,
    load_graph_document,
    save_graph_document,
)
from materials_data_studio.tools.graph_render import IgorMatplotlibRenderer, label_position
from materials_data_studio.tools.graph_window import GraphEditorWindow
from materials_data_studio.tools.pxp_model import PxpDocument, PxpWave
from materials_data_studio.tools.pxp_recreation import parse_recreation


@pytest.fixture
def source() -> PxpDocument:
    raw = b"Generated source, not a research experiment"
    text = r'''Window XY() : Graph
Display /W=(0,0,450,350) y vs x
ModifyGraph width=240,height=180,tick=2,mirror=1,mode=4,marker=8,fSize=12
ModifyGraph manTick(bottom)={0,2,0,1},manMinor(bottom)={3,0}
Label left "\Z16\f02I\f00 (a.u.)"
Label bottom "Position"
SetAxis bottom 0,6
ErrorBars y Y,wave=(err,err)
Legend/C/N=legend/F=0/B=1/A=RT/X=-3/Y=-3 "\s(y) Signal"
TextBox/C/N=note/F=0/B=1/A=LT/X=3/Y=-3 "Generated data"
EndMacro
Window Map() : Graph
Display
AppendImage map
ModifyImage map ctab={1,12,VioletOrangeYellow,0},log=1
ModifyGraph width=180,height=180
ColorScale/C/N=text0 image=map
AppendText "Intensity"
EndMacro'''
    graphs, tables = parse_recreation(text)
    waves = []
    for name, data in (("x", np.arange(1., 5.)), ("y", np.array([2., 4., 3., 5.])),
                       ("err", np.array([0.1, 0.2, 0.3, 0.4])),
                       ("map", np.arange(1, 13, dtype=np.float32).reshape(4, 3))):
        data.setflags(write=False)
        waves.append(PxpWave(name, f"root:{name}", name, data.dtype.str, data.shape,
                             data, ((0., 1.),) * data.ndim, ("",) * data.ndim, "", ""))
    return PxpDocument("source", "synthetic.pxp", hashlib.sha256(raw).hexdigest(), len(raw),
                       "10", "igor2", "0.5.13", tuple(waves), graphs=graphs, tables=tables,
                       status="FULL", source_bytes=raw, recreation_raw=text)


@pytest.fixture
def window(qtbot: object, source: PxpDocument) -> GraphEditorWindow:
    result = GraphEditorWindow()
    qtbot.addWidget(result)  # type: ignore[attr-defined]
    result.bind_document(source)
    result._discard_approved = True
    result.show()
    result.canvas.draw()
    return result


def test_conversion_render_and_roundtrip(source: PxpDocument, tmp_path: Path) -> None:
    document = EditableGraphDocument.from_pxp(source)
    path = tmp_path / "saved.mdsg"
    save_graph_document(path, document)
    restored = load_graph_document(path)
    assert restored.source.source_bytes == source.source_bytes
    assert restored.source.recreation_raw == source.recreation_raw
    assert restored.current.graphs == source.graphs
    assert restored.fidelity("XY") == "Original"
    for old, new in zip(source.waves, restored.source.waves, strict=True):
        assert new.dtype == old.dtype and new.shape == old.shape
        assert not new.data.flags.writeable
        np.testing.assert_array_equal(old.data, new.data)
    with zipfile.ZipFile(path) as archive:
        assert set(archive.namelist()) == {"manifest.json", "document.json", "arrays/data.npz", "source/original.pxp"}
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["format_version"] == 5 and manifest["source_type"] == "igor_pxp"
    for graph in restored.current.graphs:
        figure = Figure()
        FigureCanvasAgg(figure)
        mapping = IgorMatplotlibRenderer().render(figure, restored, graph.name)
        figure.canvas.draw()
        assert mapping and not figure.get_constrained_layout()


def test_saved_clean_undo_redo(source: PxpDocument, tmp_path: Path) -> None:
    controller = GraphEditorController(EditableGraphDocument.from_pxp(source))
    assert controller.dirty
    controller.save(tmp_path / "undo.mdsg")
    assert not controller.dirty
    graph = controller.document.graph("XY")
    controller.edit_graph(replace(graph, traces=(replace(graph.traces[0], line_width=3),)), "Trace")
    assert controller.dirty and controller.document.fidelity("XY") == "Modified"
    controller.history.undo()
    assert not controller.dirty and controller.document.graph("XY") == graph
    controller.history.redo()
    assert controller.dirty


@pytest.mark.parametrize("kind,identifier,changes", [
    ("axis", "bottom", {"major_interval": 1.5, "minor_count": 2, "tick": 1, "thickness": 2}),
    ("axis", "left", {"label": "Changed", "font_size": 18, "font_family": "Arial", "offset_points": 34, "lateral": 0.4}),
    ("trace", "y", {"color": "#123456", "line_width": 2.5, "line_style": 2, "marker": 5, "marker_size": 4, "marker_fill": "none", "marker_edge": "#abcdef"}),
    ("annotation", "legend", {"x": 0.75, "y": 0.8, "font_size": 16, "font": "Arial", "visible": False}),
    ("annotation", "note", {"raw": "Changed text", "x": 0.2, "y": 0.6, "color": "#990011", "rotation": 20}),
    ("geometry", "XY", {"width_points": 300, "height_points": 200, "margin_right": 70}),
])
def test_property_edit_reload_undo(window: GraphEditorWindow, source: PxpDocument, tmp_path: Path,
                                  kind: str, identifier: str, changes: dict) -> None:
    editor = window.editor
    assert editor is not None
    initial = editor.document.current.clone()
    window.apply_properties(kind, identifier, changes)
    assert editor.history.count() == 1 and editor.document.current != initial
    assert source.graphs == editor.document.source.graphs
    window.canvas.draw()
    path = tmp_path / "edited.mdsg"
    editor.save(path)
    loaded = load_graph_document(path)
    assert loaded.current == editor.document.current
    assert loaded.fidelity("XY") == "Modified"
    editor.history.undo()
    assert editor.document.current == initial
    editor.history.redo()
    assert editor.document.current == loaded.current


def test_image_colorscale_edit_reload(window: GraphEditorWindow, tmp_path: Path) -> None:
    window.tree.setCurrentItem(window.tree.topLevelItem(0).child(1).child(1))
    window.apply_properties("image", "map", {"color_table": "magma", "minimum": 2, "maximum": 10,
                                               "log_color": True, "interpolation": True, "colorbar_visible": True})
    window.apply_properties("colorbar", "map", {"x": 0.6, "y": 0.3, "width": 18, "height": 100,
                                                  "colorbar_label_raw": "Edited", "font": "Arial", "label_size": 17})
    window.canvas.draw()
    assert window.figure.axes[0].images[0].get_interpolation() == "bilinear"
    assert window.figure.axes[0].images[0].norm.vmin == 2
    assert window.editor is not None
    path = tmp_path / "map.mdsg"
    window.editor.save(path)
    restored = load_graph_document(path)
    assert restored.current == window.editor.document.current
    assert restored.graph("Map").images[0].colorbar_style.width == 18


@pytest.mark.parametrize("target", [("label", "left"), ("label", "bottom"), ("annotation", "legend"), ("annotation", "note")])
def test_drag_is_single_command_and_logical(window: GraphEditorWindow, target: tuple[str, str]) -> None:
    window.canvas.draw()
    artist = next(a for a, value in window.artist_targets.items() if value == target)
    box = artist.get_window_extent(window.canvas.get_renderer())
    x, y = (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2
    event = SimpleNamespace(button=1, dblclick=False, x=x, y=y, inaxes=None)
    window.pointer_press(event)
    assert window._drag is not None
    assert window.editor is not None
    before = window.editor.document.current.clone()
    for step in range(1, 6):
        window.pointer_move(SimpleNamespace(x=x + step * 4, y=y + step * 3))
    assert window.editor.history.count() == 0
    window.pointer_release(event)
    assert window.editor.history.count() == 1
    after = window.editor.document.current.clone()
    assert after != before
    if target[0] == "label":
        axis = next(a for a in after.graphs[0].axes if a.name == target[1])
        assert axis.style.label_position_mode == 3
        assert axis.style.label_position is not None
        assert axis.style.label_lateral_position is not None
        assert target[1] not in after.layout.get("XY", {}).get("labels", {})
    window.editor.history.undo()
    assert window.editor.document.current == before
    window.editor.history.redo()
    assert window.editor.document.current == after


def test_label_coordinate_conversion_invariant_to_dpi() -> None:
    results = []
    for dpi in (100, 200):
        figure = Figure(figsize=(6, 4), dpi=dpi)
        ax = figure.add_axes((0.2, 0.2, 0.6, 0.6))
        x, y = ax.transAxes.transform((0.4, 0))
        results.append(label_position(ax, True, x, y - 34 * dpi / 72, dpi, 1, "bottom"))
    assert results[0] == results[1]
    assert results[0]["offset_points"] == pytest.approx(34)


@pytest.mark.parametrize("changes", [{"minimum": 3}, {"minimum": 1, "maximum": 1}, {"major_interval": -1}, {"font_size": float("nan")}])
def test_invalid_axis_edits_do_not_change_document(window: GraphEditorWindow, changes: dict) -> None:
    assert window.editor is not None
    initial = window.editor.document.current.clone()
    with pytest.raises(ValueError):
        window.apply_properties("axis", "left", changes)
    assert window.editor.document.current == initial
    assert not window.editor.history.canUndo()


def test_atomic_failure_preserves_previous_document(source: PxpDocument, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "atomic.mdsg"
    document = EditableGraphDocument.from_pxp(source)
    save_graph_document(path, document)
    original = path.read_bytes()
    def fail(*_args: object) -> None:
        raise OSError("simulated replace failure")
    monkeypatch.setattr("materials_data_studio.tools.graph_document.os.replace", fail)
    with pytest.raises(OSError):
        save_graph_document(path, document)
    assert path.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("extension", [".pxp", ".mdss", ".zip"])
def test_save_refuses_source_or_session(extension: str, source: PxpDocument, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        save_graph_document(tmp_path / ("source" + extension), EditableGraphDocument.from_pxp(source))


def test_reject_future_version_and_corrupt_source(source: PxpDocument, tmp_path: Path) -> None:
    path = tmp_path / "source.mdsg"
    save_graph_document(path, EditableGraphDocument.from_pxp(source))
    with zipfile.ZipFile(path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    for change in ("future", "corrupt"):
        modified = dict(members)
        if change == "future":
            manifest = json.loads(modified["manifest.json"])
            manifest["format_version"] = 999
            modified["manifest.json"] = json.dumps(manifest).encode()
        else:
            modified["source/original.pxp"] = b"wrong source"
        with zipfile.ZipFile(path, "w") as archive:
            for name, content in modified.items():
                archive.writestr(name, content)
        with pytest.raises(ValueError):
            load_graph_document(path)


def test_independent_of_workspace_and_recovery(window: GraphEditorWindow, qtbot: Any) -> None:
    assert window.owner is None and window.editor is not None
    window.autosave()
    qtbot.waitUntil(lambda: not getattr(window, "_autosave_running", False), timeout=10000)
    assert window.recovery_path is not None and window.recovery_path.exists()
    assert load_graph_document(window.recovery_path).current == window.editor.document.current
    window._remove_recovery()
    assert not window.recovery_path.exists()


def test_properties_button_only_changes_requested_fields(window: GraphEditorWindow) -> None:
    assert window.editor is not None
    before = window.editor.document.graph("XY")
    record = window.property_widgets["axis"]
    record["selector"].setCurrentText("left")
    text, _parser = record["widgets"]["label"]
    text.setText("Edited label")
    window.select_target("axis", "left")
    window.apply_button.click()
    after = window.editor.document.graph("XY")
    assert next(a for a in after.axes if a.name == "left").label_raw == "Edited label"
    assert after.axes == tuple(replace(a, label_raw="Edited label") if a.name == "left" else a for a in before.axes)
    assert after.traces == before.traces


def test_image_colorbar_drag(window: GraphEditorWindow) -> None:
    window.tree.setCurrentItem(window.tree.topLevelItem(0).child(1).child(1))
    window.canvas.draw()
    artist = next(a for a, target in window.artist_targets.items() if target == ("colorbar", "map"))
    box = artist.bbox
    event = SimpleNamespace(button=1, dblclick=False, x=(box.x0 + box.x1) / 2,
                            y=(box.y0 + box.y1) / 2, inaxes=artist)
    window.pointer_press(event)
    assert window._drag is not None
    window.pointer_move(SimpleNamespace(x=event.x + 15, y=event.y + 20))
    window.pointer_release(event)
    assert window.editor is not None
    assert window.editor.history.count() == 1
    assert "position" in window.editor.document.current.layout["Map"]["colorbars"]["map"]


def test_text_add_and_doubleclick_edit(window: GraphEditorWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("materials_data_studio.tools.graph_window.QInputDialog.getMultiLineText",
                        lambda *_args: ("Added text", True))
    window.add_text()
    assert window.editor is not None
    assert window.editor.document.graph("XY").annotations[-1].raw == "Added text"
    window.edit_text("annotation", "note")
    assert next(a for a in window.editor.document.graph("XY").annotations if a.name == "note").raw == "Added text"
    window.edit_text("label", "bottom")
    assert next(a for a in window.editor.document.graph("XY").axes if a.name == "bottom").label_raw == "Added text"


def test_navigation_no_history_and_selection(window: GraphEditorWindow) -> None:
    assert window.editor is not None
    for mode in ("Pan", "Zoom", "Pointer", "Text", "Pointer"):
        window.set_mode(mode)
        assert window.mode == mode
    assert not window.editor.history.canUndo()
    for kind, identifier in (("trace", "y"), ("axis", "left"), ("annotation", "note")):
        window.select_target(kind, identifier)
        assert window.property_widgets[kind]["selector"].currentText() == identifier


def test_mdsg_open_never_invokes_pxp_parser(window: GraphEditorWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert window.editor is not None
    path = tmp_path / "parser-free.mdsg"
    window.editor.save(path)
    monkeypatch.setattr(window.backend, "open", lambda *_args: pytest.fail("PXP parser called"))
    monkeypatch.setattr("materials_data_studio.tools.graph_window.QMessageBox.warning", lambda _parent, _title, text: pytest.fail(text))
    window.open_graph_path(path)
    assert window.editor.path == path and not window.editor.dirty


@pytest.mark.parametrize("extension", ["png", "svg", "pdf"])
def test_edited_figure_export_geometry(window: GraphEditorWindow, tmp_path: Path, extension: str) -> None:
    assert window.editor is not None
    window.apply_properties("axis", "bottom", {"offset_points": 34, "lateral": 0.45})
    graph = window.editor.document.graph("XY")
    figure = Figure(figsize=window.renderer.export_size(graph), dpi=200)
    FigureCanvasAgg(figure)
    window.renderer.render(figure, window.editor.document, graph.name)
    figure.canvas.draw()
    path = tmp_path / ("figure." + extension)
    figure.savefig(path)
    assert path.stat().st_size > 500
    native = window.renderer.export_size(graph)
    np.testing.assert_allclose(figure.get_size_inches(), native)


def test_source_basename_portable(source: PxpDocument, tmp_path: Path) -> None:
    source = replace(source, basename=r"C:\Users\private\synthetic.pxp")
    path = tmp_path / "portable.mdsg"
    save_graph_document(path, EditableGraphDocument.from_pxp(source))
    assert load_graph_document(path).source.basename == "synthetic.pxp"


def test_hide_all_annotations_does_not_restore_legacy_legend(window: GraphEditorWindow) -> None:
    for name in ("legend", "note"):
        window.apply_properties("annotation", name, {"visible": False})
    window.canvas.draw()
    assert not any(kind in {"annotation", "legend"} for kind, _ in window.artist_targets.values())


def test_editor_reopens_without_igor2_import(source: PxpDocument, tmp_path: Path) -> None:
    path = tmp_path / "no-parser.mdsg"
    save_graph_document(path, EditableGraphDocument.from_pxp(source))
    script = f'''
import importlib.abc
import sys
from pathlib import Path
class BlockIgor(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == "igor2" or fullname.startswith("igor2."):
            raise ModuleNotFoundError("Igor parser intentionally unavailable")
sys.meta_path.insert(0, BlockIgor())
from PySide6.QtWidgets import QApplication
from materials_data_studio.tools.graph_window import GraphEditorWindow
app = QApplication([])
window = GraphEditorWindow()
window.open_graph_path(Path({str(path)!r}))
assert window.editor is not None and not window.editor.dirty
assert window.graph().name == "XY"
window.close()
'''
    # Import-blocking is the assertion; allow startup contention during the
    # Windows build/full-suite runs without changing application behavior.
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                            check=False, timeout=90)
    assert result.returncode == 0, result.stderr
