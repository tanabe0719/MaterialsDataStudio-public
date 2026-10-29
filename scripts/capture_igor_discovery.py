"""Capture generated-data editor views and scientific operation forms."""
from __future__ import annotations

import os
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parents[1]
os.environ["MDS_SETTINGS_DIRECTORY"] = str(ROOT / ".test-tmp/discovery-capture/settings")
os.environ["MDS_RECOVERY_DIRECTORY"] = str(ROOT / ".test-tmp/discovery-capture/recovery")

from manual_fonts import configure_capture_fonts  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from materials_data_studio.tools.graph_scientific_dialogs import WaveOperationDialog  # noqa: E402
from materials_data_studio.tools.graph_waves import empty_document  # noqa: E402
from materials_data_studio.tools.graph_window import GraphEditorWindow  # noqa: E402
from materials_data_studio.ui.theme import style_sheet  # noqa: E402


def main():
    app = QApplication([])
    configure_capture_fonts(app)
    app.setStyleSheet(style_sheet("light"))
    window = GraphEditorWindow()
    window.resize(1500, 1060)
    window.bind_document(empty_document().render_source())
    window._discard_approved = True
    window.show()
    window.command_workspace.execute(
        'Make/D/N=60 Time,Signal,Temperature;Time=p*.1;Signal=sin(Time);Temperature=100+10*p;'
        'Display/N=PointColors Signal vs Time;ModifyGraph mode(Signal)=3,marker(Signal)=19,msize(Signal)=3,'
        'zColor(Signal)={Temperature,*,*,Rainbow};ColorScale trace=Signal;Label bottom "Time (s)";Label left "Signal";'
        'Make/D/N=21 GroupA,GroupB;GroupA=sin(p*.7);GroupB=1.2+0.5*cos(p*.6);'
        'Display/N=Distributions;AppendBoxPlot GroupA,GroupB;ModifyBoxPlot notched,showMean=1,jitter=0.4;'
        'Make/D/N=60 ResampleInput;ResampleInput=sin(p*.1)'
    )
    window.command_workspace.console_dock.hide()
    window.command_workspace.procedure_dock.hide()
    for graph, trace, filename in [("PointColors", "Signal", "80_graph_point_colors.png"),
                                   ("Distributions", "GroupA", "81_graph_box_plot.png")]:
        window._selection = ("graph", graph)
        window._show_selection()
        window.select_target("trace", trace)
        app.processEvents()
        window.canvas.draw()
        assert window.grab().save(str(ROOT / "docs/screenshots" / filename))
    dialog = WaveOperationDialog(window, window.editor.document, "Resample")
    for i in range(dialog.waves.count()):
        item = dialog.waves.item(i)
        item.setSelected(item.text().startswith("root:ResampleInput "))
    dialog.up.setValue(2)
    dialog.samples.setValue(3)
    dialog.window_kind.setCurrentText("None")
    dialog.preview_result()
    assert "ValueError" not in dialog.preview.toPlainText()
    dialog.show()
    app.processEvents()
    assert dialog.grab().save(str(ROOT / "docs/screenshots/82_wave_resample.png"))
    dialog.close()
    window.close()


if __name__ == "__main__":
    main()
