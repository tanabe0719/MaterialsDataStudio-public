"""Compact action groups, readable diagnostics and consistent button roles."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QBoxLayout,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from materials_data_studio.ui.localization import tr


def button_role(button: QPushButton, role: str) -> None:
    button.setProperty("primary", role == "primary")
    button.setProperty("destructive", role == "destructive")
    button.setMinimumHeight(28)
    button.setAutoDefault(False)
    style = button.style()
    style.unpolish(button)
    style.polish(button)


class ActionBar(QWidget):
    """Wrap complete semantic groups instead of mixing unrelated actions."""
    def __init__(self, groups: list[list[QWidget]], parent: QWidget | None = None):
        super().__init__(parent)
        self.box = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(12)
        self.box.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.groups: list[tuple[QWidget, QBoxLayout]] = []
        for widgets in groups:
            group = QWidget(self)
            row = QBoxLayout(QBoxLayout.Direction.LeftToRight, group)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(4)
            row.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
            group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
            for widget in widgets:
                row.addWidget(widget)
            self.box.addWidget(group)
            self.groups.append((group, row))
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

    def minimumSizeHint(self):
        return QSize(0, self.box.minimumSize().height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        width = self.width()
        natural = []
        for _group, row in self.groups:
            widgets = [(item.widget() if item is not None else None) for item in (row.itemAt(i) for i in range(row.count()))]
            total = sum(widget.sizeHint().width() for widget in widgets if widget is not None) + 4 * max(0, row.count()-1)
            row.setDirection(QBoxLayout.Direction.TopToBottom if total > width else QBoxLayout.Direction.LeftToRight)
            natural.append(total)
        self.box.setDirection(QBoxLayout.Direction.TopToBottom if sum(natural) + 12 * max(0, len(natural)-1) > width else QBoxLayout.Direction.LeftToRight)
        height = self.box.sizeHint().height()
        if self.minimumHeight() != height:
            self.setMinimumHeight(height)
            self.updateGeometry()


class DiagnosticLabel(QLabel):
    """Keep the list height stable while retaining the complete diagnostic text."""
    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.full_text = text
        self.setProperty("mdsLiteralData", True)
        self.setMaximumHeight(38)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setText(text)

    def setText(self, text: str) -> None:
        self.hint_source = text if text in {"Import or drop measurement files to begin.", "Select a branch to style or analyze."} else ""
        if self.hint_source:
            text = tr(self.hint_source)
        self.full_text = text
        self.setToolTip(text)
        self._render()

    def translate_hint(self, language: str) -> None:
        if self.hint_source:
            from materials_data_studio.ui.localization import JA
            self.full_text = JA.get(self.hint_source, self.hint_source) if language == "ja" else self.hint_source
            self.setToolTip(self.full_text)
            self._render()

    def _render(self):
        text = " · ".join(self.full_text.splitlines())
        super().setText(self.fontMetrics().elidedText(text, Qt.TextElideMode.ElideMiddle, max(50, self.width())))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._render()


def show_details(parent: QWidget, text: str) -> None:
    dialog = QDialog(parent)
    dialog.setWindowTitle(tr("Details"))
    dialog.resize(620, 420)
    layout = QVBoxLayout(dialog)
    view = QTextEdit()
    view.setReadOnly(True)
    view.setPlainText(text)
    layout.addWidget(view)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.exec()
    dialog.deleteLater()


def diagnostic_row(label: DiagnosticLabel, owner: QWidget) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(label, 1)
    details = QPushButton("Details…")
    # Dataset refresh may add a filename/warnings tooltip. Retain the complete
    # primary diagnostic as well, even when its on-screen label is elided.
    details.clicked.connect(lambda: show_details(owner, "\n".join(dict.fromkeys(filter(None, (label.full_text, label.toolTip()))))))
    layout.addWidget(details)
    return row


def palette_section(combo: QWidget, buttons: list[QWidget], key: str) -> QWidget:
    """Keep optional color tools accessible without crowding the dataset list."""
    from materials_data_studio.core.settings import create_settings
    host = QWidget()
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    toggle = QToolButton()
    toggle.setText("Series colors")
    toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    toggle.setCheckable(True)
    body = QWidget()
    fields = QVBoxLayout(body)
    fields.setContentsMargins(0, 0, 0, 0)
    fields.setSpacing(4)
    fields.addWidget(combo)
    fields.addWidget(ActionBar([buttons]))
    def expand(shown):
        body.setVisible(shown)
        toggle.setArrowType(Qt.ArrowType.DownArrow if shown else Qt.ArrowType.RightArrow)
        create_settings().setValue(f"ui/datasetColors/{key}", shown)
    toggle.toggled.connect(expand)
    layout.addWidget(toggle)
    layout.addWidget(body)
    shown = str(create_settings().value(f"ui/datasetColors/{key}", "false")).lower() == "true"
    toggle.setChecked(shown)
    expand(shown)
    return host
