"""Capture Analysis/Statistics documentation with generated data only."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QPoint
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QApplication

from materials_data_studio.tools.graph_analysis_forms import AnalysisDialog
from materials_data_studio.tools.graph_window import GraphEditorWindow
from materials_data_studio.ui.theme import style_sheet

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["MDS_SETTINGS_DIRECTORY"] = str(ROOT / ".test-tmp/analysis-capture/settings")
    os.environ["MDS_RECOVERY_DIRECTORY"] = str(ROOT / ".test-tmp/analysis-capture/recovery")
    app = QApplication.instance() or QApplication([])
    from manual_fonts import configure_capture_fonts
    configure_capture_fonts(app)
    app.setStyle("Fusion")
    app.setStyleSheet(style_sheet("light"))
    window = GraphEditorWindow()
    window.resize(1400, 850)
    window.command_workspace.execute("Make/D/N=41 position,signal,reference;position=p/4;signal=2+3*position+0.3*sin(2*position);reference=1+2*position+sin(position);Display/N=Generated_Comparison signal vs position")
    window.show()
    app.processEvents()
    for menu,filename in [(window.analysis_menu,"87_analysis_menu.png"),(window.statistics_menu,"88_statistics_menu.png")]:
        menu.popup(window.mapToGlobal(QPoint(150,55)))
        app.processEvents()
        capture=window.grab()
        painter=QPainter(capture)
        painter.drawPixmap(window.mapFromGlobal(menu.pos()),menu.grab())
        painter.end()
        assert capture.save(str(ROOT/"docs/screenshots"/filename),"PNG")
        menu.close()
    dialog=AnalysisDialog(window,window.editor.document,"anova")
    dialog.resize(850,740)
    for i in (1,2):
        dialog.waves.item(i).setSelected(True)
    dialog.method.setCurrentIndex(1)
    dialog.show()
    dialog.preview_result()
    app.processEvents()
    assert dialog.grab().save(str(ROOT/"docs/screenshots/89_statistics_form.png"),"PNG")
    dialog.close()
    window._discard_approved=True
    window.close()


if __name__ == "__main__":
    main()
