"""Capture the PXP viewer using only generated numerical demonstration data."""

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PySide6.QtCore import QEventLoop, QPoint, QTimer
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QApplication

from materials_data_studio.tools.graph_annotation_dialog import AnnotationDialog
from materials_data_studio.tools.graph_calculation import WaveCalculationDialog
from materials_data_studio.tools.graph_drawing import DrawingDialog
from materials_data_studio.tools.graph_export import GraphDataDialog, SaveGraphicsDialog
from materials_data_studio.tools.graph_fit_dialog import QuickFitDialog
from materials_data_studio.tools.graph_menus import graph_context_menu
from materials_data_studio.tools.graph_tables import TableEditorDialog, table_state
from materials_data_studio.tools.graph_trace_dialogs import NewGraphDialog
from materials_data_studio.tools.graph_wave_dialogs import TextImportDialog, WaveEditorDialog
from materials_data_studio.tools.graph_window import GraphEditorWindow
from materials_data_studio.tools.pxp_model import PxpDocument, PxpDrawing, PxpTable, PxpWave
from materials_data_studio.tools.pxp_recreation import parse_recreation
from materials_data_studio.ui.theme import style_sheet

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "windows" if sys.platform == "win32" else "offscreen")
    application = QApplication.instance() or QApplication([])
    from manual_fonts import configure_capture_fonts
    configure_capture_fonts(application)
    application.setStyle("Fusion")
    application.setStyleSheet(style_sheet("light"))
    x = np.linspace(0, 10, 201)
    y = 1 + 0.5 * np.sin(x)
    image = np.exp(-((x[:, None] - 5.0) ** 2) / 3) * (
        0.2 + 0.8 * np.cos(np.linspace(0, 3, 81)[None, :]) ** 2
    )
    specs = (("position", x[::10], ((0.0, 0.5),)),
             ("signal", y[::10], ((0.0, 1.0),)),
             ("reference", 0.75 + 0.25 * np.cos(x[::10]), ((0.0, 1.0),)),
             ("position_full", x, ((0.0, 0.05),)),
             ("response_a", 1 + 0.4 * np.tanh(x - 5), ((0.0, 1.0),)),
             ("response_b", 1 + 0.5 * np.tanh(x - 4), ((0.0, 1.0),)),
             ("response_c", 1 + 0.6 * np.tanh(x - 3), ((0.0, 1.0),)),
             ("map", image, ((0.0, 0.05), (0.0, 0.1))))
    waves = tuple(PxpWave(name, f"root:{name}", name, values.dtype.str,
                          values.shape, values, scaling, ("",) * values.ndim, "", "")
                  for name, values, scaling in specs)
    for wave in waves:
        wave.data.setflags(write=False)
    graphs, tables = parse_recreation(r'''DefaultFont "Arial"
Window Signal() : Graph
Display signal vs position
AppendToGraph reference vs position
ModifyGraph mode=3,marker(signal)=8,marker(reference)=19,msize=3
ModifyGraph rgb(signal)=(12000,33000,60000),rgb(reference)=(5000,45000,28000)
ModifyGraph opaque(signal)=1,useMrkStrokeRGB(reference)=1,mrkStrokeRGB(reference)=(0,0,0)
ModifyGraph tick=2,mirror=1,fSize=14,axThick=1,minor=1
Label left "\Z16\f02I\f00 (a.u.)"
Label bottom "\Z16\f02x\f00 (mm)"
Legend/C/N=text0/F=0/B=1/A=RT/X=3/Y=3 "\Z14\s(signal) Hollow markers\r\s(reference) Filled + outline"
EndMacro
Window Map() : Graph
Display
AppendImage map
ModifyImage map ctab= {0.1,1,VioletOrangeYellow,0}
ModifyGraph width=198.425,height=198.425,tick=2,mirror=1,fSize=14,axThick=1.5
ModifyGraph axRGB=(48059,48059,48059)
ModifyGraph manTick(left)={0,2,0,0},manMinor(left)={1,5}
ModifyGraph manTick(bottom)={0,2,0,0},manMinor(bottom)={1,5}
ColorScale/C/N=text0/F=0/G=(65535,65535,65535)/B=1/A=MC/X=34/Y=-9
ColorScale/C/N=text0 image=map
AppendText "Normalized Intensity"
Label left "Field"
Label bottom "Position"
EndMacro
Window Offsets() : Graph
Display response_a vs position_full
AppendToGraph response_b vs position_full
AppendToGraph response_c vs position_full
ModifyGraph width={Aspect,1},height=230,margin(right)=75
ModifyGraph rgb(response_a)=(18000,14000,58000),rgb(response_b)=(0,33000,60000),rgb(response_c)=(0,47000,22000)
ModifyGraph lSize=1.5,offset(response_b)={0,1.8},offset(response_c)={0,3.6}
ModifyGraph tick=2,mirror=1,nticks(left)=0,fSize=14,lblMargin(left)=24
ModifyGraph manTick(bottom)={0,2,0,0},manMinor(bottom)={3,5}
SetAxis bottom 0,10
SetAxis left 0,7
Label bottom "\Z16\f02x\f00 (a.u.)"
Label left "\Z16\f02I\f00 (a.u.)"
TextBox/C/N=title/F0/B1/A=LT/X=4/Y=4 "\Z13Generated offset demo"
Legend/C/N=outside/F0/B1/A=MC/X=68/Y=0 "\Z14\sb+10Series C\r\sb+10Series B"
AppendText "\rSeries A"
SetDrawLayer UserFront
SetDrawEnv xcoord=bottom,ycoord=prel,arrow=1,arrowlen=4,arrowfat=1
DrawLine 5,0.73,5,0.78
SetDrawEnv xcoord=bottom,ycoord=prel,arrow=1,arrowlen=4,arrowfat=1
DrawLine 4,0.50,4,0.55
SetDrawEnv xcoord=bottom,ycoord=prel,arrow=1,arrowlen=4,arrowfat=1
DrawLine 3,0.24,3,0.29
EndMacro
''')
    source = b"Synthetic MDS PXP-viewer screenshot data only."
    document = PxpDocument("22222222-2222-2222-2222-222222222222", "Synthetic_Demo.pxp",
                           hashlib.sha256(source).hexdigest(), len(source), "10.0", "igor2",
                           "0.5.13", waves, ("root",), graphs, tables, (), (), "GRAPH_PARTIAL", source)
    viewer = GraphEditorWindow()
    viewer.command_workspace.console_dock.hide()  # Focus workflow figures; Command is illustrated separately.
    viewer.bind_document(document)
    viewer.resize(1500, 880)
    viewer.show()
    application.processEvents()
    tree = viewer.tree
    root = tree.topLevelItem(0)
    graph_parent = root.child(1)
    tree.setCurrentItem(graph_parent.child(1))
    application.processEvents()
    target = ROOT / "docs" / "screenshots" / "34_pxp_viewer.png"
    if not viewer.grab().save(str(target), "PNG"):
        raise RuntimeError("Could not save PXP viewer screenshot")
    tree.setCurrentItem(graph_parent.child(0))
    application.processEvents()
    target_markers = ROOT / "docs" / "screenshots" / "35_pxp_markers.png"
    if not viewer.grab().save(str(target_markers), "PNG"):
        raise RuntimeError("Could not save marker screenshot")
    tree.setCurrentItem(graph_parent.child(2))
    application.processEvents()
    target_offsets = ROOT / "docs" / "screenshots" / "36_pxp_offsets.png"
    if not viewer.grab().save(str(target_offsets), "PNG"):
        raise RuntimeError("Could not save offset screenshot")
    tree.setCurrentItem(graph_parent.child(0))
    viewer.apply_properties("axis", "bottom", {"major_interval": 2, "minor_count": 3,
                                               "offset_points": 34, "lateral": 0.55})
    viewer.apply_properties("trace", "signal", {"line_width": 1.5, "marker_fill": "#dce8f5"})
    viewer.select_target("axis", "bottom")
    application.processEvents()
    target_editor = ROOT / "docs" / "screenshots" / "37_graph_editor.png"
    if not viewer.grab().save(str(target_editor), "PNG"):
        raise RuntimeError("Could not save graph editor screenshot")
    assert viewer.editor is not None
    graphics = SaveGraphicsDialog(viewer, viewer.editor.document, "Signal")
    graphics.show()
    application.processEvents()
    graphics.grab().save(str(ROOT / "docs/screenshots/38_graph_save_graphics.png"), "PNG")
    graphics.close()
    data = GraphDataDialog(viewer, viewer.editor.document, "Signal")
    data.show()
    application.processEvents()
    data.grab().save(str(ROOT / "docs/screenshots/41_graph_export_data.png"), "PNG")
    data.close()
    viewer.canvas.draw()
    ax = viewer.figure.axes[0]
    viewer.region = {"ax": ax, "start": ax.transAxes.transform((0.25, 0.3)),
                     "end": ax.transAxes.transform((0.65, 0.7))}
    viewer._show_region()
    event = SimpleNamespace(x=0, y=0, inaxes=ax, button=3)
    menu = graph_context_menu(viewer, event)
    menu.popup(viewer.canvas.mapToGlobal(viewer.rubber_band.geometry().bottomRight()))
    application.processEvents()
    capture = viewer.grab()
    painter = QPainter(capture)
    painter.drawPixmap(viewer.mapFromGlobal(menu.pos()), menu.grab())
    painter.end()
    capture.save(str(ROOT / "docs/screenshots/39_graph_range_menu.png"), "PNG")
    menu.close()
    viewer.clear_region()
    artist = next(a for a, target in viewer.artist_targets.items() if target == ("trace", "signal"))
    event.x, event.y = artist.get_transform().transform((5, 1 + 0.5 * np.sin(5)))
    menu = graph_context_menu(viewer, event)
    menu.popup(viewer.mapToGlobal(QPoint(710, 70)))
    application.processEvents()
    capture = viewer.grab()
    painter = QPainter(capture)
    painter.drawPixmap(viewer.mapFromGlobal(menu.pos()), menu.grab())
    painter.end()
    capture.save(str(ROOT / "docs/screenshots/40_graph_trace_menu.png"), "PNG")
    menu.close()
    # New v0.8.8 workflows. All waves and text are generated above/in demo_data.
    importer = TextImportDialog(viewer, ROOT / "docs/demo_data/viewer_general_text.csv")
    importer.show()
    application.processEvents()
    importer.grab().save(str(ROOT / "docs/screenshots/42_graph_wave_import.png"), "PNG")
    importer.close()
    wave = document.wave("signal")
    assert wave is not None
    wave_editor = WaveEditorDialog(viewer, wave)
    wave_editor.show()
    wave_editor.table.setCurrentIndex(wave_editor.model.index(0, 0))
    application.processEvents()
    wave_editor.grab().save(str(ROOT / "docs/screenshots/43_graph_wave_edit.png"), "PNG")
    wave_editor.close()
    new_graph = NewGraphDialog(viewer, viewer.editor.document)
    for index in range(new_graph.y_list.count()):
        item = new_graph.y_list.item(index)
        if "root:signal " in item.text() or "root:reference " in item.text():
            item.setSelected(True)
    new_graph.x_list.setCurrentRow(1)
    new_graph.show()
    application.processEvents()
    new_graph.grab().save(str(ROOT / "docs/screenshots/44_graph_new_graph.png"), "PNG")
    new_graph.close()
    annotation = AnnotationDialog(viewer, viewer.editor.document, "Signal", "text0")
    annotation.resize(900, 750)
    annotation.show()
    application.processEvents()
    annotation.grab().save(str(ROOT / "docs/screenshots/45_graph_annotation.png"), "PNG")
    annotation.close()
    color_scale = AnnotationDialog(viewer, viewer.editor.document, "Map", viewer.editor.document.graph("Map").images[0].wave, True)
    color_scale.resize(1100, 750)
    color_scale.tabs.setCurrentIndex(color_scale.tabs.count() - 1)
    color_scale.show()
    application.processEvents()
    color_scale.grab().save(str(ROOT / "docs/screenshots/47_graph_colorscale.png"), "PNG")
    color_scale.close()
    fitting = QuickFitDialog(viewer, viewer.editor.document, "Signal", "signal", "sin")
    fitting.show()
    fitting.run()
    loop = QEventLoop()
    assert fitting.worker is not None
    fitting.worker.finished.connect(loop.quit)
    QTimer.singleShot(20000, loop.quit)
    loop.exec()
    application.processEvents()
    if fitting.fit_result is None:
        raise RuntimeError("Synthetic Quick Fit screenshot did not finish")
    fitting.grab().save(str(ROOT / "docs/screenshots/46_graph_quick_fit.png"), "PNG")
    fitting.close()
    viewer.select_target("trace", "signal")
    application.processEvents()
    combo = viewer.property_widgets["trace"]["widgets"]["marker"][0]
    combo.showPopup()
    application.processEvents()
    capture = viewer.grab()
    popup = combo.view().window()
    painter = QPainter(capture)
    painter.drawPixmap(viewer.mapFromGlobal(popup.pos()), popup.grab())
    painter.end()
    capture.save(str(ROOT / "docs/screenshots/48_graph_visual_markers.png"), "PNG")
    combo.hidePopup()
    viewer.editor.edit_graph(replace(viewer.editor.document.graph("Signal"), drawings=(
        PxpDrawing("DrawText", (.15, .18), "Generated text\nTwo lines", color=(0, 30000, 50000), font_size=14),
        PxpDrawing("DrawOval", (.12, .3, .35, .5), color=(0, 30000, 50000), line_width=1.5),
        PxpDrawing("DrawLine", (.35, .4, .55, .6), arrow=1, color=(0, 30000, 50000)),
    )), "Synthetic drawings")
    application.processEvents()
    viewer.select_target("drawing", "1")
    application.processEvents()
    viewer.grab().save(str(ROOT / "docs/screenshots/49_graph_drawing_toolbar.png"), "PNG")
    drawing_dialog = DrawingDialog(viewer, viewer.editor.document.graph("Signal").drawings[0])
    drawing_dialog.show()
    application.processEvents()
    drawing_dialog.grab().save(str(ROOT / "docs/screenshots/50_graph_drawing_edit.png"), "PNG")
    drawing_dialog.close()
    viewer.editor.edit_graph(replace(viewer.editor.document.graph("Signal"), drawings=(
        PxpDrawing("DrawBezier", (.1, .75, .15, .5, .3, .3, .4, .4, .5, .5, .6, .6, .7, .25), color=(0, 10000, 65535), line_width=1.5),
    )), "Synthetic Bezier")
    viewer.set_mode("Bezier")
    viewer.select_target("drawing", "0")
    viewer.canvas.draw()
    application.processEvents()
    viewer.grab().save(str(ROOT / "docs/screenshots/54_graph_bezier_handles.png"), "PNG")
    ax = viewer.figure.axes[0]
    for xy, delta in (((.2, .2), (35, 25)), ((.55, .8), (35, -30))):
        x, y = ax.transAxes.transform(xy)
        viewer.pointer_press(SimpleNamespace(button=1, x=x, y=y, inaxes=ax, dblclick=False))
        viewer.pointer_move(SimpleNamespace(x=x + delta[0], y=y + delta[1], key="alt"))
        viewer.pointer_release(SimpleNamespace(x=x + delta[0], y=y + delta[1], key="alt"))
    application.processEvents()
    viewer._paint_drag()
    application.processEvents()
    viewer.grab().save(str(ROOT / "docs/screenshots/56_graph_bezier_creation.png"), "PNG")
    viewer.set_mode("Pointer")
    tree.setCurrentItem(graph_parent.child(1))
    application.processEvents()
    image_combo = viewer.property_widgets["image"]["widgets"]["color_table"][0]
    viewer.select_target("image", viewer.editor.document.graph("Map").images[0].wave)
    image_combo.showPopup()
    application.processEvents()
    capture = viewer.grab()
    popup = image_combo.view().window()
    painter = QPainter(capture)
    painter.drawPixmap(viewer.mapFromGlobal(popup.pos()), popup.grab())
    painter.end()
    capture.save(str(ROOT / "docs/screenshots/55_graph_colormaps.png"), "PNG")
    image_combo.hidePopup()
    tree.setCurrentItem(graph_parent.child(0))
    calculation = WaveCalculationDialog(viewer, viewer.editor.document)
    calculation.name.setText("difference")
    calculation.expression.setText("signal - reference")
    calculation.show()
    application.processEvents()
    calculation.grab().save(str(ROOT / "docs/screenshots/51_graph_wave_calculation.png"), "PNG")
    calculation.close()
    table = PxpTable("Generated_Table", ("position", "signal", "reference"))
    viewer._selection = "table", table.name
    viewer.view_state["selected"] = viewer._selection
    viewer.editor.commit(viewer.editor.document.current, table_state(viewer.editor.document, table), "Synthetic table")
    application.processEvents()
    viewer.grab().save(str(ROOT / "docs/screenshots/52_graph_table.png"), "PNG")
    table_editor = TableEditorDialog(viewer, viewer.editor.document, table)
    table_editor.show()
    application.processEvents()
    table_editor.grab().save(str(ROOT / "docs/screenshots/53_graph_table_edit.png"), "PNG")
    table_editor.close()
    viewer._discard_approved = True
    viewer.close()
    print(f"Captured {target.name}, {target_markers.name}, {target_offsets.name}, {target_editor.name} and workflow screenshots through 56 from synthetic arrays")


if __name__ == "__main__":
    main()
