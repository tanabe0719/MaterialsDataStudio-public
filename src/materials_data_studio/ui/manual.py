"""In-application rendering of the bundled Markdown user manual."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QDesktopServices,
    QImage,
    QMouseEvent,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)


class ManualTextBrowser(QTextBrowser):
    """QTextBrowser with deterministic mouse activation for custom manual links."""

    manualLinkActivated = Signal(QUrl)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        anchor = self.anchorAt(event.position().toPoint())
        super().mouseReleaseEvent(event)
        if anchor:
            self.manualLinkActivated.emit(QUrl(anchor))


def resolve_user_manual_path(language: str = "en") -> Path:
    """Locate one localized manual in a source checkout or bundle."""
    filename = {
        "en": "user_manual_en.md",
        "ja": "user_manual.md",
    }.get(language)
    if filename is None:
        raise ValueError(f"Unsupported manual language: {language}")
    return _resolve_manual_path(filename)


def resolve_graph_manual_path(language: str = "en") -> Path:
    """Locate the detailed Graph Viewer guide, independently of the main guide."""
    filename = {"en": "graph_viewer_manual_en.md", "ja": "graph_viewer_manual.md"}.get(language)
    if filename is None:
        raise ValueError(f"Unsupported manual language: {language}")
    return _resolve_manual_path(filename)


def _resolve_manual_path(filename: str) -> Path:
    bundle_root = getattr(sys, "_MEIPASS", None)
    candidates = [
        Path(str(bundle_root)) / "docs" / filename
        if bundle_root is not None
        else None,
        Path(__file__).resolve().parents[3] / "docs" / filename,
        Path.cwd() / "docs" / filename,
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "The bundled user manual was not found. Reinstall Materials Data Studio "
        "or keep the complete application folder together."
    )


class UserManualDialog(QDialog):
    """Searchable Markdown manual displayed without leaving the application."""

    def __init__(self, manual_path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle({
            "user_manual.md": "ユーザーマニュアル",
            "graph_viewer_manual.md": "Graph Viewer マニュアル",
            "graph_viewer_manual_en.md": "Graph Viewer Manual",
        }.get(manual_path.name, "User Manual"))
        self.resize(1120, 820)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find in manual…")
        self.find_next_button = QPushButton("Find Next")
        self.find_next_button.setAutoDefault(False)
        self.find_status = QLabel()
        self.browser = ManualTextBrowser()
        self.browser.setOpenLinks(False)
        self.browser.setOpenExternalLinks(False)
        document = self.browser.document()
        document.setBaseUrl(
            QUrl.fromLocalFile(f"{manual_path.parent.resolve().as_posix()}/")
        )
        markdown = manual_path.read_text(encoding="utf-8")
        self._section_titles = {
            f"section-{number.replace('.', '-')}": f"{number}. {title.strip()}"
            for number, title in re.findall(
                r"^#{2,6}\s+(\d+(?:\.\d+)*)\.\s+(.+)$",
                markdown,
                flags=re.MULTILINE,
            )
        }
        image_resources: list[tuple[QUrl, QImage]] = []

        def scaled_image(match: re.Match[str]) -> str:
            alt_text, relative_source = match.groups()
            image_path = (manual_path.parent / relative_source).resolve()
            if not image_path.is_file():
                return match.group(0)
            image = QImage(str(image_path))
            if image.isNull():
                return match.group(0)
            if image.width() > 1000:
                image = image.scaledToWidth(
                    1000, Qt.TransformationMode.SmoothTransformation
                )
            resource_url = QUrl(f"manual-image:{image_path.name}")
            image_resources.append((resource_url, image))
            return f"![{alt_text}]({resource_url.toString()})"

        markdown = re.sub(
            r"!\[([^\]]*)\]\((screenshots/[^)]+\.png)\)",
            scaled_image,
            markdown,
        )
        # QTextBrowser does not reliably activate fragment-only Markdown links
        # when the corresponding headings have no generated HTML id. Route
        # manual sections through a dedicated scheme and locate the heading
        # text ourselves in _open_manual_link.
        markdown = re.sub(
            r"\]\(#(section-[0-9-]+)\)",
            r"](manual:\1)",
            markdown,
        )
        self.browser.setMarkdown(markdown)
        for resource_url, image in image_resources:
            document.addResource(
                QTextDocument.ResourceType.ImageResource,
                resource_url,
                image,
            )
        document.markContentsDirty(0, document.characterCount())
        # Add real QTextDocument anchor targets as well as keeping a cursor map.
        # This avoids relying on the platform-dependent Markdown fragment logic
        # that failed in some packaged Windows builds.
        self._section_positions: dict[str, int] = {}
        remaining_sections = dict(self._section_titles)
        block = document.begin()
        while block.isValid() and remaining_sections:
            if block.blockFormat().headingLevel() > 0:
                heading = block.text().strip()
                for section, target in tuple(remaining_sections.items()):
                    if heading != target:
                        continue
                    self._section_positions[section] = block.position()
                    cursor = QTextCursor(block)
                    cursor.movePosition(
                        QTextCursor.MoveOperation.EndOfBlock,
                        QTextCursor.MoveMode.KeepAnchor,
                    )
                    anchor_format = QTextCharFormat(cursor.charFormat())
                    anchor_format.setAnchor(True)
                    anchor_format.setAnchorNames([section])
                    cursor.mergeCharFormat(anchor_format)
                    del remaining_sections[section]
                    break
            block = block.next()
        self.browser.manualLinkActivated.connect(self._open_manual_link)
        self.search.returnPressed.connect(self.find_next)
        self.find_next_button.clicked.connect(self.find_next)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_button is not None:
            # Otherwise Return in the search field also activates Close because
            # QDialog promotes the first auto-default button.
            close_button.setAutoDefault(False)
            close_button.setDefault(False)
        buttons.rejected.connect(self.close)
        layout = QVBoxLayout(self)
        search_row = QHBoxLayout()
        search_row.addWidget(self.search, 1)
        search_row.addWidget(self.find_next_button)
        search_row.addWidget(self.find_status)
        layout.addLayout(search_row)
        layout.addWidget(self.browser, 1)
        layout.addWidget(buttons)

    def _open_manual_link(self, url: QUrl) -> None:
        """Follow generated section links locally and delegate web links."""
        if url.scheme() == "manual":
            section = url.path().lstrip("/") or url.toString().partition(":")[2].lstrip("/")
        else:
            section = url.fragment()
        target = self._section_titles.get(section)
        if target is not None:
            position = self._section_positions.get(section)
            if position is not None:
                self._scroll_to_section(position)
            return
        if url.scheme() in {"http", "https", "mailto"}:
            QDesktopServices.openUrl(url)

    def _scroll_to_section(self, position: int) -> None:
        """Place the actual heading block at the top of the manual viewport."""

        def move() -> None:
            document = self.browser.document()
            block = document.findBlock(position)
            if not block.isValid():
                return
            cursor = QTextCursor(block)
            self.browser.setTextCursor(cursor)
            top = round(document.documentLayout().blockBoundingRect(block).top())
            scroll_bar = self.browser.verticalScrollBar()
            scroll_bar.setValue(
                max(scroll_bar.minimum(), min(scroll_bar.maximum(), top - 6))
            )

        move()
        # QTextDocument may finish laying out image-heavy sections after the
        # link event, so repeat once on the next event-loop turn.
        QTimer.singleShot(0, move)

    def find_next(self) -> None:
        """Select the next match, wrapping to the beginning when necessary."""
        query = self.search.text().strip()
        if not query:
            self.find_status.setText("Enter text to find")
            return
        if self.browser.find(query):
            self.find_status.setText("Match found")
            return
        cursor = self.browser.textCursor()
        cursor.movePosition(cursor.MoveOperation.Start)
        self.browser.setTextCursor(cursor)
        if self.browser.find(query):
            self.find_status.setText("Wrapped to start")
        else:
            self.find_status.setText("No matches")
