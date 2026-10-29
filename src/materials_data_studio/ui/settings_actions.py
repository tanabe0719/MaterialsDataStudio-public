"""A context-bound execution button remains reachable while settings scroll."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from materials_data_studio.ui.action_layout import ActionBar, button_role, show_details
from materials_data_studio.ui.localization import tr


class ExecutionFooter(QWidget):
    def __init__(self, cards: list[QGroupBox], parent: QWidget):
        super().__init__(parent)
        self.entries: list[tuple[QGroupBox, QPushButton]] = []
        self.current = 0
        self.pending = False
        for card in cards:
            for button in card.findChildren(QPushButton):
                source = str(button.property("mdsCaptionSource") or button.text())
                if source.startswith(("Run ", "Integrate ")):
                    self.entries.append((card, button))
                    button_role(button, "primary")
                elif source.startswith(("Reset ", "Clear fit")):
                    button.setProperty("supportAction", True)
                    button_role(button, "secondary")
            card.installEventFilter(self)
            for child in card.findChildren(QWidget):
                child.installEventFilter(self)
            for view in card.findChildren(QPlainTextEdit):
                if view.isReadOnly():
                    view.textChanged.connect(self.refresh)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 5, 4, 4)
        layout.setSpacing(4)
        self.context = QLabel()
        self.context.setProperty("scopeBadge", True)
        self.context.setProperty("mdsLiteralData", True)
        self.context.setWordWrap(True)
        layout.addWidget(self.context)
        self.selector = QComboBox()
        self.selector.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.selector.setMinimumContentsLength(10)
        self.selector.setProperty("mdsLiteralData", True)
        self.selector.addItems([card.title() for card, _ in self.entries])
        self.selector.currentIndexChanged.connect(self.select)
        self.selector.setVisible(len(self.entries) > 1)
        layout.addWidget(self.selector)
        self.execute = QPushButton()
        self.execute.setProperty("mdsLiteralData", True)
        button_role(self.execute, "primary")
        self.execute.clicked.connect(self.run)
        self.details = QPushButton("Result details…")
        self.details.clicked.connect(self.result_details)
        layout.addWidget(ActionBar([[self.execute], [self.details]]))
        self.result = QLabel()
        self.result.setProperty("mdsLiteralData", True)
        self.result.setMaximumHeight(30)
        layout.addWidget(self.result)
        self.setVisible(bool(self.entries))
        self.refresh()

    def eventFilter(self, watched: QObject, event) -> bool:
        if event.type() in {QEvent.Type.FocusIn, QEvent.Type.MouseButtonPress}:
            for index, (card, _button) in enumerate(self.entries):
                if watched is card or (isinstance(watched, QWidget) and card.isAncestorOf(watched)):
                    self.current = index
                    break
        if event.type() in {QEvent.Type.FocusIn, QEvent.Type.MouseButtonPress, QEvent.Type.EnabledChange, QEvent.Type.LayoutRequest, QEvent.Type.Show, QEvent.Type.Hide} and not self.pending:
            self.pending = True
            QTimer.singleShot(0, self.refresh)
        return False

    def refresh(self):
        from shiboken6 import isValid
        if not isValid(self):
            return
        self.pending = False
        if not self.entries:
            return
        card, button = self.entries[self.current]
        blocked = self.selector.blockSignals(True)
        for index, (entry_card, _button) in enumerate(self.entries):
            self.selector.setItemText(index, entry_card.title())
        self.selector.setCurrentIndex(self.current)
        self.selector.blockSignals(blocked)
        self.context.setText(tr("Active analysis") + ": " + card.title())
        self.execute.setText(button.text())
        self.execute.setEnabled(card.isEnabled() and button.isEnabled())
        labels = [label for label in card.findChildren(QLabel) if label.wordWrap() and not label.property("scopeBadge")]
        self.result_text = "\n".join(label.text() for label in labels if label.text())
        text_results = [view.toPlainText() for view in card.findChildren(QPlainTextEdit) if view.isReadOnly()]
        if text_results:
            self.result_text = "\n".join(filter(None, text_results))
        self.result.setText(self.result.fontMetrics().elidedText(self.result_text.replace("\n", " · "), Qt.TextElideMode.ElideRight, max(100, self.width()-12)))
        self.result.setToolTip(self.result_text)
        self.details.setEnabled(bool(self.result_text))

    def run(self):
        if self.entries:
            card, button = self.entries[self.current]
            if card.isEnabled() and button.isEnabled():
                button.click()
                self.refresh()

    def result_details(self):
        show_details(self, getattr(self, "result_text", ""))

    def refresh_language(self):
        self.refresh()

    def select(self, index: int):
        if 0 <= index < len(self.entries):
            self.current = index
            self.refresh()
