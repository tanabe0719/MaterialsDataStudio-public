"""Capture the real Graph Editor with generated data; no user files or desktop."""
from __future__ import annotations

import os
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parents[1]
os.environ["MDS_SETTINGS_DIRECTORY"] = str(ROOT / ".test-tmp/igor-extended-capture/settings")
os.environ["MDS_RECOVERY_DIRECTORY"] = str(ROOT / ".test-tmp/igor-extended-capture/recovery")

from manual_fonts import configure_capture_fonts  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

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
    result = window.command_workspace.execute(
        'Make/D/N=201 Time_s,Signal;Time_s=p*0.05;Signal=0.6+0.35*sin(Time_s);'
        'Display/N=GeneratedTypography Signal vs Time_s;'
        'Label bottom "Time (s)";Label left "Signal (a.u.)";'
        'ModifyGraph gFont="Arial",gfSize=12,gfMult=100,gmSize=4,fSize=14,'
        'mode(Signal)=4,marker(Signal)=8,msize(Signal)=3,'
        'rgb(Signal)=(10000,24000,46000),mirror=1,tick=2,minor=1,'
        'lblPosMode(left)=1,lblPos(left)=40,lblLatPos(left)=27;'
        'Legend/N=note/F=2/A=RT "\\s(Signal) Generated signal"'
    )
    if result is not None and hasattr(result, "success") and not result.success:
        raise RuntimeError(result.message)
    window.command_workspace.console_dock.hide()
    window.command_workspace.procedure_dock.hide()
    window.select_target("axis", "left")
    app.processEvents()
    window.canvas.draw()
    path = ROOT / "docs/screenshots/79_graph_extended_fidelity.png"
    assert window.grab().save(str(path))
    print(path.name)
    window.close()


if __name__ == "__main__":
    main()
