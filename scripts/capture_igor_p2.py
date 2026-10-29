"""Capture generated-data editor views and scientific operation forms."""
from __future__ import annotations

import os
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parents[1]
os.environ["MDS_SETTINGS_DIRECTORY"] = str(ROOT / ".test-tmp/discovery-capture/settings")
os.environ["MDS_RECOVERY_DIRECTORY"] = str(ROOT / ".test-tmp/discovery-capture/recovery")

from manual_fonts import configure_capture_fonts  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QTreeWidgetItemIterator  # noqa: E402

from materials_data_studio.tools.graph_analysis_dialogs import (  # noqa: E402
    ScientificAnalysisDialog,
)
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
        'Make/D/N=30 GroupA,GroupB;GroupA=sin(p*.7);GroupB=1.2+.5*cos(p*.6);'
        'Make/D/N=(32,24) Image;Image=sin(p*.15)+cos(q*.2);'
        'Display/N=Violin;AppendViolinPlot GroupA,GroupB;ModifyViolinPlot bandwidth=.25,showMean=1,showMedian=1;'
        'Display/N=ImagePlot;AppendImage Image;ColorScale image=Image;'
        'NewLayout/N=FigurePage;AppendLayoutObject/R=(36,36,576,350) graph Violin;AppendLayoutObject/R=(36,390,576,720) graph ImagePlot'
    )
    window.command_workspace.console_dock.hide()
    window.command_workspace.procedure_dock.hide()
    for graph, filename in [("Violin", "85_graph_violin.png"), ("FigurePage", "86_graph_layout.png")]:
        iterator = QTreeWidgetItemIterator(window.tree)
        while iterator.value():
            item = iterator.value()
            if item.data(0, Qt.ItemDataRole.UserRole) == ("graph", graph):
                window.tree.setCurrentItem(item)
                break
            iterator += 1
        window._selection = ("graph", graph)
        window._show_selection()
        if graph == "Violin":
            window.select_target("trace", "GroupA")
        app.processEvents()
        window.canvas.draw()
        assert window.grab().save(str(ROOT / "docs/screenshots" / filename))
    for operation, wave, filename in [("ImageThreshold", "Image", "83_image_threshold.png"), ("StatsTTest", "GroupA", "84_t_test.png")]:
        dialog = ScientificAnalysisDialog(window, window.editor.document, operation)
        for i in range(dialog.waves.count()):
            item = dialog.waves.item(i)
            item.setSelected(item.text().startswith("root:" + wave + " "))
        dialog.preview_result()
        assert "ValueError" not in dialog.preview.toPlainText()
        dialog.show()
        app.processEvents()
        assert dialog.grab().save(str(ROOT / "docs/screenshots" / filename))
        dialog.close()
    window.close()


if __name__ == "__main__":
    main()
