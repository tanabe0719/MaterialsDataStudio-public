"""Capture the real editor with generated waves only; no desktop/user data."""

from __future__ import annotations

import os
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parents[1]
os.environ["MDS_SETTINGS_DIRECTORY"] = str(ROOT / ".test-tmp/igor-core-capture/settings")
os.environ["MDS_RECOVERY_DIRECTORY"] = str(ROOT / ".test-tmp/igor-core-capture/recovery")

import numpy as np  # noqa: E402
from manual_fonts import configure_capture_fonts  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from materials_data_studio.tools.command_services import DocumentServices  # noqa: E402
from materials_data_studio.tools.graph_waves import empty_document  # noqa: E402
from materials_data_studio.tools.graph_window import GraphEditorWindow  # noqa: E402
from materials_data_studio.ui.theme import style_sheet  # noqa: E402


def main():
    app = QApplication([])
    configure_capture_fonts(app)
    app.setStyleSheet(style_sheet("light"))
    services = DocumentServices(empty_document())
    services.create_wave("Time_s", np.linspace(0, 10, 201))
    services.create_wave("Signal", 0.6 + 0.35 * np.sin(np.linspace(0, 10, 201)))
    image = services.create_wave("Generated_image", np.arange(1.0, 13.0).reshape(3, 4))
    from dataclasses import replace

    services.write([replace(image, dimension_scaling=((0, 4), (1.5, 0.8)))], overwrite=True)
    window = GraphEditorWindow()
    window.resize(1500, 1060)
    window.bind_document(services.document.render_source())
    window._discard_approved = True
    window.show()
    window.command_workspace.execute(
        'Display/T/N=GeneratedMixed Signal vs Time_s; AppendImage Generated_image; SetAxis left 0,5; SetAxis bottom 0,10; SetAxis top 0,10; Label top "Time (s)"; Label left "Signal / image Y"; ModifyGraph rgb(Signal)=(0,0,0),lsize(Signal)=2'
    )
    window.command_workspace.console_dock.hide()
    window.command_workspace.procedure_dock.hide()
    window.select_target("trace", "Signal")
    app.processEvents()
    window.canvas.draw()
    path = ROOT / "docs/screenshots/78_graph_axis_fidelity.png"
    assert window.grab().save(str(path))
    print(path.name)
    window.close()


if __name__ == "__main__":
    main()
