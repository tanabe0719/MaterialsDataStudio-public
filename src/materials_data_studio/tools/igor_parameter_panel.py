"""Safe MDS forms for converted Igor panel metadata; callbacks run in isolation."""
from __future__ import annotations

import ast
import html
import math
import re
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent, QResizeEvent
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

ENTRY_MARKER = "# Explicit entry point; runs only when Run All is pressed."


class NumericParameterEdit(QLineEdit):
    """Keep free-form scientific input while providing Igor-like step buttons."""
    def __init__(self, value: str, parent: QWidget | None = None, *, limits: Any = None) -> None:
        super().__init__(value, parent)
        self.limits = limits
        self.up = QToolButton(self)
        self.down = QToolButton(self)
        self.up.setArrowType(Qt.ArrowType.UpArrow)
        self.down.setArrowType(Qt.ArrowType.DownArrow)
        self.up.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.down.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.up.setAccessibleName("Increase value")
        self.down.setAccessibleName("Decrease value")
        self.up.clicked.connect(lambda: self.step(1))
        self.down.clicked.connect(lambda: self.step(-1))
        self.setTextMargins(0, 0, 18, 0)
        self.setStyleSheet("QLineEdit { padding: 0px 2px; } QToolButton { padding: 0px; border-radius: 0px; }")

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        height = max(1, (self.height() - 2) // 2)
        self.up.setGeometry(self.width() - 17, 1, 16, height)
        self.down.setGeometry(self.width() - 17, height + 1, 16, self.height() - height - 2)

    def step(self, direction: int) -> None:
        try:
            value = float(self.text())
            low, high, increment = self.limits if self.limits is not None else (-math.inf, math.inf, 1.0)
            low = -math.inf if low is None else low
            high = math.inf if high is None else high
            value = max(low, min(high, value + direction * increment))
            if math.isfinite(value):
                self.setText(format(value, ".15g"))
        except (ValueError, TypeError, OverflowError):
            return

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self.step(1 if event.key() == Qt.Key.Key_Up else -1)
            return
        super().keyPressEvent(event)


def _geometry(value: Any, length: int, label: str) -> tuple[float, ...] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise ValueError(f"Invalid converted panel {label}")
    if any(type(part) not in (int, float) or not math.isfinite(part) or abs(part) > 100_000 for part in value):
        raise ValueError(f"Invalid converted panel {label}")
    return tuple(float(part) for part in value)


def _color(value: Any, *, native_default: bool = False) -> str | None:
    if not isinstance(value, (list, tuple)) or len(value) != 3 or not all(type(part) in (int, float) and 0 <= part <= 65535 for part in value):
        return None
    if native_default and not any(value):
        return None
    rgb = tuple(round(part / 257) for part in value)
    return f"rgb({rgb[0]},{rgb[1]},{rgb[2]})"


def callback_source(source: str, callable_name: str) -> str:
    """A manifest can select a converted definition, never inject an expression."""
    if not re.fullmatch(r"mdsRoutine[1-9]\d*", callable_name):
        raise ValueError("The callback is not a converted MDS routine")
    tree = ast.parse(source)
    functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    if callable_name not in functions:
        raise ValueError("The callback is not defined in the current Python source; reconvert its Igor source")
    lines = source.splitlines()
    markers = [index for index, line in enumerate(lines) if line == ENTRY_MARKER]
    if len(markers) != 1:
        raise ValueError("The converted Python entry marker is missing or ambiguous; reconvert before running a callback")
    output = "\n".join(lines[:markers[0] + 1]) + f"\n{callable_name}({{'eventCode': 2}})\n"
    from materials_data_studio.tools.procedure_policy import validate
    validate(output)
    return output


def panel_metadata(value: Any) -> list[dict[str, Any]]:
    """Keep untrusted document metadata bounded and purely descriptive."""
    if not isinstance(value, list) or len(value) > 64:
        raise ValueError("Converted panel metadata must contain at most 64 panels")
    panels = []
    for panel in value:
        if not isinstance(panel, dict):
            raise ValueError("Invalid converted panel metadata")
        fields, buttons, groups = panel.get("fields", []), panel.get("buttons", []), panel.get("groups", [])
        if not isinstance(fields, list) or len(fields) > 256 or not isinstance(buttons, list) or len(buttons) > 64:
            raise ValueError("A parameter panel is limited to 256 fields and 64 buttons")
        if not isinstance(groups, list) or len(groups) > 64:
            raise ValueError("A parameter panel is limited to 64 groups")
        _geometry(panel.get("rect"), 4, "rectangle")
        for item in [panel, *fields, *buttons, *groups]:
            if not isinstance(item, dict) or any(not isinstance(key, str) for key in item):
                raise ValueError("Invalid parameter controls")
            for key in ("name", "title", "path", "callback", "python_callable"):
                text = item.get(key, "")
                if not isinstance(text, str) or len(text) > 4096 or "\0" in text:
                    raise ValueError("Invalid parameter control text")
            _geometry(item.get("pos"), 2, "position")
            size = _geometry(item.get("size"), 2, "size")
            if size is not None and min(size) <= 0:
                raise ValueError("Parameter control sizes must be positive")
        for item in fields:
            path = item.get("path", "")
            if not path.startswith("root:") or any(char in path for char in "\r\n"):
                raise ValueError("Parameter fields must identify full root: variable paths")
            if item.get("value_type", "number") not in ("number", "variable", "string"):
                raise ValueError("Parameter fields require numeric or text global variables")
            limits = item.get("limits")
            if limits is not None:
                if not isinstance(limits, (list, tuple)) or len(limits) != 3 or any(part is not None and (type(part) not in (int, float) or not math.isfinite(part)) for part in limits):
                    raise ValueError("Invalid numeric parameter limits")
                low, high, increment = limits
                if increment is None or increment < 0 or (low is not None and high is not None and low > high):
                    raise ValueError("Invalid numeric parameter limits")
        panels.append(panel)
    return panels


class IgorParameterDialog(QDialog):
    def __init__(self, workspace: Any) -> None:
        super().__init__(workspace.window)
        self.workspace = workspace
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle("Converted Igor macro parameters")
        self.resize(600, 620)
        self.editor = workspace.window.editor
        self.fields: dict[str, QLineEdit] = {}
        self._field_controls: list[QLineEdit] = []
        self._field_types: dict[str, str] = {}
        self._field_limits: dict[str, Any] = {}
        self.callback_buttons: list[QPushButton] = []
        self._signature = ""
        layout = QVBoxLayout(self)
        note = QLabel("Edit the macro parameters, then click its action button. Callbacks run in the isolated Procedure environment; requested files are selected interactively.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Close)
        self.apply_button = buttons.button(QDialogButtonBox.StandardButton.Apply)
        self.apply_button.clicked.connect(self.apply_parameters)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self) -> None:
        editor = self.workspace.window.editor
        if editor is not self.editor:
            self.close()
            return
        environment = editor.document.current.environment
        manifest = environment.get("igor_parameter_panels", [])
        variables = environment.get("variables", {})
        signature = repr((manifest, variables, environment.get("strings", {})))
        if signature == self._signature:
            self.set_running(self.workspace.is_running())
            return
        self._signature = signature
        while self.tabs.count():
            page = self.tabs.widget(0)
            self.tabs.removeTab(0)
            if page is not None:
                page.deleteLater()
        self.fields.clear()
        self._field_types.clear()
        self._field_limits.clear()
        self._field_controls.clear()
        self.callback_buttons.clear()
        try:
            panels = panel_metadata(manifest)
            if not isinstance(variables, dict):
                raise ValueError("Invalid global variable metadata")
            strings = environment.get("strings", {})
            if not isinstance(strings, dict):
                raise ValueError("Invalid global text metadata")
            for field in (field for panel in panels for field in panel.get("fields", [])):
                is_text = field.get("value_type", "number") == "string"
                value = (strings if is_text else variables).get(field["path"])
                if is_text and (not isinstance(value, str) or len(value) > 4096):
                    raise ValueError(f"Missing or invalid text variable: {field['path']}")
                if not is_text and (not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value)):
                    raise ValueError(f"Missing or non-finite numeric variable: {field['path']}")
            for panel in panels:
                page = QWidget()
                source_layout = panel.get("rect") is not None and all(
                    item.get("pos") is not None and item.get("size") is not None
                    for item in [*panel.get("fields", []), *panel.get("buttons", []), *panel.get("groups", [])]
                )
                # Source coordinates are logical Igor points, not screen pixels.
                # Qt performs physical-DPI scaling; expand only for font metrics.
                scale = max(1.0, page.fontMetrics().height() / 16.0)
                form = None
                layout = None
                if source_layout:
                    rect = panel["rect"]
                    page.setMinimumSize(round(max(120, rect[2] - rect[0]) * scale), round(max(80, rect[3] - rect[1]) * scale))
                    for group in panel.get("groups", []):
                        control = QGroupBox(group.get("title", ""), page)
                        self._place(control, group, scale)
                        if color := _color(group.get("fcolor"), native_default=True):
                            control.setStyleSheet(f"QGroupBox {{ color: {color}; }}")
                else:
                    layout = QVBoxLayout(page)
                    box = QGroupBox("Global variables")
                    form = QFormLayout(box)
                    layout.addWidget(box)
                for field in panel.get("fields", []):
                    path = field["path"]
                    value_type = field.get("value_type", "number")
                    value = (strings if value_type == "string" else variables).get(path)
                    edit = QLineEdit(str(value)) if value_type == "string" else NumericParameterEdit(str(value), limits=field.get("limits"))
                    edit.setMaxLength(4096 if value_type == "string" else 128)
                    edit.setToolTip("<p>" + html.escape(path) + "</p>")
                    edit.setAccessibleName(field.get("title") or field.get("name") or path)
                    label = QLabel(field.get("title") or field.get("name") or path)
                    label.setTextFormat(Qt.TextFormat.PlainText)
                    if color := _color(field.get("fcolor"), native_default=True):
                        label.setStyleSheet(f"color: {color};")
                    if color := _color(field.get("valuecolor")):
                        edit.setStyleSheet(edit.styleSheet() + f" QLineEdit {{ color: {color}; }}")
                    if source_layout:
                        if value_type == "string":
                            edit.setStyleSheet(edit.styleSheet() + " QLineEdit { padding: 0px 2px; }")
                        row = QWidget(page)
                        row_layout = QHBoxLayout(row)
                        row_layout.setContentsMargins(0, 0, 0, 0)
                        row_layout.setSpacing(4)
                        row_layout.addWidget(label)
                        row_layout.addWidget(edit, 1)
                        self._place(row, field, scale)
                    elif form is not None:
                        form.addRow(label, edit)
                    if path in self.fields:
                        previous = self.fields[path]
                        previous.textChanged.connect(edit.setText)
                        edit.textChanged.connect(previous.setText)
                    self.fields[path] = edit
                    self._field_types[path] = value_type
                    self._field_limits[path] = field.get("limits")
                    self._field_controls.append(edit)
                for button in panel.get("buttons", []):
                    control = QPushButton(button.get("title") or button.get("name") or "Run callback", page)
                    control.setAutoDefault(False)
                    control.setToolTip("<p>Explicitly run " + html.escape(button.get("callback", "callback")) + " in the isolated Procedure environment</p>")
                    control.clicked.connect(lambda _checked=False, item=button: self.run_callback(item))
                    self.callback_buttons.append(control)
                    if source_layout:
                        self._place(control, button, scale)
                    elif layout is not None:
                        layout.addWidget(control)
                    face = button.get("fcolor", button.get("bcolor"))
                    if color := _color(face, native_default="fcolor" in button):
                        # Igor fColor is the button face. Theme foregrounds may
                        # otherwise turn a pale face + white text unreadable.
                        luminance = sum(weight * part / 257 for weight, part in zip((.2126, .7152, .0722), face, strict=True))
                        foreground = _color(button.get("valuecolor")) or ("#111827" if luminance >= 140 else "#ffffff")
                        control.setStyleSheet(f"QPushButton {{ background-color: {color}; color: {foreground}; }}")
                if layout is not None:
                    layout.addStretch()
                scroll = QScrollArea()
                scroll.setWidgetResizable(True)
                scroll.setWidget(page)
                self.tabs.addTab(scroll, panel.get("title") or panel.get("name") or "Macro parameters")
            self.tabs.tabBar().setVisible(len(panels) > 1)
            if len(panels) == 1:
                self.setWindowTitle(panels[0].get("title") or "Converted Igor macro parameters")
                if panels[0].get("rect") is not None:
                    rect = panels[0]["rect"]
                    self.resize(min(1200, max(400, round(rect[2] - rect[0]) + 40)), min(900, max(260, round(rect[3] - rect[1]) + 145)))
            self.status.setText("No converted parameter panels. Run the converted panel-initialization routine first." if not panels else "Edits are local to this Graph Document and support Undo. Callback buttons do not run automatically.")
        except (ValueError, TypeError, OverflowError) as exc:
            self.status.setText(str(exc))
            self.fields.clear()
            self._field_controls.clear()
            self.callback_buttons.clear()
        self.set_running(self.workspace.is_running())

    @staticmethod
    def _place(control: QWidget, metadata: dict[str, Any], scale: float) -> None:
        position, size = metadata["pos"], metadata["size"]
        control.setGeometry(round(position[0] * scale), round(position[1] * scale), round(size[0] * scale), round(size[1] * scale))

    def set_running(self, running: bool) -> None:
        self.apply_button.setEnabled(not running and bool(self.fields))
        for control in [*self._field_controls, *self.callback_buttons]:
            control.setEnabled(not running)

    def apply_parameters(self) -> bool:
        if self.workspace.is_running() or self.workspace.window.editor is not self.editor:
            self.status.setText("Stop the current Procedure before applying parameters.")
            return False
        try:
            values = {path: edit.text() if self._field_types[path] == "string" else float(edit.text()) for path, edit in self.fields.items()}
            if not all(isinstance(value, str) or math.isfinite(value) for value in values.values()):
                raise ValueError("Parameter values must be finite numbers")
            for path, value in values.items():
                limits = self._field_limits.get(path)
                if limits is not None and not isinstance(value, str):
                    low = -math.inf if limits[0] is None else limits[0]
                    high = math.inf if limits[1] is None else limits[1]
                    if not low <= value <= high:
                        raise ValueError(f"Parameter value is outside its limits: {path}")
            before = self.editor.document.current
            for path in values:
                section = before.environment.get("strings" if self._field_types[path] == "string" else "variables", {})
                if not isinstance(section, dict) or path not in section:
                    raise ValueError("The parameter globals changed; refresh the panel")
            after = before.clone()
            for path, value in values.items():
                after.environment.setdefault("strings" if isinstance(value, str) else "variables", {})[path] = value
            self._signature = ""
            self.editor.commit(before, after, "Edit converted Igor parameters")
            self.status.setText("Parameter values applied. No callback has run.")
            return True
        except (ValueError, TypeError) as exc:
            self.status.setText(str(exc))
            return False

    def run_callback(self, button: dict[str, str]) -> None:
        try:
            source = callback_source(self.workspace.code.toPlainText(), button.get("python_callable", ""))
        except (ValueError, SyntaxError) as exc:
            self.status.setText(str(exc))
            return
        if self.apply_parameters():
            self.workspace.code.setPlainText(source)
            self.workspace.flush()
            self.workspace.tabs.setCurrentIndex(0)
            self.workspace.start(source)
