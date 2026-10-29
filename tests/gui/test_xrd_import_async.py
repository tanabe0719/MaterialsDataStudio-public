"""Text preview must finish when opened from a queued import completion."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QDialogButtonBox

from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.modules.xrd.widget import XrdWidget
from materials_data_studio.ui.import_preview import ImportPreview


def test_xy_import_preview_inside_worker_completion(qtbot, monkeypatch, tmp_path):
    source = tmp_path / "generated.xy"
    source.write_text("# synthetic diffraction\n" + "\n".join(f"{x} {x*x}" for x in np.linspace(5, 90, 5000)), encoding="utf-8")
    state = ProjectState()
    widget = XrdWidget(state)
    qtbot.addWidget(widget)
    original = QDialog.exec
    previews = []
    def accept_ready(dialog):
        previews.append(dialog)
        timer = QTimer(dialog)
        timer.setInterval(10)
        def check():
            if dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled():
                timer.stop()
                dialog.accept()
        timer.timeout.connect(check)
        timer.start()
        return original(dialog)
    monkeypatch.setattr(ImportPreview, "exec", accept_ready)
    widget.import_files([source])
    qtbot.waitUntil(lambda: len(state.datasets) == 1, timeout=15000)
    assert len(state.datasets[0].raw_data["x"]) == 5000
    assert not previews
    assert widget.open_phase_search_button.property("primary") == widget.open_peak_fitting_button.property("primary")
    # Section folding owns a body container; check the user-visible action order.
    parent = widget.open_phase_search_button.parentWidget()
    assert parent is not None and parent is widget.open_peak_fitting_button.parentWidget()
    layout = parent.layout()
    assert layout is not None
    assert layout.indexOf(widget.open_phase_search_button) < layout.indexOf(widget.open_peak_fitting_button)
    widget.close()


def test_preview_delimiter_changes_and_bad_input(qtbot):
    dialog = ImportPreview(__import__("pathlib").Path("generated.xy"), b"angle;intensity;sigma\n10;100;2\n11;90;3\n")
    qtbot.addWidget(dialog)
    dialog.delimiter.setCurrentText("Comma")
    dialog.delimiter.setCurrentText("Semicolon")
    qtbot.waitUntil(lambda: dialog.y_column.count() == 3)
    assert dialog.arrays().intensity.tolist() == [100, 90]
    dialog._preview_error("Corrupt input")
    assert dialog.read_status.text() == "Corrupt input"
    dialog.close()
