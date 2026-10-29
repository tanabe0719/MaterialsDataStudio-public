"""Capture command/procedure docks with generated waves, never research fixtures."""
from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QApplication, QScrollArea

from materials_data_studio.tools.graph_window import GraphEditorWindow
from materials_data_studio.tools.pxp_model import PxpDocument
from materials_data_studio.ui.theme import style_sheet


def capture_igor_dialogs(viewer, workspace, app, root, *, file_only=False):
    """Only generated parameters and explicit, nonexecuting UI requests."""
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    from materials_data_studio.tools.igor_dialogs import IgorInteractionController, IgorPromptDialog
    from materials_data_studio.tools.igor_parameter_panel import ENTRY_MARKER

    environment = viewer.editor.document.current.environment
    environment["variables"] = {"root:demo_parameters:weight_mg": 10.0, "root:demo_parameters:molar_mass": 100.0}
    environment["python_source"] = "def mdsRoutine1(event):\n    return 0\n" + ENTRY_MARKER + "\nmdsRoutine1({'eventCode': 2})\n"
    environment["igor_parameter_panels"] = [{
        "name": "SyntheticParameters", "title": "Generated data analysis",
        "rect": [50, 50, 490, 190],
        "groups": [{"name": "inputs", "title": "Load and plot generated data", "pos": [10, 10], "size": [420, 120]}],
        "fields": [
            {"name": "weight", "title": "Weight (mg):", "path": "root:demo_parameters:weight_mg", "pos": [20, 40], "size": [210, 24]},
            {"name": "molar_mass", "title": "Molar mass (g/mol):", "path": "root:demo_parameters:molar_mass", "pos": [20, 75], "size": [210, 24]},
        ],
        "buttons": [{"name": "load", "title": "Load generated data", "callback": "DemoLoad", "python_callable": "mdsRoutine1",
                     "pos": [245, 40], "size": [170, 60], "fcolor": [65535, 55000, 49151]}],
    }]
    workspace.show_macro_parameters()
    app.processEvents()
    if not file_only:
        assert workspace.parameter_dialog.grab().save(str(root / "72_igor_parameters.png"), "PNG")
    workspace.parameter_dialog.close()

    class CaptureSession:
        def __init__(self, kind, payload):
            self.request = {"version": 1, "sequence": 1, "kind": kind, "payload": payload}

        def poll(self):
            request, self.request = self.request, None
            return request

        def respond(self, _request, _response):
            pass

        def close(self):
            pass

    examples = [
        ("file_open", {"title": "Select a generated .dat file", "filter": "Data files (*.dat):.dat;"}, "74_igor_runtime_file.png"),
        ("prompt", {"title": "Generated sample parameters", "help": "These demonstration values are not research data.", "fields": [
            {"name": "weight", "label": "Weight (mg)", "type": "number", "value": 10.0},
            {"name": "molar_mass", "label": "Molar mass (g/mol)", "type": "number", "value": 100.0},
            {"name": "sample", "label": "Sample label", "type": "text", "value": "Generated demonstration"},
            {"name": "mode", "label": "Plot mode", "type": "choice", "choices": ["Temperature", "Field"], "value": 0},
        ]}, "75_igor_runtime_prompt.png"),
        ("message", {"title": "Generated data confirmation", "text": "The generated example is ready.\nContinue to create a graph?", "buttons": ["Yes", "No", "Cancel"]}, "76_igor_runtime_message.png"),
    ]
    if file_only:
        examples = examples[:1]
    for kind, payload, filename in examples:
        controller = IgorInteractionController(workspace, CaptureSession(kind, payload), [])
        controller.poll()
        dialog = controller.dialog
        assert dialog is not None
        if isinstance(dialog, QFileDialog):
            dialog.setDirectory(str(root.parent / "demo_data"))
            dialog.selectFile("viewer_quantum_design_demo.dat")
            dialog.resize(900, 560)
        elif isinstance(dialog, IgorPromptDialog):
            dialog.resize(560, dialog.sizeHint().height())
        elif isinstance(dialog, QMessageBox):
            dialog.setMinimumWidth(460)
            dialog.setMinimumHeight(180)
        app.processEvents()
        if isinstance(dialog, QFileDialog):
            from manual_privacy import anonymize_file_dialog
            anonymize_file_dialog(dialog)
            app.processEvents()
        assert dialog.grab().save(str(root / filename), "PNG")
        controller.close()


def main() -> None:
    project = Path(__file__).resolve().parents[1]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("MDS_SETTINGS_DIRECTORY", str(project / ".test-tmp/manual-capture-igor/settings"))
    os.environ.setdefault("MDS_RECOVERY_DIRECTORY", str(project / ".test-tmp/manual-capture-igor/recovery"))
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                      str(project / ".test-tmp/manual-capture-igor/qt-settings"))
    app = QApplication.instance() or QApplication([])
    assert isinstance(app, QApplication)
    from manual_fonts import configure_capture_fonts
    configure_capture_fonts(app)
    app.setStyle("Fusion")
    app.setStyleSheet(style_sheet("light"))
    viewer = GraphEditorWindow()
    viewer.resize(1500, 980)
    source = PxpDocument("generated", "Synthetic_Command_Demo.mdsg", "", 0, "", "MDS", "", status="FULL")
    source = replace(source, command_history=("// Synthetic imported history: displayed only",), procedures=("// Synthetic Igor source: read-only, never executed\nFunction Example()\n    Print \"Imported source\"\nEnd",))
    viewer.bind_document(source)
    workspace = viewer.command_workspace
    workspace.execute('Make/D/N=201 time,signal; SetScale/P x,0,0.05,"s",time; time=x; signal=sin(2*pi*time/3); Display/N=Signal signal vs time')
    workspace.execute('Label bottom "Time (s)"; Label left "Signal (a.u.)"; ModifyGraph rgb(signal)=(9000,24000,48000),lsize(signal)=1.5,mirror=1,tick=2')
    workspace.execute("WaveStats signal")
    viewer.show()
    app.processEvents()
    viewer.canvas.draw()
    root = project / "docs/screenshots"
    if "--igor-dialogs-only" in sys.argv or "--igor-file-only" in sys.argv:
        capture_igor_dialogs(viewer, workspace, app, root, file_only="--igor-file-only" in sys.argv)
        viewer._discard_approved = True
        viewer.close()
        return
    help_menu = viewer.help_menu
    help_menu.popup(viewer.menuBar().mapToGlobal(viewer.menuBar().actionGeometry(help_menu.menuAction()).bottomLeft()))
    app.processEvents()
    capture = viewer.grab()
    painter = QPainter(capture)
    painter.drawPixmap(viewer.mapFromGlobal(help_menu.pos()), help_menu.grab())
    painter.end()
    assert capture.save(str(root / "77_graph_help_menu.png"), "PNG")
    help_menu.hide()
    workspace.input.setText("App")
    workspace.input.setFocus()
    workspace.completer.setCompletionPrefix("App")
    workspace.completer.complete()
    app.processEvents()
    image = viewer.grab()
    popup = workspace.completer.popup()
    if popup is not None and popup.isVisible():
        popup.move(workspace.input.mapToGlobal(QPoint(0, -popup.height())))
        app.processEvents()
        painter = QPainter(image)
        painter.drawPixmap(viewer.mapFromGlobal(popup.pos()), popup.grab())
        painter.end()
        popup.hide()
    assert image.save(str(root / "57_command_window.png"), "PNG")
    workspace.input.clear()
    workspace.procedure_dock.show()
    workspace.console_dock.hide()
    viewer.options.hide()
    viewer.resizeDocks([workspace.procedure_dock], [670], Qt.Orientation.Horizontal)
    workspace.code.setPlainText('import numpy as np\nfrom mds import wave, graph\nfrom mds import transaction\n\nwith transaction("Derivative"):\n    t = wave("time")\n    y = wave("signal")\n    values = np.gradient(\n        y.values, t.values\n    )\n    d = wave.create("derivative", values)\n    g = graph.new("Derivative")\n    trace = g.append(d, t)\n    trace.color = "#2361ab"\n    trace.line_width = 1.5\n    g.axis("bottom").label = "Time (s)"\n')
    workspace.flush()
    app.processEvents()
    viewer.canvas.draw()
    assert viewer.grab().save(str(root / "58_procedure_window.png"), "PNG")
    workspace.float_dock(workspace.console_dock)
    workspace.float_dock(workspace.procedure_dock)
    width, height = (viewer.width() - 60) // 2, viewer.height() - 110
    workspace.console_dock.resize(width, height)
    workspace.procedure_dock.resize(width, height)
    workspace.console_dock.move(viewer.mapToGlobal(QPoint(20, 70)))
    workspace.procedure_dock.move(viewer.mapToGlobal(QPoint(40 + width, 70)))
    app.processEvents()
    image = viewer.grab()
    painter = QPainter(image)
    for dock in (workspace.console_dock, workspace.procedure_dock):
        painter.drawPixmap(viewer.mapFromGlobal(dock.pos()), dock.grab())
    painter.end()
    assert image.save(str(root / "59_command_procedure_separate.png"), "PNG")
    from materials_data_studio.tools.graph_wave_dialogs import TextImportDialog
    dialog = TextImportDialog(viewer, root.parent / "demo_data/viewer_quantum_design_demo.dat")
    dialog.show()
    target = dialog.columns.item(4, 1)
    assert target is not None
    dialog.columns.scrollToItem(target)
    app.processEvents()
    assert dialog.grab().save(str(root / "60_quantum_design_import.png"), "PNG")
    dialog.close()
    workspace.dock_back(workspace.console_dock)
    workspace.dock_back(workspace.procedure_dock)
    workspace.procedure_dock.hide()
    viewer.options.hide()
    viewer.resizeDocks([workspace.console_dock], [280], Qt.Orientation.Vertical)
    workspace.output.clear()
    app.processEvents()
    viewer.canvas.draw()
    viewer.apply_properties("trace", "signal", {"color": "#2361ab", "line_width": 2})
    viewer.set_mode("Line")
    app.processEvents()
    viewer.canvas.draw()
    ax = viewer.figure.axes[0]
    x0, y0 = ax.transAxes.transform((.2, .7))
    x1, y1 = ax.transAxes.transform((.65, .4))
    viewer.pointer_press(SimpleNamespace(button=1, x=x0, y=y0, inaxes=ax, dblclick=False, key=None))
    viewer.pointer_move(SimpleNamespace(x=x1, y=y1, inaxes=ax, key=None))
    viewer.pointer_release(SimpleNamespace(button=1, x=x1, y=y1, inaxes=ax, key=None))
    viewer.set_mode("Pointer")
    app.processEvents()
    viewer.canvas.draw()
    assert "DrawLine/W=Signal" in workspace.output.toPlainText()
    assert viewer.grab().save(str(root / "62_gui_command_recording.png"), "PNG")
    workspace.show_procedure_help()
    help_dialog = workspace.procedure_help
    help_dialog.tabs.setCurrentIndex(2)
    app.processEvents()
    assert help_dialog.grab().save(str(root / "61_procedure_help.png"), "PNG")
    help_dialog.hide()
    from materials_data_studio.tools.igor_conversion_dialog import IgorConversionDialog
    synthetic_igor = '''Function DemoScale(factor)
    Variable factor
    Make/D/O/N=201 demo_signal
    demo_signal = sin(p/12)*factor
    Display/N=ConvertedDemo demo_signal
    ModifyGraph tick=2,mirror=1,rgb[0]=(9000,24000,48000)
End
'''
    conversion = IgorConversionDialog(viewer, [("Synthetic Igor demonstration", synthetic_igor)])
    conversion.open_inputs([root.parent / "demo_data/viewer_general_text.csv"])
    conversion.analyze()
    conversion.arguments.setText("[2.0]")
    assert conversion.refresh_preview()
    conversion.pages.setCurrentIndex(1)
    conversion.show()
    app.processEvents()
    assert conversion.grab().save(str(root / "71_igor_conversion.png"), "PNG")
    conversion.close()
    capture_igor_dialogs(viewer, workspace, app, root)
    workspace.console_dock.hide()
    workspace.procedure_dock.hide()
    viewer.options.show()
    viewer.resize(1500, 1040)
    viewer.apply_properties("axis", "bottom", {"axis_color": "#1f2937", "label_color": "#1d4ed8",
        "major_tick_color": "#c2410c", "minor_tick_color": "#0f766e", "tick_label_color": "#7e22ce",
        "tick_enable_min": .5, "tick_enable_max": 2.5, "major_interval": .5,
        "minor_enabled": True, "minor_count": 4})
    viewer.property_selector.setCurrentText("Axes")
    record = viewer.property_widgets["axis"]
    record["selector"].setCurrentText("bottom")
    app.processEvents()
    for scroll in viewer.properties.findChildren(QScrollArea):
        scroll.ensureWidgetVisible(record["widgets"]["tick_enable_min"][0], 0, 30)
    app.processEvents()
    viewer.canvas.draw()
    assert viewer.grab().save(str(root / "73_graph_axis_colors.png"), "PNG")
    viewer._discard_approved = True
    viewer.close()


if __name__ == "__main__":
    main()
