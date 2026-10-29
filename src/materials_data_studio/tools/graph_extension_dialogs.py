"""Small semantic forms for graph, page, cursor and table workflows."""
from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QMessageBox

from materials_data_studio.tools.command_engine import CommandEngine
from materials_data_studio.tools.command_services import DocumentServices
from materials_data_studio.tools.graph_scientific_dialogs import object_name

FORMS = {
    "Cursor": [("Cursor A..J", "A"), ("Trace", ""), ("Point index", "0")],
    "Tag": [("Trace", ""), ("Point index", "0"), ("Text (ON/OP/OX/OY escapes supported)", "value=\\OY"), ("Arrow mode 0..4", "2"), ("X offset (%)", "5"), ("Y offset (%)", "5")],
    "XY Error Bars": [("Trace", ""), ("X lower wave", ""), ("X upper wave", ""), ("Y lower wave", ""), ("Y upper wave", ""), ("Line width (pt)", "1"), ("Cap size (pt)", "2")],
    "Image Coordinates": [("Image wave", ""), ("X edge wave (*=scaling)", "*"), ("Y edge wave (*=scaling)", "*")],
    "Date Axis": [("Axis", "bottom"), ("Suppress 0=none,1=date,2=time", "0"), ("Time 0=AM/PM,1=24h,2=elapsed", "1"), ("Date 0=ISO,1=long,2=abbreviated", "0")],
    "Table Format": [("Table", ""), ("Column wave", ""), ("Format 0..10", "3"), ("Digits", "6"), ("Width", "90"), ("Show units 0/1", "1")],
    "Contour": [("Matrix wave", ""), ("Manual levels (comma separated; blank=auto)", ""), ("Fill 0/1", "0")],
    "Layout": [("New page name", "Layout0"), ("Graph to place", ""), ("Left,top,right,bottom (pt)", "36,36,576,396")],
    "Subwindow": [("Child name", "Inset0"), ("Y wave", ""), ("Left,top,right,bottom (host fraction)", ".55,.05,.95,.45")],
    "Rectangular ROI": [("Image wave", ""), ("Output ROI mask", "roi"), ("First,last row", "0,10"), ("First,last column", "0,10")],
}


class GraphExtensionDialog(QDialog):
    def __init__(self, parent: Any, document: Any, operation: str, graph: str = "") -> None:
        super().__init__(parent)
        self.document, self.operation, self.graph = document, operation, graph
        self.command = ""
        self.setWindowTitle(operation)
        self.resize(640, 260)
        form = QFormLayout(self)
        self.fields = []
        for caption, default in FORMS[operation]:
            field = QLineEdit(default)
            self.fields.append(field)
            form.addRow(caption, field)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def build_command(self) -> str:
        import json
        values = [f.text().strip() for f in self.fields]
        n = object_name
        w = "/W=" + n(self.graph) if self.graph else ""
        op = self.operation
        if op == "Cursor":
            return f"Cursor{w}/P {n(values[0])},{n(values[1])},{int(values[2])}"
        if op == "Tag":
            return f"Tag{w}/N=tag0/L={int(values[3])}/X={float(values[4])}/Y={float(values[5])} {n(values[0])},{int(values[1])},{json.dumps(values[2])}"
        if op == "XY Error Bars":
            return f"ErrorBars{w}/L={float(values[5])}/Y={float(values[6])} {n(values[0])} XY,wave=({n(values[1])},{n(values[2])}),wave=({n(values[3])},{n(values[4])})"
        if op == "Image Coordinates":
            return f"SetImageCoordinates{w} {n(values[0])}," + ",".join("*" if v == "*" else n(v) for v in values[1:])
        if op == "Date Axis":
            return f"ModifyGraph{w} dateInfo({n(values[0])})={{{int(values[1])},{int(values[2])},{int(values[3])}}}"
        if op == "Table Format":
            ref = n(values[1])
            return f"ModifyTable/W={n(values[0])} format({ref})={int(values[2])},digits({ref})={int(values[3])},width({ref})={float(values[4])},showUnits({ref})={int(values[5])}"
        if op == "Contour":
            return f"AppendMatrixContour{w} {n(values[0])};ModifyContour{w} {n(values[0])}," + ("autoLevels={0,0,0},moreLevels={" + ",".join(str(float(v)) for v in values[1].split(',')) + "}," if values[1] else "") + f"fill={int(values[2])}"
        if op == "Layout":
            rect = ",".join(str(float(v)) for v in values[2].split(','))
            return f"NewLayout/N={n(values[0])};AppendLayoutObject/R=({rect}) graph {n(values[1])}"
        if op == "Subwindow":
            rect = ",".join(str(float(v)) for v in values[2].split(','))
            return f"Display/HOST={n(self.graph)}/N={n(values[0])}/W=({rect}) {n(values[1])}"
        wave = self.document.render_source().wave(values[0])
        if wave is None or wave.data.ndim != 2:
            raise ValueError("Select a 2D image wave")
        row, column = ([int(v) for v in pair.split(',')] for pair in values[2:])
        if not 0 <= row[0] <= row[1] < wave.shape[0] or not 0 <= column[0] <= column[1] < wave.shape[1]:
            raise ValueError("ROI bounds must lie inside the image")
        dest = n(values[1])
        return f"Make/B/U/N=({wave.shape[0]},{wave.shape[1]}) {dest};{dest}=1-((p>={row[0]})&&(p<={row[1]})&&(q>={column[0]})&&(q<={column[1]}))"

    def apply(self) -> None:
        try:
            self.command = self.build_command()
            self.accept()
        except (ValueError, IndexError) as exc:
            QMessageBox.warning(self, self.operation, str(exc))


def extension_action(window: Any, operation: str) -> None:
    from materials_data_studio.tools.graph_actions import ensure_document
    ensure_document(window)
    document = window.editor.document
    selected = window.graph()
    dialog = GraphExtensionDialog(window, document, operation, selected.name if selected else "")
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return
    before = document.current
    result = CommandEngine(DocumentServices(document)).execute(dialog.command)
    if not result.success:
        QMessageBox.warning(window, operation, result.message)
        return
    after, document.current = document.current, before
    window.editor.commit(before, after, operation)
