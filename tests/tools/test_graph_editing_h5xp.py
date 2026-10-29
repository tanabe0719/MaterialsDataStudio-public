"""Synthetic regressions for drawing interaction, shared inputs and H5XP."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import h5py
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication, QComboBox
from tests.tools.test_graph_document import source as source_fixture
from tests.tools.test_graph_interaction import editor_window

from materials_data_studio.tools.graph_document import EditableGraphDocument
from materials_data_studio.tools.graph_drawing import DrawingDialog, drawing_operation
from materials_data_studio.tools.graph_h5xp import load_h5xp, save_h5xp
from materials_data_studio.tools.graph_handles import guided
from materials_data_studio.tools.graph_waves import make_wave
from materials_data_studio.tools.pxp_model import PxpDrawing, PxpTable
from materials_data_studio.tools.pxp_recreation import parse_recreation
from materials_data_studio.ui.style_inputs import ColorEdit, FontEdit, colormap_swatches

source = source_fixture
window = editor_window


def test_native_drawing_font_recreation_without_mds_metadata(source, tmp_path):
    document = EditableGraphDocument.from_pxp(source)
    graph = document.current.graphs[0]
    drawings = (
        PxpDrawing("DrawLine", (0.1, 0.2, 0.8, 0.9), font_family="Courier New"),
        PxpDrawing("DrawText", (0.2, 0.8), text="Synthetic label", font_family="Times New Roman", font_size=16),
    )
    document.current.graphs = (replace(graph, drawings=drawings),)
    path = tmp_path / "drawings.h5xp"
    save_h5xp(path, document)
    with h5py.File(path, "a") as handle:
        raw = handle["Recreation/Recreation Procedures"].asstr()[()]
        assert 'fname="Courier New"' in raw and 'fname="Times New Roman"' in raw
        assert ',font=' not in raw
        del handle["MDS Graph Document"]
    restored = load_h5xp(path).current.graphs[0].drawings
    assert [d.font_family for d in restored] == [d.font_family for d in drawings]
    assert restored[1].text == "Synthetic label" and restored[1].font_size == 16


def test_send_editor_document_exports_before_launch_and_preserves_edit_state(window, tmp_path, monkeypatch):
    from materials_data_studio.integrations.igor import IgorLauncher, IgorTransferService

    calls = []
    def launch(_self, path, executable=""):
        exported = load_h5xp(path).render_source()
        assert exported.graphs and exported.waves
        assert not exported.procedures
        calls.append((path, executable))
    monkeypatch.setattr(IgorLauncher, "launch", launch)
    service = IgorTransferService(transfer_directory=tmp_path)
    monkeypatch.setattr("materials_data_studio.integrations.igor.IgorTransferService", lambda: service)
    before = window.editor.document.current
    saved_path = window.editor.path
    history_index = window.editor.history.index()
    window._selection = ("wave", "x")
    window.send_to_igor_action.trigger()
    assert len(calls) == 1 and calls[0][0].suffix == ".h5xp"
    assert window.editor.document.current is before
    assert window.editor.path == saved_path and window.editor.history.index() == history_index


def test_send_document_failure_never_launches_and_launch_error_retains_h5xp(source, tmp_path, monkeypatch):
    from materials_data_studio.integrations.igor import (
        IgorLauncher,
        IgorLaunchError,
        IgorTransferService,
    )

    calls = []
    def fail_launch(_self, path, executable=""):
        calls.append(path)
        raise IgorLaunchError("Synthetic launch failure", path)
    monkeypatch.setattr(IgorLauncher, "launch", fail_launch)
    service = IgorTransferService(transfer_directory=tmp_path)
    document = EditableGraphDocument.from_pxp(source)
    with pytest.raises(IgorLaunchError) as raised:
        service.send_document(document)
    assert load_h5xp(raised.value.experiment_path).current.graphs
    assert len(calls) == 1
    def fail_export(*args):
        raise ValueError("Synthetic export failure")
    monkeypatch.setattr("materials_data_studio.tools.graph_h5xp.save_h5xp", fail_export)
    with pytest.raises(ValueError, match="Synthetic export failure"):
        service.send_document(document)
    assert len(calls) == 1


def test_system_fonts_and_palette(qapp: object) -> None:
    font = FontEdit("Arial")
    assert font.count() > 5 and font.isEditable()
    font.setText("Custom font")
    assert font.text() == "Custom font"
    color = ColorEdit("#ff0000")
    assert not color.swatch.icon().isNull()
    color.setText("#001122")
    assert color.text() == "#001122"
    combo = QComboBox()
    combo.addItems(["Viridis", "Grays", "VioletOrangeYellow"])
    colormap_swatches(combo)
    assert all(not combo.itemIcon(i).isNull() for i in range(combo.count()))


def test_single_literal_escape_and_draw_paths() -> None:
    graph = parse_recreation(r"""Window G() : Graph
Display y vs x
Label left "\\f02Italic\\f00"
TextBox/C/N=text0 "\\f01Bold\\f00\rsecond"
SetDrawEnv xcoord=prel,ycoord=prel
DrawBezier/ABS 0,0,1,1,{0,0,0.1,0,0.2,1,0.3,1,0.4,1,0.5,0,0.6,0}
EndMacro""")[0][0]
    assert graph.axes[0].label_raw == r"\f02Italic\f00"
    assert graph.annotations[0].raw == "\\f01Bold\\f00\rsecond"
    assert len(graph.drawings[0].coordinates) == 14


@pytest.mark.parametrize("key,snapped", [("", True), ("alt", False), ("shift+alt", False)])
def test_guides_and_alt(window: object, key: str, snapped: bool) -> None:
    window.drawing_snap = True  # type: ignore[attr-defined]
    point = guided(window, SimpleNamespace(x=37, y=22, key=key), np.array([10, 10]))
    assert point[0] == (40 if snapped else 37)
    if "shift" in key:
        assert point[1] == 10


def test_drawing_clipboard_and_selection(window: object) -> None:
    graph = window.graph()  # type: ignore[attr-defined]
    window.editor.edit_graph(
        replace(graph, drawings=(PxpDrawing("DrawLine", (0.1, 0.1, 0.5, 0.5)),)), "drawing"
    )  # type: ignore[attr-defined]
    window.select_target("drawing", "0")  # type: ignore[attr-defined]
    drawing_operation(window, "copy")
    assert '"mds_drawing": 1' in QApplication.clipboard().text()
    assert len(window.graph().drawings) == 1  # type: ignore[attr-defined]
    window.paste_drawing()  # type: ignore[attr-defined]
    assert len(window.graph().drawings) == 2  # type: ignore[attr-defined]
    window.editor.history.undo()  # type: ignore[attr-defined]
    window.select_target("drawing", "0")  # type: ignore[attr-defined]
    drawing_operation(window, "duplicate")
    assert len(window.graph().drawings) == 2  # type: ignore[attr-defined]
    drawing_operation(window, "cut", index=0)
    assert len(window.graph().drawings) == 1  # type: ignore[attr-defined]
    window.editor.history.undo()  # type: ignore[attr-defined]
    assert len(window.graph().drawings) == 2  # type: ignore[attr-defined]
    assert "checked" in window.drawing_toolbar.styleSheet()  # type: ignore[attr-defined]


def test_multinode_bezier_creation_and_edit(
    window: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(DrawingDialog, "exec", lambda *_: pytest.fail("Drawing options opened"))
    ax = window.figure.axes[0]  # type: ignore[attr-defined]
    initial_bounds = window.canvas.size()  # type: ignore[attr-defined]
    window.set_mode("Bezier")  # type: ignore[attr-defined]
    for i, xy in enumerate([(0.1, 0.7), (0.4, 0.3), (0.7, 0.7)]):
        x, y = ax.transAxes.transform(xy)
        event = SimpleNamespace(button=1, x=x, y=y, inaxes=ax, dblclick=i == 2)
        window.pointer_press(event)  # type: ignore[attr-defined]
        if i < 2:
            window.pointer_move(SimpleNamespace(x=x + 20, y=y + 15))  # type: ignore[attr-defined]
            QApplication.processEvents()
            assert window.canvas.size() == initial_bounds  # type: ignore[attr-defined]
            assert window._insert_drag["ax"] in window.figure.axes  # type: ignore[attr-defined]
            window._paint_drag()  # type: ignore[attr-defined]
            nodes, handles = window.drawing_selection_overlay.insertion_geometry(window._insert_drag)  # type: ignore[attr-defined]
            assert len(nodes) == i + 1
            np.testing.assert_allclose(nodes[-1], [x, y])
            np.testing.assert_allclose(handles[-1, 1], [x + 20, y + 15])
            assert window.drawing_selection_overlay.isVisible()  # type: ignore[attr-defined]
            assert not window.drawing_selection_overlay.grab().isNull()  # type: ignore[attr-defined]
            window.pointer_release(SimpleNamespace(x=x + 20, y=y + 15))  # type: ignore[attr-defined]
    drawing = window.graph().drawings[0]  # type: ignore[attr-defined]
    assert len(drawing.coordinates) == 14
    window.canvas.draw()  # type: ignore[attr-defined]
    window.set_mode("Bezier")  # type: ignore[attr-defined]
    window.select_target("drawing", "0")  # type: ignore[attr-defined]
    from materials_data_studio.tools.graph_drawing import drawing_transform

    artist = next(a for a, target in window.artist_targets.items() if target == ("drawing", "0"))  # type: ignore[attr-defined]
    x, y = drawing_transform(artist.axes, drawing).transform(drawing.coordinates[6:8])
    window.pointer_press(SimpleNamespace(button=1, x=x, y=y, inaxes=artist.axes, dblclick=False))  # type: ignore[attr-defined]
    assert window._bezier_drag  # type: ignore[attr-defined]
    window.pointer_move(SimpleNamespace(x=x + 15, y=y + 10, key="alt"))  # type: ignore[attr-defined]
    window._paint_drag()  # type: ignore[attr-defined]
    window.pointer_release(SimpleNamespace())  # type: ignore[attr-defined]
    assert window.graph().drawings[0] != drawing  # type: ignore[attr-defined]
    window.editor.history.undo()  # type: ignore[attr-defined]
    assert window.graph().drawings[0] == drawing  # type: ignore[attr-defined]


def test_h5xp_roundtrip_and_external_import(source: object, tmp_path: Path) -> None:
    document = EditableGraphDocument.from_pxp(source)  # type: ignore[arg-type]
    projection = document.render_source()
    text_wave = make_wave("labels", np.array(["一", "long name", "C"]))
    complex_wave = make_wave("complex", np.array([1 + 2j, 3 - 4j]))
    document.current.waves = (*projection.waves, text_wave, complex_wave)
    document.current.tables = (PxpTable("Table0", ("x", "y", "labels")),)
    graph = document.current.graphs[0]
    document.current.graphs = (
        replace(
            graph, drawings=(PxpDrawing("DrawBezier", (0.0, 0.0, 0.1, 0.2, 0.2, 0.3, 0.4, 0.5)),)
        ),
        *document.current.graphs[1:],
    )
    path = tmp_path / "graph.h5xp"
    save_h5xp(path, document)
    restored = load_h5xp(path)
    assert restored.current.graphs == document.current.graphs
    assert restored.render_source().table_definitions == document.render_source().table_definitions
    for first, second in zip(
        document.render_source().waves, restored.render_source().waves, strict=True
    ):
        np.testing.assert_array_equal(first.data, second.data)
        assert first.dimension_scaling == second.dimension_scaling
    with h5py.File(path, "a") as handle:
        assert handle.attrs["IGORPlatform"] == b"Windows"
        del handle["MDS Graph Document"]
    external = load_h5xp(path)
    assert len(external.current.graphs) == 2
    np.testing.assert_array_equal(
        external.render_source().wave("root:MDS:map").data, projection.wave("map").data
    )  # type: ignore[union-attr]


def test_h5xp_rejects_external_links(tmp_path: Path) -> None:
    path = tmp_path / "unsafe.h5xp"
    with h5py.File(path, "w") as handle:
        handle["Packed Data"] = h5py.ExternalLink("outside.h5", "/")
    with pytest.raises(ValueError, match="links"):
        load_h5xp(path)


def test_h5xp_native_fractional_settings_and_palette(source: object, tmp_path: Path) -> None:
    from matplotlib import colormaps

    document = EditableGraphDocument.from_pxp(source)  # type: ignore[arg-type]
    graph, image_graph = document.current.graphs
    document.current.graphs = (
        replace(graph, traces=(replace(graph.traces[0], offset=(0.25, -0.125), multiplier=(1.5, 2.75)),)),
        replace(image_graph, images=(replace(image_graph.images[0], color_table="plasma"),)),
    )
    path = tmp_path / "palette.h5xp"
    save_h5xp(path, document)
    with h5py.File(path, "a") as handle:
        raw = handle["Recreation/Recreation Procedures"][()].decode()
        assert "offset(y)={0.25,-0.125}" in raw
        assert "muloffset(y)={1.5,2.75}" in raw
        assert "root:MDS:MDS_ctab_plasma" in raw
        del handle["MDS Graph Document"]
    imported = load_h5xp(path)
    palette_name = imported.current.graphs[1].images[0].color_table
    np.testing.assert_allclose(
        colormaps[palette_name](np.array([0., 1.])),
        colormaps["plasma"](np.array([0., 1.])), atol=1 / 65535,
    )
    assert imported.current.graphs[0].traces[0].offset == (0.25, -0.125)
    from materials_data_studio.tools.graph_document import load_graph_document, save_graph_document

    mdsg = tmp_path / "palette.mdsg"
    save_graph_document(mdsg, imported)
    colormaps.unregister(palette_name)
    colormaps.unregister(palette_name + "_r")
    restored = load_graph_document(mdsg)
    assert restored.current.graphs[1].images[0].color_table in colormaps


def test_h5xp_export_only_and_entire_document_without_send(window: object, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "question", lambda _, title, *args: QMessageBox.StandardButton.Discard if title == "Unsaved Graph Document" else QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: pytest.fail(str(args)))
    destination = tmp_path / "saved.h5xp"
    assert window._save(destination)  # type: ignore[attr-defined]
    assert window.editor.dirty and window.editor.path is None  # type: ignore[attr-defined]
    original = destination.read_bytes()
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    assert not window._save(destination)  # type: ignore[attr-defined]
    assert warnings and destination.read_bytes() == original
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: pytest.fail(str(args)))
    window.open_graph_path(destination)  # type: ignore[attr-defined]
    assert window.editor.path is None and window.editor.dirty  # type: ignore[attr-defined]
    assert window.editor.document.source.basename == destination.name  # type: ignore[attr-defined]
    monkeypatch.setattr(QMessageBox, "question", lambda _, title, *args: QMessageBox.StandardButton.Save if title == "Unsaved Graph Document" else QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(window, "save_document", lambda: window._save(tmp_path / "close-export.h5xp"))  # type: ignore[attr-defined]
    assert not window.confirm_close()  # type: ignore[attr-defined]
    monkeypatch.undo()
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: pytest.fail(str(args)))
    graph = window.graph()  # type: ignore[attr-defined]
    window.editor.edit_graph(replace(graph, axes=(replace(graph.axes[0], label_raw="Edited axis"), *graph.axes[1:])), "label")  # type: ignore[attr-defined]
    editable = tmp_path / "saved.mdsg"
    assert window._save(editable)  # type: ignore[attr-defined]
    assert window.editor.path == editable and not window.editor.dirty  # type: ignore[attr-defined]
    window.editor.edit_graph(replace(window.graph(), legend_raw="Changed"), "legend")  # type: ignore[attr-defined]
    assert window._save(tmp_path / "second.h5xp")  # type: ignore[attr-defined]
    assert window.editor.path == editable and window.editor.dirty  # type: ignore[attr-defined]
    assert window.save_document() and not window.editor.dirty  # type: ignore[attr-defined]
    assert destination.read_bytes() == original
    window.editor.document.current.tables = (PxpTable("Table0", ("x", "y")),)  # type: ignore[attr-defined]
    path = tmp_path / "whole-export.h5xp"
    assert window._save(path)  # type: ignore[attr-defined]
    exported = load_h5xp(path).render_source()
    assert len(exported.graphs) == 2 and len(exported.waves) == 4
    assert len(exported.table_definitions) == 1 and not exported.procedures
    # Both export and transfer operate on the document, regardless of selection.
    window._selection = ("wave", "x")  # type: ignore[attr-defined]
    assert window._save(tmp_path / "wave-selection-export.h5xp")  # type: ignore[attr-defined]
    assert callable(window.send_to_igor)  # type: ignore[attr-defined]
    assert window.send_to_igor_action in window.file_menu.actions()  # type: ignore[attr-defined]
    assert not window.open_button.isVisible() and not window.source_label.isVisible()  # type: ignore[attr-defined]


def test_h5xp_native_all_objects_axis_positions_and_no_procedures(source: object, tmp_path: Path) -> None:
    from materials_data_studio.tools.graph_document import load_graph_document, save_graph_document

    document = EditableGraphDocument.from_pxp(source)  # type: ignore[arg-type]
    document.source = replace(document.source, procedures=("PrivateUserProcedure",), recreation_raw=document.source.recreation_raw + "\nFunction PrivateUserProcedure()\nAbort\nEnd")
    native = """Window Multi() : Graph
Display y vs x
AppendToGraph/R=secondary y vs x
ModifyGraph axisEnab(left)={0,0.45},axisEnab(secondary)={0.55,1}
ModifyGraph freePos(secondary)={1.12,kwFraction},axOffset(secondary)=8
ModifyGraph lblPos(secondary)=42,lblPosMode(secondary)=1,lblLatPos(secondary)=3
Label secondary "Second axis"
SetAxis secondary 0,10
SetAxis bottom *,6
EndMacro"""
    graph = parse_recreation(native)[0][0]
    document.current.graphs = (*document.current.graphs, graph)
    document.current.tables = (PxpTable("T0", ("x", "y"), (("x", 95.),)), PxpTable("T1", ("err",)))
    document.current.waves = (*document.render_source().waves, make_wave("unused", np.arange(5.)))
    document.current.layout["XY"] = {"view": {"traces": {"y": {"visible": False}}}, "annotations": {"note": {"visible": False}}}
    path = tmp_path / "whole.h5xp"
    save_h5xp(path, document)
    with h5py.File(path, "a") as handle:
        raw = handle["Recreation/Recreation Procedures"][()].decode()
        assert "axisEnab(secondary)={0.55000000000000004,1}" in raw
        assert "freePos(secondary)={1.1200000000000001,kwFraction}" in raw
        assert "/R=secondary" in raw and "axOffset(secondary)=8" in raw
        assert "lblPosMode(secondary)=1" in raw and "lblLatPos(secondary)=3" in raw
        assert "SetAxis bottom *,6" in raw
        assert "hideTrace(y)=1" in raw and "/N=note/V=0" in raw
        assert "opaque(y#1)=" in raw and "ModifyTable width(root:MDS:x)=95" in raw
        assert "PrivateUserProcedure" not in raw
        assert set(handle.keys()) == {"Packed Data", "Recreation", "MDS Graph Document"}
        del handle["MDS Graph Document"]
    imported = load_h5xp(path).render_source()
    assert len(imported.graphs) == 3 and len(imported.table_definitions) == 2
    assert len([w for w in imported.waves if not w.name.startswith("MDS_ctab_")]) == 5
    assert imported.graphs[1].images[0].color_lut
    axis = next(a for a in imported.graphs[-1].axes if a.name == "secondary")
    assert axis.side == "right" and axis.enable == (.55, 1.)
    assert axis.free_position == (1.12, "kwFraction") and axis.style.axis_offset == 8
    assert axis.style.label_position == 42 and axis.style.label_position_mode == 1
    # Native positions survive MDSG serialization too, including old schema support.
    mdsg = tmp_path / "axis.mdsg"
    save_graph_document(mdsg, document)
    assert load_graph_document(mdsg).current.graphs[-1] == graph
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        save_h5xp(path, document)
    assert path.read_bytes() == original


def test_h5xp_no_clobber_at_atomic_commit(source: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    import materials_data_studio.tools.graph_h5xp as writer

    destination = tmp_path / "race.h5xp"
    commit = "rename" if os.name == "nt" else "link"
    operation = getattr(writer.os, commit)
    def competitor(first: Path, second: Path) -> None:
        destination.write_bytes(b"Existing experiment created during export")
        operation(first, second)
    monkeypatch.setattr(writer.os, commit, competitor)
    with pytest.raises(FileExistsError):
        save_h5xp(destination, EditableGraphDocument.from_pxp(source))  # type: ignore[arg-type]
    assert destination.read_bytes() == b"Existing experiment created during export"
    assert not list(tmp_path.glob("*.tmp"))
